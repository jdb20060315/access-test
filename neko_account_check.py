"""原作者 CLI 的一次性连接检查；输出脱敏，退出后核对旧 Token。"""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


def run_arguments(distance_km=2, pace_seconds=390):
    # ponytail: 不改上游二进制；调用时明确覆盖距离，拒绝回退到 1–1.5 km 默认值。
    if type(distance_km) not in (int, float) or not math.isfinite(distance_km) or not 2 <= distance_km <= 3:
        raise ValueError('distance_out_of_range')
    if type(pace_seconds) not in (int, float) or not math.isfinite(pace_seconds) or not 150 <= pace_seconds <= 600:
        raise ValueError('pace_out_of_range')
    return ['run', '--dist', str(distance_km), '--pace', str(pace_seconds)]


def login_diagnostics(output):
    """只保留固定阶段和状态码，不复制响应正文、URL 或凭据。"""
    progress = []
    failures = []
    markers = (
        ("check_failed", "[gee] checkGeeUse 失败"),
        ("captcha_started", "[login] 开始 GT4 滑块验证"),
        ("captcha_passed", "[login] GT4 通过"),
        ("validation_passed", "[geevalidate] 验证通过"),
        ("direct_login", "[login] 免验证码路径：直接登录"),
    )
    for line in output.splitlines():
        line = line.strip()
        for label, marker in markers:
            if line.startswith(marker) and label not in progress:
                progress.append(label)
        codes = list(re.finditer(r"\bHTTP\s+(\d{3})\b", line))
        if not codes:
            continue
        source = "unknown"
        if line.startswith("[gee] checkGeeUse 失败"):
            source = "gee_check"
        elif re.match(r"^(?:×\s*)?\[gt4\]\s*第\s*\d+\s*次失败:", line):
            source = "captcha"
        elif "GT4 连续 3 次失败:" in line:
            source = "captcha"
        for match in codes:
            event = {"stage": source, "status": int(match.group(1))}
            # ponytail: 只识别明确格式；截断或未知正文不猜测厂商和拦截原因。
            body = line[match.end():].lstrip(": ")
            if re.match(r"<(?:!doctype\s+html\b|html\b|head\b|body\b)", body, re.I):
                event["response_kind"] = "html"
            elif body.startswith(("{", "[")):
                try:
                    value, _ = json.JSONDecoder().raw_decode(body)
                    if isinstance(value, (dict, list)):
                        event["response_kind"] = "json"
                except ValueError:
                    pass
            if event not in failures and len(failures) < 10:
                failures.append(event)
    return {"progress": progress, "http_failures": failures}


def check(root, username, password, runner=subprocess.run):
    root = Path(root)
    binary = root / 'nekosportsworldtool'
    if not binary.is_file():
        raise ValueError('binary_unavailable')
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        raise ValueError('missing_credentials')
    session_path = root / 'session.json'
    if session_path.exists():
        raise ValueError('existing_session_requires_logout')
    config_path = root / 'config.json'
    if config_path.exists():
        existing = json.loads(config_path.read_text())
        if existing.get('remember') or existing.get('username') or existing.get('password'):
            raise ValueError('existing_config_requires_review')
    root.chmod(0o700)
    settings = {'username': '', 'password': '', 'remember': False,
                'dist_min': 2, 'dist_max': 3, 'pace_min': 360, 'pace_max': 420,
                'update_check': 'off'}
    with os.fdopen(os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        json.dump(settings, handle)
    report = {'stage': 'native_account_check', 'login_ok': False, 'query_ok': False,
              'remote_logout': False, 'record_uploaded': False,
              'default_distance_km': 2, 'allowed_distance_km': [2, 3]}
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LANG': 'C.UTF-8'}

    def call(arguments, timeout):
        # 上游登录会输出 Token；仅在内存捕获，不写日志或转发到页面。
        return runner([str(binary), *arguments], cwd=root, env=env,
                      capture_output=True, text=True, timeout=timeout)

    try:
        result = call(['login', '--user', username, '--pass', password], 120)
        report['login_exit_code'] = result.returncode
        report['login_ok'] = result.returncode == 0 and session_path.exists()
        if report['login_ok']:
            report['query_ok'] = call(['semester'], 45).returncode == 0
        else:
            report['failure'] = 'login_failed'
            diagnostic = result.stdout + '\n' + result.stderr
            report['login_diagnostics'] = login_diagnostics(diagnostic)
            report['login_error_codes'] = sorted(set(int(value) for value in re.findall(
                r'\b(?:error|err|code)\s*[=:]\s*(-?\d+)\b', diagnostic, re.I)))[:10]
            report['http_status_codes'] = sorted(set(int(value) for value in re.findall(
                r'\bHTTP\s+(\d{3})\b', diagnostic)))[:10]
            categories = {'captcha': ('GT4', 'captcha', '验证码'),
                          'decrypt': ('解密失败', 'decrypt'),
                          'signature': ('签名', 'signature'),
                          'network': ('timeout', 'timed out', 'connection', 'connect', 'dns', 'tls', 'SSL'),
                          'panic': ('panicked', 'panic'),
                          'device_risk': ('10121', '设备风险')}
            report['login_diagnostic_tags'] = [name for name, markers in categories.items()
                if any(marker.casefold() in diagnostic.casefold() for marker in markers)]
    except subprocess.TimeoutExpired:
        report['failure'] = 'account_check_timeout'
    except Exception:
        report['failure'] = 'account_check_failed'
    finally:
        # 必须保留旧 Token 才能验证远端注销；CLI 的退出码本身不可信。
        saved = session_path.read_bytes() if session_path.exists() else None
        try:
            call(['logout'], 30)
            if saved:
                with os.fdopen(os.open(session_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as handle:
                    handle.write(saved)
                verified = call(['semester'], 30)
                text = verified.stdout + '\n' + verified.stderr
                rejected = verified.returncode != 0 and re.search(
                    r'(?:error\s*[=:]\s*401\b|HTTP\s+401\b|"error"\s*:\s*401\b)', text)
                if rejected:
                    session_path.unlink()
                    report['remote_logout'] = True
                else:
                    report['logout_failure'] = 'token_revocation_not_confirmed'
            else:
                report['logout_failure'] = 'no_saved_session_to_verify'
        except Exception:
            if saved:
                with os.fdopen(os.open(session_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as handle:
                    handle.write(saved)
            report['logout_failure'] = 'logout_or_verification_failed'
        report['session_retained'] = session_path.exists()
    report['ok'] = report['login_ok'] and report['query_ok'] and report['remote_logout']
    return report


if __name__ == '__main__':
    os.umask(0o077)
    payload = json.load(sys.stdin)
    result = check(payload['root'], payload['username'], payload['password'])
    print(json.dumps(result))
