"""Render 个人账号检测：口令访问、一次性预约、自动退出，不上传运动记录。"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

from neko_account_check import check
import personal_store as store

ROOT = Path(__file__).parent
ORIGIN = os.environ.get('RENDER_EXTERNAL_URL', 'https://access-test-vpz8.onrender.com').rstrip('/')
POOL = ThreadPoolExecutor(max_workers=1)
LOCK = threading.Lock()
ACTIVE = False
ATTEMPTS = []
READY = False
COOKIE = 'personal_test'


def settings(require_active=True):
    code = os.environ.get('ACCESS_CODE', '')
    account_hash = os.environ.get('PERSONAL_ACCOUNT_SHA256', '')
    expiry = int(os.environ.get('ACCESS_EXPIRES', '0'))
    key = os.environ.get('STATE_ENCRYPTION_KEY', '')
    if len(code) < 24 or not re.fullmatch('[a-f0-9]{64}', account_hash) or not key or (require_active and expiry <= time.time()):
        raise ValueError('private_settings_missing_or_expired')
    from cryptography.fernet import Fernet
    return code, account_hash, expiry, Fernet(key.encode())


def initialize():
    global READY
    try:
        settings()
        store.schema()
        runtime = json.loads((ROOT / 'runtime_report.json').read_text())
        READY = bool(runtime.get('runtime_verified') and runtime.get('sha256_verified')
                     and (ROOT / 'nekosportsworldtool').is_file())
    except Exception:
        READY = False


def sign(value):
    return hmac.new(settings()[0].encode(), value.encode(), hashlib.sha256).hexdigest()


def session(handler):
    try:
        cookies = SimpleCookie(handler.headers.get('Cookie', ''))
        token = cookies[COOKIE].value
        value, signature = token.rsplit('.', 1)
        expiry, nonce = value.split('.', 1)
        if not nonce or int(expiry) <= time.time() or not secrets.compare_digest(sign(value), signature):
            return None
        return hmac.new(settings()[0].encode(), ('csrf:' + nonce).encode(), hashlib.sha256).hexdigest()
    except Exception:
        return None


def send(handler, status, data, *, html=False, cookie=None):
    body = data.encode() if html else json.dumps(data, ensure_ascii=False).encode()
    handler.send_response(status)
    headers = {'Content-Type': 'text/html; charset=utf-8' if html else 'application/json',
               'Content-Length': str(len(body)), 'Cache-Control': 'no-store',
               'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
               'Referrer-Policy': 'no-referrer'}
    if cookie:
        headers['Set-Cookie'] = f'{COOKIE}={cookie}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=7200'
    for name, value in headers.items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def checkpoint(root):
    path = root / 'session.json'
    cipher = settings(require_active=False)[3].encrypt(path.read_bytes()).decode() if path.exists() else None
    store.checkpoint(cipher)
    return cipher


def execute(username, password):
    global ACTIVE
    try:
        with tempfile.TemporaryDirectory(prefix='personal-check-') as folder:
            root = Path(folder)
            shutil.copyfile(ROOT / 'nekosportsworldtool', root / 'nekosportsworldtool')
            (root / 'nekosportsworldtool').chmod(0o700)

            def runner(args, **kwargs):
                try:
                    return subprocess.run(args, **kwargs)
                finally:
                    checkpoint(root)

            report = check(root, username, password, runner=runner)
            cipher = checkpoint(root)
            store.finish(report, cipher)
    except Exception:
        # 已预约任务保留，无法确认结果时不会自动重新登录。
        try:
            current = store.load()
            store.finish({'ok': False, 'failure': 'result_unknown_no_retry',
                          'record_uploaded': False, 'remote_logout': False},
                         current['session_ciphertext'] if current else None)
        except Exception:
            pass
    finally:
        password = None
        with LOCK:
            ACTIVE = False


def revoke():
    global ACTIVE
    try:
        current = store.load()
        if not current or not current['session_ciphertext']:
            return
        with tempfile.TemporaryDirectory(prefix='personal-logout-') as folder:
            root = Path(folder)
            binary = root / 'nekosportsworldtool'
            shutil.copyfile(ROOT / 'nekosportsworldtool', binary)
            binary.chmod(0o700)
            saved = settings(require_active=False)[3].decrypt(current['session_ciphertext'].encode())
            path = root / 'session.json'
            path.write_bytes(saved)
            path.chmod(0o600)
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LANG': 'C.UTF-8'}
            subprocess.run([str(binary), 'logout'], cwd=root, env=env,
                           capture_output=True, timeout=30)
            path.write_bytes(saved)
            result = subprocess.run([str(binary), 'semester'], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=30)
            rejected = result.returncode != 0 and bool(re.search(
                r'(?:error\s*[=:]\s*401\b|HTTP\s+401\b|"error"\s*:\s*401\b)', result.stdout + result.stderr))
            report = current['report'] or {'record_uploaded': False}
            report['remote_logout'] = rejected
            report['session_retained'] = not rejected
            store.finish(report, None if rejected else current['session_ciphertext'])
    except Exception:
        pass
    finally:
        with LOCK:
            ACTIVE = False


GATE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>个人测试入口</title><body style="font:18px/1.7 system-ui;max-width:480px;margin:2rem auto;padding:1rem"><h1>个人测试入口</h1><p>先输入访问口令，此处不要输入运动账号密码。</p><form id="gate"><input id="code" type="password" aria-label="访问口令" required maxlength="128"><button>打开个人页面</button></form><p id="message" role="status"></p><script>document.getElementById('gate').onsubmit=async e=>{e.preventDefault();const i=document.getElementById('code');const code=i.value;i.value='';try{const r=await fetch('/api/access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});if(r.ok)location.reload();else document.getElementById('message').textContent='口令无效、过期或配置尚未就绪。';}catch{document.getElementById('message').textContent='连接失败，请稍后再试。';}};</script></body></html>'''


def handle(handler, method):
    global ACTIVE, ATTEMPTS
    if method == 'GET':
        if handler.path == '/':
            csrf = session(handler)
            if not csrf:
                send(handler, 200, GATE, html=True)
            else:
                page = (ROOT / 'personal_test.html').read_text(encoding='utf-8').replace('__CSRF__', csrf)
                send(handler, 200, page, html=True)
            return
        if handler.path != '/api/status':
            return send(handler, 404, {})
        if not session(handler):
            return send(handler, 401, {'failure': 'session_expired'})
        try:
            current = store.load()
            result = {'busy': ACTIVE, 'reserved': bool(current),
                      'report': current['report'] if current else None,
                      'logout_available': bool(current and current['session_ciphertext'])}
            send(handler, 200, result)
        except Exception:
            send(handler, 503, {'failure': 'database_unavailable'})
        return
    if handler.headers.get('Origin') != ORIGIN:
        return send(handler, 403, {'failure': 'invalid_origin'})
    if not READY:
        return send(handler, 503, {'failure': 'private_setup_required'})
    handler.connection.settimeout(10)
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 < length <= 4096 or handler.headers.get('Transfer-Encoding'):
            raise ValueError()
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError()
    except Exception:
        return send(handler, 400, {'failure': 'invalid_request'})
    if handler.path == '/api/access':
        with LOCK:
            ATTEMPTS = [t for t in ATTEMPTS if time.monotonic() - t < 60]
            if len(ATTEMPTS) >= 5:
                return send(handler, 429, {'failure': 'rate_limited'})
            ATTEMPTS.append(time.monotonic())
        try:
            value = payload.get('code')
            code, _, expiry, _ = settings()
            if not isinstance(value, str) or not secrets.compare_digest(value, code):
                raise ValueError()
            nonce = secrets.token_hex(16)
            value = str(min(int(time.time()) + 7200, expiry)) + '.' + nonce
            return send(handler, 200, {'ok': True}, cookie=value + '.' + sign(value))
        except Exception:
            return send(handler, 403, {'failure': 'access_denied'})
    csrf = session(handler)
    if not csrf or not secrets.compare_digest(handler.headers.get('X-CSRF-Token', ''), csrf):
        return send(handler, 403, {'failure': 'session_expired'})
    if handler.path == '/api/check':
        username, password = payload.get('username'), payload.get('password')
        try:
            allowed = settings()[1]
            if (not isinstance(username, str) or not isinstance(password, str)
                    or not 0 < len(password) <= 256 or len(username) > 128
                    or payload.get('consent') is not True
                    or not secrets.compare_digest(hashlib.sha256(username.strip().encode()).hexdigest(), allowed)):
                raise ValueError()
            with LOCK:
                if ACTIVE:
                    return send(handler, 409, {'failure': 'busy'})
                if not store.reserve():
                    return send(handler, 409, {'failure': 'already_reserved'})
                ACTIVE = True
                try:
                    POOL.submit(execute, username.strip(), password)
                except Exception:
                    ACTIVE = False
                    raise
            return send(handler, 202, {'ok': True})
        except ValueError:
            return send(handler, 400, {'failure': 'personal_account_or_consent_required'})
        except Exception:
            return send(handler, 503, {'failure': 'result_unknown_no_retry'})
    if handler.path == '/api/logout':
        with LOCK:
            if ACTIVE:
                return send(handler, 409, {'failure': 'busy'})
            ACTIVE = True
            try:
                POOL.submit(revoke)
            except Exception:
                ACTIVE = False
                return send(handler, 503, {'failure': 'logout_unavailable'})
        return send(handler, 202, {'ok': True})
    return send(handler, 404, {})
