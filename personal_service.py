"""同校多账号网页：独立浏览器状态、每日账号预约、自动退出。"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

from neko_single_run_check import single_run
from location_input import location
import personal_store as store

ROOT = Path(__file__).parent
ORIGIN = os.environ.get('RENDER_EXTERNAL_URL', 'https://access-test-vpz8.onrender.com').rstrip('/')
POOL = ThreadPoolExecutor(max_workers=1)
LOCK = threading.Lock()
ACTIVE = False
ACTIVE_KEY = None
READY = False
COOKIE = 'campus_test_v2'


def settings(require_active=True):
    from cryptography.fernet import Fernet
    key = os.environ.get('STATE_ENCRYPTION_KEY', '')
    cipher = Fernet(key.encode())
    return key, None, int(time.time()) + 604800, cipher


def scope(value):
    return hmac.new(settings(False)[0].encode(), value.encode(), hashlib.sha256).hexdigest()


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
        if not re.fullmatch('[a-f0-9]{32}', nonce) or int(expiry) <= time.time() or not secrets.compare_digest(sign(value), signature):
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
        headers['Set-Cookie'] = f'{COOKIE}={cookie}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800'
    for name, value in headers.items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def checkpoint(root, key):
    path = root / 'session.json'
    if not path.exists():
        current = store.load(key)
        return current['session_ciphertext'] if current else None
    state = {'session': path.read_text(),
             'identity': json.loads((root / 'identity.json').read_text())}
    cipher = settings(require_active=False)[3].encrypt(json.dumps(state).encode()).decode()
    store.checkpoint(key, cipher)
    return cipher


def execute(key, account_key, username, password, anchor, distance_km, pace_seconds):
    global ACTIVE, ACTIVE_KEY
    try:
        with tempfile.TemporaryDirectory(prefix='personal-check-') as folder:
            root = Path(folder)
            shutil.copyfile(ROOT / 'nekosportsworldtool', root / 'nekosportsworldtool')
            (root / 'nekosportsworldtool').chmod(0o700)
            identity = anchor.copy()
            identity['device_id'] = str(uuid.UUID(scope('device:' + account_key)[:32])).upper()
            identity['app_install_time'] = 1_760_000_000_000
            identity['mac_address'] = '02:' + ':'.join(scope('mac:' + account_key)[i:i+2] for i in range(0,10,2))
            (root / 'identity.json').write_text(json.dumps(identity), encoding='utf-8')

            def runner(args, **kwargs):
                try:
                    return subprocess.run(args, **kwargs)
                finally:
                    checkpoint(root, key)

            report = single_run(root, username, password, runner=runner, distance_km=distance_km,
                                pace_seconds=pace_seconds, journal_name='browser-single-run.json')
            cipher = None if report.get('remote_logout') else checkpoint(root, key)
            store.finish(key, report, cipher)
    except Exception:
        # 已预约任务保留，无法确认结果时不会自动重新登录。
        try:
            current = store.load(key)
            store.finish(key, {'ok': False, 'failure': 'result_unknown_no_retry',
                          'record_uploaded': None, 'remote_logout': False},
                         current['session_ciphertext'] if current else None)
        except Exception:
            pass
    finally:
        password = None
        with LOCK:
            ACTIVE = False
            ACTIVE_KEY = None


def revoke(key):
    global ACTIVE, ACTIVE_KEY
    try:
        current = store.load(key)
        if not current or not current['session_ciphertext']:
            return
        with tempfile.TemporaryDirectory(prefix='personal-logout-') as folder:
            root = Path(folder)
            binary = root / 'nekosportsworldtool'
            shutil.copyfile(ROOT / 'nekosportsworldtool', binary)
            binary.chmod(0o700)
            saved_state = json.loads(settings(require_active=False)[3].decrypt(current['session_ciphertext'].encode()))
            saved = saved_state['session'].encode()
            (root / 'identity.json').write_text(json.dumps(saved_state['identity']))
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
            store.finish(key, report, None if rejected else current['session_ciphertext'])
    except Exception:
        pass
    finally:
        with LOCK:
            ACTIVE = False
            ACTIVE_KEY = None




def handle(handler, method):
    global ACTIVE, ACTIVE_KEY
    if method == 'GET':
        if handler.path == '/':
            if not READY:
                return send(handler, 503, '服务尚未就绪，请稍后再试。', html=True)
            csrf = session(handler)
            token = None
            if not csrf:
                nonce = secrets.token_hex(16)
                value = str(int(time.time()) + 604800) + '.' + nonce
                token = value + '.' + sign(value)
                csrf = hmac.new(settings()[0].encode(), ('csrf:' + nonce).encode(), hashlib.sha256).hexdigest()
            page = (ROOT / 'personal_test.html').read_text(encoding='utf-8').replace('__CSRF__', csrf)
            return send(handler, 200, page, html=True, cookie=token)
        if handler.path != '/api/status':
            return send(handler, 404, {})
        if not session(handler):
            return send(handler, 401, {'failure': 'session_expired'})
        try:
            key = 'browser-run-v2:' + session(handler)
            current = store.load(key)
            result = {'busy': ACTIVE and ACTIVE_KEY == key, 'reserved': bool(current),
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
    csrf = session(handler)
    if not csrf or not secrets.compare_digest(handler.headers.get('X-CSRF-Token', ''), csrf):
        return send(handler, 403, {'failure': 'session_expired'})
    key = 'browser-run-v2:' + csrf
    if handler.path == '/api/run':
        username, password = payload.get('username'), payload.get('password')
        try:
            if (not isinstance(username, str) or not isinstance(password, str)
                    or not 0 < len(password) <= 256 or not re.fullmatch(r'1[3-9][0-9]{9}', username.strip())
                    or payload.get('consent') is not True):
                raise ValueError()
            anchor = location(payload.get('city'), payload.get('latitude'), payload.get('longitude'))
            try:
                distance_km = float(payload.get('distance_km'))
                cadence = float(payload.get('cadence'))
                step_length_m = float(payload.get('step_length_m'))
            except (TypeError, ValueError):
                raise ValueError('invalid_running_parameters') from None
            if (not all(math.isfinite(v) for v in (distance_km, cadence, step_length_m))
                    or not 2 <= distance_km <= 3 or not 140 <= cadence <= 200
                    or not 0.7 <= step_length_m <= 1.5):
                raise ValueError('invalid_running_parameters')
            pace_seconds = round(60000 / (cadence * step_length_m))
            if not 150 <= pace_seconds <= 600:
                raise ValueError('invalid_running_parameters')
            with LOCK:
                if ACTIVE:
                    return send(handler, 409, {'failure': 'busy'})
                account_key = 'account-run-v2:' + scope('account:' + username.strip())
                day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
                if not store.reserve(key, account_key, day):
                    return send(handler, 409, {'failure': 'already_reserved'})
                ACTIVE = True
                ACTIVE_KEY = key
                try:
                    POOL.submit(execute, key, account_key, username.strip(), password, anchor,
                                distance_km, pace_seconds)
                except Exception:
                    ACTIVE = False
                    ACTIVE_KEY = None
                    raise
            return send(handler, 202, {'ok': True})
        except ValueError as exc:
            category = ('invalid_location' if str(exc) in ('invalid_city', 'invalid_coordinate', 'location_outside_supported_area')
                        else 'invalid_running_parameters' if str(exc) == 'invalid_running_parameters'
                        else 'account_or_consent_required')
            return send(handler, 400, {'failure': category})
        except Exception:
            return send(handler, 503, {'failure': 'result_unknown_no_retry'})
    if handler.path == '/api/logout':
        with LOCK:
            if ACTIVE:
                return send(handler, 409, {'failure': 'busy'})
            ACTIVE = True
            try:
                ACTIVE_KEY = key
                POOL.submit(revoke, key)
            except Exception:
                ACTIVE = False
                ACTIVE_KEY = None
                return send(handler, 503, {'failure': 'logout_unavailable'})
        return send(handler, 202, {'ok': True})
    return send(handler, 404, {})
