"""本人单次测试：不声明人脸通过，保留防重复状态，始终核对退出。"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from neko_account_check import check, run_arguments
import math


def meter_summary(output):
    match = re.search(r'有效里程：([0-9.]+)\s*km', output)
    return round(float(match.group(1)) * 1000) if match else None


def single_run(root, username, password, runner=subprocess.run, distance_km=2, pace_seconds=390, journal_name='single-run-2km-face0.json'):
    arguments = run_arguments(distance_km, pace_seconds)
    if journal_name not in ('single-run-2km-face0.json', 'browser-single-run.json'):
        raise ValueError('invalid_journal')
    root = Path(root)
    journal = root / journal_name
    result = {'stage': 'native_single_run', 'distance_m': round(distance_km * 1000), 'pace_seconds_per_km': pace_seconds,
              'face_verified': False, 'run_attempted': False,
              'record_uploaded': False, 'valid_credit_verified': False}
    previous = None
    if journal.exists():
        previous = json.loads(journal.read_text())
        # 只允许重试上游在提交前明确拒绝的大连默认配置；未知结果和已提交结果始终阻止。
        if previous.get('failure') != 'unconfigured_default_location' or previous.get('location_retry'):
            return {**result, 'failure': 'previous_attempt_requires_review', 'duplicate_prevented': True}
        identity_path = root / 'identity.json'
        identity = json.loads(identity_path.read_text()) if identity_path.exists() else {}
        if identity.get('city') != '哈尔滨市' or not 45.85 < identity.get('anchor_lat', 0) < 45.88 or not 126.54 < identity.get('anchor_lon', 0) < 126.58:
            return {**result, 'failure': 'location_requires_configuration', 'duplicate_prevented': True}
        result['location_retry'] = True
    query_count = 0

    def intercepted(args, **kwargs):
        nonlocal query_count
        response = runner(args, **kwargs)
        if args[1] != 'semester':
            return response
        query_count += 1
        if query_count != 1 or response.returncode != 0:
            return response
        result['valid_distance_before_m'] = meter_summary(response.stdout)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if previous else os.O_EXCL)
        with os.fdopen(os.open(journal, flags, 0o600), 'w') as handle:
            json.dump({'phase': 'attempt_reserved', 'distance_m': result['distance_m'], 'face': 0, 'prior_result': previous}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        result['run_attempted'] = True
        result['record_uploaded'] = None
        try:
            duration_minutes = math.ceil(distance_km * pace_seconds / 60)
            run_args = [args[0], *arguments, '--face', '0', '--ago', str(duration_minutes)]
            running = runner(run_args, **{**kwargs, 'timeout': 120})
            output = running.stdout + '\n' + running.stderr
            result['run_exit_code'] = running.returncode
            result['record_uploaded'] = True if '跑步提交成功 rrid=' in output else None
            result['error_codes'] = sorted(set(int(v) for v in re.findall(r'\b(?:error|err|code)\s*[=:]\s*(-?\d+)', output)))[:10]
            if '大连默认配置' in output:
                result['failure'] = 'unconfigured_default_location'
            elif running.returncode:
                result['failure'] = 'remote_or_client_rejected'
            if result['record_uploaded']:
                after = runner([args[0], 'semester'], **kwargs)
                result['valid_distance_after_m'] = meter_summary(after.stdout) if after.returncode == 0 else None
                before_m = result['valid_distance_before_m']
                after_m = result['valid_distance_after_m']
                result['valid_credit_verified'] = before_m is not None and after_m is not None and after_m - before_m >= result['distance_m']
        except subprocess.TimeoutExpired:
            result['failure'] = 'run_result_unknown_no_retry'
        finally:
            journal.write_text(json.dumps({**result, 'prior_result': previous}), encoding='utf-8')
        return response

    account = check(root, username, password, runner=intercepted)
    result['login_ok'] = account['login_ok']
    result['remote_logout'] = account['remote_logout']
    result['session_retained'] = account['session_retained']
    for field in ('login_error_codes', 'http_status_codes', 'login_diagnostic_tags', 'login_diagnostics', 'logout_failure'):
        if field in account:
            result[field] = account[field]
    if not result['run_attempted']:
        result['failure'] = account.get('failure', 'baseline_query_failed')
    result['ok'] = result['valid_credit_verified'] and result['remote_logout']
    return result


if __name__ == '__main__':
    os.umask(0o077)
    request = json.load(sys.stdin)
    print(json.dumps(single_run(request['root'], request['username'], request['password'])))
