"""SSH 执行器：把单次任务转发到国内 VPS 的 sport-relay。

仅当 RUNNER_MODE=ssh 且配置齐全时被 personal_service 调用；
凭据只经内存传入 stdin，不写日志、不进报告、不进异常文本。
"""
import base64
import json
import os
import time

RELAY_COMMAND = 'exec /usr/bin/python3 -B /opt/sport-relay/relay.py'


class RelayError(RuntimeError):
    """中继调用失败；调用方按结果未知处理，不自动重试。"""


def _env(name, default=''):
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def relay_config():
    host = _env('SPORT_RELAY_HOST')
    user = _env('SPORT_RELAY_USER', 'sportrelay')
    port = int(_env('SPORT_RELAY_PORT', '35877'))
    key_pem = _env('SPORT_RELAY_SSH_KEY').replace('\\n', '\n')
    token = _env('SPORT_RELAY_SHARED_TOKEN')
    hostkey_line = _env('SPORT_RELAY_HOSTKEY')
    if not host or not key_pem or not token or not hostkey_line:
        raise RelayError('relay_not_configured')
    return host, port, user, key_pem, token, hostkey_line


def _load_private_key(paramiko, key_pem):
    from io import StringIO
    try:
        return paramiko.Ed25519Key.from_private_key(StringIO(key_pem))
    except paramiko.SSHException:
        return paramiko.RSAKey.from_private_key(StringIO(key_pem))


def _load_hostkey(paramiko, hostkey_line):
    kind, _, blob = hostkey_line.partition(' ')
    material = base64.b64decode(blob.strip())
    if kind == 'ssh-ed25519':
        return paramiko.Ed25519Key(data=material)
    if kind in ('ssh-rsa', 'rsa-sha2-256', 'rsa-sha2-512'):
        return paramiko.RSAKey(data=material)
    raise RelayError('relay_hostkey_type_unsupported')


def run_job(payload, timeout=480):
    """发送一次中继任务；返回中继的 JSON 结果，失败抛 RelayError。"""
    import paramiko

    host, port, user, key_pem, token, hostkey_line = relay_config()
    body = json.dumps({**payload, 'token': token}).encode()
    key = _load_private_key(paramiko, key_pem)
    transport = paramiko.Transport((host, port))
    transport.banner_timeout = 30
    try:
        transport.connect(hostkey=_load_hostkey(paramiko, hostkey_line),
                          username=user, pkey=key)
        channel = transport.open_session(timeout=30)
        channel.settimeout(30)
        channel.exec_command(RELAY_COMMAND)
        deadline = time.monotonic() + timeout
        channel.sendall(body)
        channel.shutdown_write()
        chunks = []
        while True:
            if channel.recv_ready():
                chunks.append(channel.recv(65536))
                continue
            if channel.exit_status_ready():
                break
            if time.monotonic() > deadline:
                raise RelayError('relay_timeout')
            time.sleep(0.2)
        status = channel.recv_exit_status()
        output = b''.join(chunks)
    finally:
        transport.close()
    if status != 0:
        raise RelayError('relay_exit_' + str(status))
    try:
        result = json.loads(output.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise RelayError('relay_invalid_response') from None
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise RelayError('relay_rejected')
    return result
