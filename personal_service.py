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
# 公网来源校验：自托管可用逗号分隔多个 Origin，Render 用 RENDER_EXTERNAL_URL。
def allowed_origins():
    configured = (os.environ.get('PUBLIC_ORIGIN')
                  or os.environ.get('RENDER_EXTERNAL_URL')
                  or 'https://access-test-vpz8.onrender.com')
    return {item.strip().rstrip('/') for item in configured.split(',') if item.strip()}

ORIGINS = allowed_origins()
ORIGIN = next(iter(ORIGINS)) if ORIGINS else 'https://access-test-vpz8.onrender.com'
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


def configured_concurrency():
    """并发上限：默认 2；只接受 1-8 的整数，其它值保守退回 1（/health 可见）。"""
    raw = os.environ.get('MAX_ACTIVE_RUNS', '2').strip()
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= 8 else 1


# 排障期结束：单任务限制解除为可配置，仍受内存水位（resources_available）保护。
MAX_ACTIVE_RUNS = configured_concurrency()
# 执行器位置：local=本机二进制（自托管或 Render 现状）；ssh=转发国内 VPS 的 sport-relay。
RUNNER_MODE = os.environ.get('RUNNER_MODE', 'local').strip().lower() or 'local'
LOCK = threading.Lock()
ACTIVE_KEYS = set()
READY = False
COOKIE = 'campus_test_v2'
PROFILE_TTL = 30 * 86400
PROFILE_FIELDS = ('username', 'password', 'school_name', 'latitude', 'longitude',
                  'distance_km', 'run_minutes')


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
        binary_ready = (ROOT / 'nekosportsworldtool').is_file()
        READY = bool(runtime.get('runtime_verified') and runtime.get('sha256_verified')
                     and (binary_ready or RUNNER_MODE == 'ssh'))
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
        proto = (handler.headers.get('X-Forwarded-Proto') or 'https').lower()
        secure_flag = '; Secure' if proto == 'https' else ''
        headers['Set-Cookie'] = f'{COOKIE}={cookie}{secure_flag}; HttpOnly; SameSite=Lax; Path=/; Max-Age={PROFILE_TTL}'
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
    school_name = str(payload.get('school_name') or payload.get('city') or '').strip()
    if not school_name or len(school_name) < 2:
        raise ValueError('account_or_consent_required')
    anchor = location(school_name, payload.get('latitude'), payload.get('longitude'))
    try:
        distance_km = float(payload.get('distance_km') or 2.2)
        run_minutes = float(payload.get('run_minutes') or 15)
    except (TypeError, ValueError):
        raise ValueError('invalid_running_parameters') from None
    if (not all(math.isfinite(v) for v in (distance_km, run_minutes))
            or not 2 <= distance_km <= 3 or not 8 <= run_minutes <= 45):
        raise ValueError('invalid_running_parameters')
    pace_seconds = round(run_minutes * 60 / distance_km)
    if not 150 <= pace_seconds <= 600:
        raise ValueError('invalid_running_parameters')
    defaults = {'distance_km': '2.2', 'run_minutes': '15'}
    fields = {field: payload.get(field, defaults.get(field, '')) for field in PROFILE_FIELDS}
    fields['username'] = username.strip()
    fields['school_name'] = school_name
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
        identity = anchor.copy()
        identity['platform'] = 'ios'
        identity['device_name'] = 'iPhone'
        identity['os_version'] = '26.5.2'
        identity['idfa'] = ''
        identity['manufacturer'] = ''
        identity['device_id'] = str(uuid.UUID(scope('device:' + account_key)[:32])).upper()
        identity['app_install_time'] = 1_760_000_000_000
        identity['mac_address'] = '02:' + ':'.join(scope('mac:' + account_key)[i:i+2] for i in range(0,10,2))
        if RUNNER_MODE == 'ssh':
            from runner_remote import run_job
            result = run_job({'op': 'run', 'job_key': key.split(':', 1)[1], 'username': username,
                              'password': password, 'anchor': anchor, 'identity': identity,
                              'distance_km': distance_km, 'pace_seconds': pace_seconds,
                              'diagnose_only': diagnose_only}, timeout=480)
            report = result['report']
            cipher = None
            if not report.get('remote_logout') and result.get('session_state'):
                cipher = settings(require_active=False)[3].encrypt(
                    json.dumps(result['session_state']).encode()).decode()
            store.finish(key, report, cipher)
            return
        with tempfile.TemporaryDirectory(prefix='personal-check-') as folder:
            root = Path(folder)
            shutil.copyfile(ROOT / 'nekosportsworldtool', root / 'nekosportsworldtool')
            (root / 'nekosportsworldtool').chmod(0o700)
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
        if not current or (not current['session_ciphertext'] and RUNNER_MODE != 'ssh'):
            return
        if RUNNER_MODE == 'ssh':
            from runner_remote import run_job
            saved_state = None
            if current['session_ciphertext']:
                saved_state = json.loads(settings(require_active=False)[3]
                                         .decrypt(current['session_ciphertext'].encode()))
            result = run_job({'op': 'logout', 'job_key': key.split(':', 1)[1],
                              'session_state': saved_state}, timeout=150)
            if not result.get('found'):
                return
            rejected = result.get('remote_logout') is True
            report = current['report'] or {'record_uploaded': False}
            report['remote_logout'] = rejected
            report['session_retained'] = not rejected
            store.finish(key, report, None if rejected else current['session_ciphertext'])
            return
        if not current['session_ciphertext']:
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




