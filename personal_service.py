"""多账号网页：独立浏览器状态、账号并发预约、自动退出。"""
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
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

from neko_single_run_check import single_run
from neko_account_check import check as check_account
from location_input import location
import personal_store as store

ROOT = Path(__file__).parent
ORIGIN = os.environ.get('RENDER_EXTERNAL_URL', 'https://access-test-vpz8.onrender.com').rstrip('/')
def memory_available():
    """Read Linux memory headroom, including a smaller container limit."""
    try:
        values = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            name, _, rest = line.partition(':')
            if name in ('MemTotal', 'MemAvailable'):
                values[name] = int(rest.split()[0]) * 1024
        total, available = values['MemTotal'], values['MemAvailable']
    except (OSError, KeyError, ValueError, IndexError):
        return None
    for limit_path, usage_path in (
        ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current'),
        ('/sys/fs/cgroup/memory/memory.limit_in_bytes',
         '/sys/fs/cgroup/memory/memory.usage_in_bytes'),
    ):
        try:
            limit = int(Path(limit_path).read_text().strip())
            used = int(Path(usage_path).read_text().strip())
        except (OSError, ValueError):
            continue
        if 0 < limit < total:
            total, available = limit, min(available, max(0, limit - used))
    return total, available


def resources_available():
    memory = memory_available()
    if memory is None:
        return not sys.platform.startswith('linux')
    total, available = memory
    return available >= max(128 * 1024 * 1024, total // 8)


def start_task(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


# 本轮故障对照临时固定为单任务，旧环境变量不覆盖；恢复并发需另行发布。
MAX_ACTIVE_RUNS = 1
LOCK = threading.Lock()
ACTIVE_KEYS = set()
READY = False
COOKIE = 'campus_test_v2'
PROFILE_TTL = 30 * 86400
PROFILE_FIELDS = ('username', 'password', 'city', 'latitude', 'longitude',
                  'distance_km', 'cadence', 'step_length_m')


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
        headers['Set-Cookie'] = f'{COOKIE}={cookie}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age={PROFILE_TTL}'
    for name, value in headers.items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def load_profile(owner):
    from cryptography.fernet import InvalidToken
    encrypted = store.load_profile(owner)
    if not encrypted:
        return None
    try:
        saved = json.loads(settings()[3].decrypt(encrypted.encode(), ttl=PROFILE_TTL))
        if saved.get('owner') != owner or not isinstance(saved.get('fields'), dict):
            return None
        return saved['fields']
    except (InvalidToken, ValueError, TypeError, AttributeError, UnicodeError):
        return None


def parameters(payload, owner):
    if payload.get('use_saved') is True:
        saved = load_profile(owner)
        if not saved or (payload.get('username') and payload['username'] != saved.get('username')):
            raise ValueError('saved_settings_unavailable')
        payload = {**saved, **{key: value for key, value in payload.items()
                              if key != 'password' or value}}
    username, password = payload.get('username'), payload.get('password')
    if (not isinstance(username, str) or not isinstance(password, str)
            or not 0 < len(password) <= 256 or not re.fullmatch(r'1[3-9][0-9]{9}', username.strip())):
        raise ValueError('account_or_consent_required')
    anchor = location(payload.get('city'), payload.get('latitude'), payload.get('longitude'))
    try:
        distance_km, cadence, step_length_m = (float(payload.get(field))
                                             for field in ('distance_km', 'cadence', 'step_length_m'))
    except (TypeError, ValueError):
        raise ValueError('invalid_running_parameters') from None
    if (not all(math.isfinite(v) for v in (distance_km, cadence, step_length_m))
            or not 2 <= distance_km <= 3 or not 140 <= cadence <= 200
            or not 0.7 <= step_length_m <= 1.5):
        raise ValueError('invalid_running_parameters')
    pace_seconds = round(60000 / (cadence * step_length_m))
    if not 150 <= pace_seconds <= 600:
        raise ValueError('invalid_running_parameters')
    fields = {field: payload[field] for field in PROFILE_FIELDS}
    fields['username'] = username.strip()
    return fields, anchor, distance_km, pace_seconds


def save_profile(owner, fields):
    encrypted = settings()[3].encrypt(json.dumps({'owner': owner, 'fields': fields}).encode()).decode()
    store.save_profile(owner, encrypted)


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


def execute(key, account_key, username, password, anchor, distance_km, pace_seconds, diagnose_only=False):
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

            if diagnose_only:
                report = check_account(root, username, password, runner=runner)
                report.update(operation='login_diagnosis', run_attempted=False, record_uploaded=False)
            else:
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
            ACTIVE_KEYS.discard(key)


def revoke(key):
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
            ACTIVE_KEYS.discard(key)




def handle(handler, method):
    if method == 'GET':
        if handler.path == '/':
            if not READY:
                return send(handler, 503, '服务尚未就绪，请稍后再试。', html=True)
            csrf = session(handler)
            nonce = (SimpleCookie(handler.headers.get('Cookie', ''))[COOKIE].value.split('.')[1]
                     if csrf else secrets.token_hex(16))
            value = str(int(time.time()) + PROFILE_TTL) + '.' + nonce
            token = value + '.' + sign(value)
            csrf = hmac.new(settings()[0].encode(), ('csrf:' + nonce).encode(), hashlib.sha256).hexdigest()
            page = (ROOT / 'personal_test.html').read_text(encoding='utf-8').replace('__CSRF__', csrf)
            return send(handler, 200, page, html=True, cookie=token)
        if handler.path not in ('/api/status', '/api/profile'):
            return send(handler, 404, {})
        if not session(handler):
            return send(handler, 401, {'failure': 'session_expired'})
        try:
            if handler.path == '/api/profile':
                saved = load_profile(session(handler))
                return send(handler, 200, {'saved': bool(saved), 'fields': {
                    key: value for key, value in (saved or {}).items()
                    if key in PROFILE_FIELDS and key != 'password'}})
            key = 'browser-run-v2:' + session(handler)
            current = store.load(key)
            with LOCK:
                busy = key in ACTIVE_KEYS
            result = {'busy': busy, 'reserved': bool(current and current['phase'] != 'finished'),
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
    if handler.path in ('/api/run', '/api/profile', '/api/diagnose'):
        try:
            if handler.path == '/api/profile' and payload.get('forget') is True:
                store.save_profile(csrf, None)
                return send(handler, 200, {'saved': False})
            if handler.path in ('/api/run', '/api/diagnose') and payload.get('consent') is not True:
                raise ValueError('account_or_consent_required')
            if handler.path == '/api/profile' and payload.get('remember') is not True:
                raise ValueError('remember_consent_required')
            fields, anchor, distance_km, pace_seconds = parameters(payload, csrf)
            username, password = fields['username'], fields['password']
            if payload.get('remember') is True:
                save_profile(csrf, fields)
            if handler.path == '/api/profile':
                return send(handler, 200, {'saved': True})
            with LOCK:
                if key in ACTIVE_KEYS:
                    return send(handler, 409, {'failure': 'busy'})
                if (MAX_ACTIVE_RUNS is not None and len(ACTIVE_KEYS) >= MAX_ACTIVE_RUNS) or not resources_available():
                    return send(handler, 503, {'failure': 'capacity_unavailable'})
                account_key = 'account-run-v2:' + scope('account:' + username.strip())
                day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
                if not store.reserve(key, account_key, day):
                    return send(handler, 409, {'failure': 'already_reserved'})
                ACTIVE_KEYS.add(key)
                try:
                    task_args = (key, account_key, username.strip(), password, anchor, distance_km, pace_seconds)
                    if handler.path == '/api/diagnose':
                        task_args += (True,)
                    start_task(execute, *task_args)
                except Exception:
                    ACTIVE_KEYS.discard(key)
                    store.finish(key, {'ok': False, 'record_uploaded': False,
                                       'run_attempted': False}, None)
                    raise
            return send(handler, 202, {'ok': True})
        except ValueError as exc:
            category = ('invalid_location' if str(exc) in ('invalid_city', 'invalid_coordinate', 'location_outside_supported_area')
                        else 'invalid_running_parameters' if str(exc) == 'invalid_running_parameters'
                        else str(exc) if str(exc) in ('saved_settings_unavailable', 'remember_consent_required')
                        else 'account_or_consent_required')
            return send(handler, 400, {'failure': category})
        except Exception:
            return send(handler, 503, {'failure': 'result_unknown_no_retry'})
    if handler.path == '/api/logout':
        with LOCK:
            if key in ACTIVE_KEYS:
                return send(handler, 409, {'failure': 'busy'})
            if (MAX_ACTIVE_RUNS is not None and len(ACTIVE_KEYS) >= MAX_ACTIVE_RUNS) or not resources_available():
                return send(handler, 503, {'failure': 'capacity_unavailable'})
            ACTIVE_KEYS.add(key)
            try:
                start_task(revoke, key)
            except Exception:
                ACTIVE_KEYS.discard(key)
                return send(handler, 503, {'failure': 'logout_unavailable'})
        return send(handler, 202, {'ok': True})
    return send(handler, 404, {})
