"""只保存随机部署标记；报告不包含连接地址、凭据或账号信息。"""
import os
from pathlib import Path
import secrets
import ssl
from urllib.parse import urlsplit


def check_database(dsn=None, connector=None):
    dsn = os.environ.get('DATABASE_URL', '') if dsn is None else dsn
    report = {'configured': bool(dsn), 'connection_verified': False,
              'write_read_verified': False, 'restart_persistence_verified': False,
              'account_data_stored': False}
    if not dsn:
        return {**report, 'failure': 'not_configured'}
    try:
        parsed = urlsplit(dsn)
        if (parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname
                or not parsed.hostname.endswith('.neon.tech') or not parsed.password
                or set(parsed.password) == {'*'}):
            raise ValueError('invalid_connection_string')
        ca = ssl.get_default_verify_paths().cafile
        if not ca or not Path(ca).is_file():
            raise ValueError('trusted_ca_unavailable')
        if connector is None:
            import psycopg
            connector = psycopg.connect
        marker = secrets.token_hex(16)
        key = 'backend-persistence-v1'
        options = {'connect_timeout': 10, 'sslmode': 'verify-full', 'sslrootcert': ca,
                   'options': '-c statement_timeout=10000 -c lock_timeout=5000'}
        with connector(dsn, **options) as connection:
            report['connection_verified'] = True
            connection.execute('''CREATE TABLE IF NOT EXISTS access_test_persistence_probe (
                probe_key TEXT PRIMARY KEY, marker TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            connection.execute('''INSERT INTO access_test_persistence_probe (probe_key, marker)
                VALUES (%s, %s) ON CONFLICT (probe_key) DO NOTHING''', (key, marker))
        # 独立连接读取已提交数据，区分首次写入与再次启动后仍存在的标记。
        with connector(dsn, **options) as connection:
            row = connection.execute('SELECT marker FROM access_test_persistence_probe WHERE probe_key=%s', (key,)).fetchone()
        report['write_read_verified'] = bool(row and isinstance(row[0], str))
        report['restart_persistence_verified'] = bool(row and row[0] != marker)
    except Exception as exc:
        # 数据库异常文本可能含 URL 或密码；只返回异常类型。
        report['failure'] = type(exc).__name__
    return report
