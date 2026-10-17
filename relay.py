#!/usr/bin/env python3
"""sport-relay：部署在 VPS /opt/sport-relay，由 Render 经 SSH 强制命令调用。

stdin 一行 JSON：{op, token, job_key, ...}；stdout 一行脱敏 JSON。
安全边界：authorized_keys 强制命令 + 共享令牌双重校验；凭据只在内存，
不写日志、不回显二进制输出；会话状态用共享令牌派生密钥加密落盘，仅保存
未确认退出的会话供事后核对退出，确认退出或超过 7 天即删除。
"""
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet

BASE = Path('/opt/sport-relay')
STATE_DIR = BASE / 'state'
BINARY = BASE / 'nekosportsworldtool'
TOKEN_FILE = BASE / 'token'
STATE_TTL = 7 * 86400
KEY_PATTERN = re.compile(r'[a-f0-9]{64}')


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n')


def fail(reason):
    emit({'ok': False, 'failure': reason})


def read_token():
    return TOKEN_FILE.read_text().strip()


def fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(read_token().encode()).digest()))


def save_state(job_key, report, session_state):
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    blob = fernet().encrypt(json.dumps(
        {'report': report, 'session_state': session_state, 'saved_at': int(time.time())}).encode())
    fd = os.open(STATE_DIR / (job_key + '.json'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(blob)


def load_state(job_key):
    path = STATE_DIR / (job_key + '.json')
    if not path.is_file():
        return None
    try:
        saved = json.loads(fernet().decrypt(path.read_bytes(), ttl=STATE_TTL))
        return saved if isinstance(saved, dict) else None
    except Exception:
        return None


def delete_state(job_key):
    try:
        (STATE_DIR / (job_key + '.json')).unlink()
    except OSError:
        pass


def purge_states():
    now = time.time()
    if not STATE_DIR.is_dir():
        return
    for path in STATE_DIR.glob('*.json'):
        try:
            if now - path.stat().st_mtime > STATE_TTL:
                path.unlink()
        except OSError:
            pass


def op_run(payload):
    sys.path.insert(0, str(BASE))
    from neko_single_run_check import single_run
    from neko_account_check import check as check_account

    with tempfile.TemporaryDirectory(prefix='relay-') as folder:
        root = Path(folder)
        shutil.copyfile(BINARY, root / 'nekosportsworldtool')
        (root / 'nekosportsworldtool').chmod(0o700)
        (root / 'identity.json').write_text(json.dumps(payload['identity']), encoding='utf-8')

        def runner(args, **kwargs):
            return subprocess.run(args, **kwargs)

        if payload.get('diagnose_only'):
            report = check_account(root, payload['username'], payload['password'], runner=runner)
            report.update(operation='login_diagnosis', run_attempted=False, record_uploaded=False)
        else:
            report = single_run(root, payload['username'], payload['password'], runner=runner,
                                distance_km=payload['distance_km'],
                                pace_seconds=payload['pace_seconds'],
                                journal_name='browser-single-run.json')
        session_state = None
        if (root / 'session.json').exists():
            session_state = {'session': (root / 'session.json').read_text(),
                             'identity': json.loads((root / 'identity.json').read_text())}
        save_state(payload['job_key'], report, session_state)
        return {'ok': True, 'report': report, 'session_state': session_state}


def op_logout(payload):
    state = payload.get('session_state')
    if not state:
        saved = load_state(payload['job_key'])
        state = saved.get('session_state') if saved else None
    if not state:
        return {'ok': True, 'found': False}
    with tempfile.TemporaryDirectory(prefix='relay-logout-') as folder:
        root = Path(folder)
        binary = root / 'nekosportsworldtool'
        shutil.copyfile(BINARY, binary)
        binary.chmod(0o700)
        (root / 'identity.json').write_text(json.dumps(state['identity']), encoding='utf-8')
        path = root / 'session.json'
        path.write_text(state['session'], encoding='utf-8')
        path.chmod(0o600)
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LANG': 'C.UTF-8'}
        subprocess.run([str(binary), 'logout'], cwd=root, env=env,
                       capture_output=True, timeout=30)
        path.write_text(state['session'], encoding='utf-8')
        result = subprocess.run([str(binary), 'semester'], cwd=root, env=env,
                                capture_output=True, text=True, timeout=30)
        rejected = result.returncode != 0 and bool(re.search(
            r'(?:error\s*[=:]\s*401\b|HTTP\s+401\b|"error"\s*:\s*401\b)', result.stdout + result.stderr))
        if rejected:
            delete_state(payload['job_key'])
        return {'ok': True, 'found': True, 'remote_logout': rejected,
                'session_retained': not rejected}


def parse_rows(text):
    """只保留日期、距离、时长、配速；不返回记录编号或原始文本。"""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or not re.fullmatch(r'\d{2}-\d{2}', parts[0]) \
                or not re.fullmatch(r'\d{2}:\d{2}:\d{2}', parts[1]):
            continue
        try:
            distance = int(float(parts[2]))
        except ValueError:
            continue
        rows.append({'when': parts[0] + ' ' + parts[1], 'distance_m': distance,
                     'duration': parts[3], 'pace': parts[4]})
    return rows[:20]


def op_records(payload):
    """只读核对：登录 → 跑步记录列表 → 退出核对；不产生任何运动记录。"""
    with tempfile.TemporaryDirectory(prefix='relay-records-') as folder:
        root = Path(folder)
        binary = root / 'nekosportsworldtool'
        shutil.copyfile(BINARY, binary)
        binary.chmod(0o700)
        (root / 'identity.json').write_text(json.dumps(payload['identity']), encoding='utf-8')
        (root / 'config.json').write_text(json.dumps(
            {'username': '', 'password': '', 'remember': False, 'dist_min': 2, 'dist_max': 3,
             'pace_min': 360, 'pace_max': 420, 'update_check': 'off'}), encoding='utf-8')
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LANG': 'C.UTF-8'}
        report = {'op': 'records', 'login_ok': False, 'records_ok': False,
                  'remote_logout': False, 'rows': []}
        try:
            login = subprocess.run([str(binary), 'login', '--user', payload['username'],
                                    '--pass', payload['password']], cwd=root, env=env,
                                   capture_output=True, text=True, timeout=120)
            report['login_ok'] = login.returncode == 0 and (root / 'session.json').exists()
            if report['login_ok']:
                listing = subprocess.run([str(binary), 'records'], cwd=root, env=env,
                                         capture_output=True, text=True, timeout=60)
                report['records_ok'] = listing.returncode == 0
                report['rows'] = parse_rows(listing.stdout)
            else:
                report['failure'] = 'login_failed'
                report['http_status_codes'] = sorted(set(int(value) for value in re.findall(
                    r'\bHTTP\s+(\d{3})\b', login.stdout + login.stderr)))[:10]
        except subprocess.TimeoutExpired:
            report['failure'] = 'records_timeout'
        saved = (root / 'session.json').read_bytes() if (root / 'session.json').exists() else None
        try:
            subprocess.run([str(binary), 'logout'], cwd=root, env=env,
                           capture_output=True, timeout=30)
            if saved:
                (root / 'session.json').write_bytes(saved)
                verified = subprocess.run([str(binary), 'semester'], cwd=root, env=env,
                                          capture_output=True, text=True, timeout=30)
                text = verified.stdout + verified.stderr
                rejected = verified.returncode != 0 and bool(re.search(
                    r'(?:error\s*[=:]\s*401\b|HTTP\s+401\b|"error"\s*:\s*401\b)', text))
                if rejected:
                    (root / 'session.json').unlink()
                    report['remote_logout'] = True
                else:
                    report['logout_failure'] = 'token_revocation_not_confirmed'
            else:
                report['logout_failure'] = 'no_saved_session_to_verify'
        except Exception:
            if saved:
                (root / 'session.json').write_bytes(saved)
            report['logout_failure'] = 'logout_or_verification_failed'
        report['session_retained'] = (root / 'session.json').exists()
        if report['session_retained']:
            save_state(payload['job_key'], report, None)
        return report


def main():
    raw = sys.stdin.buffer.read()
    if len(raw) > 65536:
        return fail('payload_too_large')
    if not TOKEN_FILE.is_file() or not BINARY.is_file():
        return fail('relay_not_provisioned')
    try:
        payload = json.loads(raw)
    except Exception:
        return fail('invalid_payload')
    if not isinstance(payload, dict):
        return fail('invalid_payload')
    if not isinstance(payload.get('token'), str) or not hmac.compare_digest(payload['token'], read_token()):
        return fail('token_rejected')
    op = payload.get('op')
    job_key = str(payload.get('job_key') or '')
    if op in ('run', 'logout', 'records') and not KEY_PATTERN.fullmatch(job_key):
        return fail('invalid_job_key')
    if op not in ('ping', 'run', 'logout', 'records'):
        return fail('unknown_op')
    purge_states()
    if op == 'ping':
        return emit({'ok': True, 'binary': True, 'time': int(time.time())})
    if op == 'run':
        return emit(op_run(payload))
    if op == 'records':
        return emit({'ok': True, 'report': op_records(payload)})
    return emit(op_logout(payload))


if __name__ == '__main__':
    main()
