// Local form checks: all HTTP responses and browser elements are faked.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, 'personal_test.html'), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1];

async function page(response, status = { busy: false, reserved: false }, profile = { saved: false }) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      id, value: '', textContent: '', checked: false, listeners: {},
      addEventListener(name, callback) { this.listeners[name] = callback; },
      setAttribute() {},
      before(other) { elements.set(other.id, other); },
    });
    return elements.get(id);
  };
  let poll;
  let sent;
  const context = vm.createContext({
    document: {
      getElementById: element,
      createElement: () => ({ textContent: '', setAttribute() {} }),
      querySelector: () => ({ textContent: '密码' }),
    },
    setInterval(callback) { poll = callback; },
    async fetch(url, options) {
      if (url === '/api/profile') {
        if (options?.method === 'POST') {
          sent = JSON.parse(options.body);
          return { ok: true, json: async () => ({ saved: !sent.forget }) };
        }
        return { ok: true, json: async () => profile };
      }
      if (url === '/api/status') return {
        ok: true,
        json: async () => status,
      };
      assert.equal(url, '/api/run');
      sent = JSON.parse(options.body);
      if (response instanceof Error) throw response;
      return {
        ok: response.status >= 200 && response.status < 300,
        status: response.status,
        json: async () => response.body,
      };
    },
  });
  vm.runInContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  element('password').value = 'dummy-local-password';
  return {
    element, poll: () => poll(),
    submit: () => element('form').onsubmit({ preventDefault() {} }),
    sent: () => sent,
  };
}

for (const response of [
  { status: 400, body: { failure: 'invalid_running_parameters' } },
  { status: 403, body: { failure: 'session_expired' } },
  { status: 503, body: { failure: 'capacity_unavailable' } },
]) test(`rejected request ${response.body.failure} keeps its error and password`, async () => {
  const browser = await page(response);
  await browser.submit();
  const message = browser.element('feedback').textContent;
  assert.ok(message.length > 0);
  assert.equal(browser.sent().password, 'dummy-local-password');
  assert.equal(browser.element('password').value, 'dummy-local-password');
  await browser.poll();
  await browser.poll();
  assert.equal(browser.element('feedback').textContent, message);
});

test('accepted request clears password and previous error', async () => {
  const browser = await page({ status: 202, body: {} });
  browser.element('feedback').textContent = 'previous error';
  await browser.submit();
  assert.equal(browser.element('password').value, '');
  assert.equal(browser.element('feedback').textContent, '');
  assert.match(browser.element('status').textContent, /已接受/);
});

test('network interruption clears password and keeps the uncertainty notice', async () => {
  const browser = await page(new Error('local simulated interruption'));
  await browser.submit();
  assert.equal(browser.element('password').value, '');
  assert.match(browser.element('feedback').textContent, /连接中断/);
  await browser.poll();
  assert.match(browser.element('feedback').textContent, /连接中断/);
});

test('native validation explains the focused field without clearing password', async () => {
  const browser = await page({ status: 202, body: {} });
  browser.element('form').listeners.invalid({ target: {
    id: 'password', validationMessage: '请填写此字段。',
  } });
  assert.match(browser.element('feedback').textContent, /密码/);
  await browser.poll();
  assert.match(browser.element('feedback').textContent, /请填写此字段/);
  assert.equal(browser.sent(), undefined);
});

test('login result displays only recognized diagnostics and numeric codes', async () => {
  const browser = await page({}, { busy: false, reserved: false, report: {
    record_uploaded: false, failure: 'login_failed',
    login_diagnostic_tags: ['captcha', 'network', 'private raw output', '__proto__'],
    login_error_codes: [10121, 'private raw output'], http_status_codes: [403],
  } });
  const result = browser.element('result').textContent;
  assert.match(result, /验证码/);
  assert.match(result, /网络连接错误/);
  assert.match(result, /10121/);
  assert.match(result, /HTTP 状态：403/);
  assert.doesNotMatch(result, /private raw output|__proto__/);
});

test('unclassified login failure does not claim the password is wrong', async () => {
  const browser = await page({}, { report: {
    record_uploaded: false, failure: 'login_failed',
  } });
  assert.match(browser.element('result').textContent, /不能据此判断密码错误/);
});

test('restored settings refill fields and reuse password without exposing it', async () => {
  const browser = await page({ status: 202, body: {} }, {}, { saved: true, fields: {
    username: '13800000000', city: '哈尔滨市', latitude: '45.86', longitude: '126.56',
    distance_km: '2', cadence: '160', step_length_m: '1',
  } });
  browser.element('password').value = '';
  assert.equal(browser.element('latitude').value, '45.86');
  assert.equal(browser.element('cadence').value, '160');
  assert.equal(browser.element('password').required, false);
  await browser.submit();
  assert.equal(browser.sent().password, '');
  assert.equal(browser.sent().use_saved, true);
  browser.element('username').value = '13900000000';
  browser.element('username').listeners.input();
  assert.equal(browser.element('password').required, true);
});

test('saving settings is separate from submitting a record', async () => {
  const browser = await page({});
  browser.element('username').value = '13800000000';
  browser.element('remember').checked = true;
  await browser.element('saveSettings').onclick();
  assert.equal(browser.sent().remember, true);
  assert.equal(browser.element('password').value, '');
  assert.match(browser.element('savedStatus').textContent, /没有提交运动记录/);
});

test('forgetting settings restores the password requirement', async () => {
  const browser = await page({}, {}, { saved: true, fields: { username: '13800000000' } });
  await browser.element('forgetSettings').onclick();
  assert.equal(browser.sent().forget, true);
  assert.equal(browser.element('password').required, true);
  assert.equal(browser.element('remember').checked, false);
  assert.equal(browser.element('forgetSettings').hidden, true);
});