def fetch_school_points(username, password, anchor):
    account_key = 'account-run-v2:' + scope('account:' + username.strip())
    identity = anchor.copy()
    identity['platform'] = 'ios'
    identity['device_name'] = 'iPhone'
    identity['os_version'] = '26.5.2'
    identity['idfa'] = ''
    identity['manufacturer'] = ''
    identity['device_id'] = str(uuid.UUID(scope('device:' + account_key)[:32])).upper()
    identity['app_install_time'] = 1_760_000_000_000
    identity['mac_address'] = '02:' + ':'.join(scope('mac:' + account_key)[i:i+2] for i in range(0, 10, 2))

    with tempfile.TemporaryDirectory(prefix='personal-pts-') as folder:
        root = Path(folder)
        shutil.copyfile(ROOT / 'nekosportsworldtool', root / 'nekosportsworldtool')
        (root / 'nekosportsworldtool').chmod(0o700)
        (root / 'identity.json').write_text(json.dumps(identity), encoding='utf-8')
        (root / 'config.json').write_text(json.dumps({
            'username': '', 'password': '', 'remember': False,
            'dist_min': 2, 'dist_max': 3, 'pace_min': 360, 'pace_max': 420,
            'update_check': 'off'
        }), encoding='utf-8')
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LANG': 'C.UTF-8'}
        binary = root / 'nekosportsworldtool'

        login = subprocess.run([str(binary), 'login', '--user', username, '--pass', password],
                               cwd=root, env=env, capture_output=True, text=True, timeout=60)
        if login.returncode != 0:
            err = login.stdout + '\n' + login.stderr
            if '10121' in err or '设备' in err:
                raise ValueError('device_conflict')
            raise ValueError('login_failed')

        try:
            subprocess.run([str(binary), 'obs-sample', '--dist', '2.0'],
                           cwd=root, env=env, capture_output=True, text=True, timeout=60)
            cache_file = root / 'points_cache.json'
            if not cache_file.exists():
                raise ValueError('points_unavailable')
            doc = json.loads(cache_file.read_text(encoding='utf-8'))
            pts = doc.get('points') or []
            results = []
            for p in pts:
                results.append({
                    'name': p.get('pointName') or '打卡点',
                    'lat': p.get('glat') or p.get('lat'),
                    'lng': p.get('glon') or p.get('lon')
                })
            return results
        finally:
            subprocess.run([str(binary), 'logout'], cwd=root, env=env, capture_output=True, timeout=30)


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
                      'logout_available': bool(current and (current['session_ciphertext'] or RUNNER_MODE == 'ssh'))}
            send(handler, 200, result)
        except Exception:
            send(handler, 503, {'failure': 'database_unavailable'})
        return
    origin = (handler.headers.get('Origin') or '').rstrip('/')
    if origin not in ORIGINS and origin != ORIGIN:
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
    if handler.path == '/api/points':
        try:
            fields, anchor, _, _ = parameters(payload, csrf)
            pts = fetch_school_points(fields['username'], fields['password'], anchor)
            return send(handler, 200, {'ok': True, 'points': pts})
        except ValueError as exc:
            cat = ('invalid_location' if str(exc) in ('invalid_city', 'invalid_coordinate', 'location_outside_supported_area')
                   else str(exc) if str(exc) in ('device_conflict', 'login_failed', 'points_unavailable')
                   else 'account_or_consent_required')
            return send(handler, 400, {'ok': False, 'failure': cat})
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return send(handler, 500, {'ok': False, 'failure': 'points_fetch_error', 'detail': str(exc)})
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
