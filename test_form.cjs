// Local form checks: all HTTP responses and browser elements are faked.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, 'personal_test.html'), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1];

async function page(response) {
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
      if (url === '/api/status') return {
        ok: true,
        json: async () => ({ busy: false, reserved: false }),
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
