"""个人单次账号检测的持久化状态；不存储用户名和密码。"""
import json
from check_database import connect_database

KEY = 'personal-account-check-v1'


def schema():
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        c.execute('''CREATE TABLE IF NOT EXISTS personal_account_check (
            test_key TEXT PRIMARY KEY, phase TEXT NOT NULL,
            report JSONB, session_ciphertext TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def reserve():
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        row = c.execute('''INSERT INTO personal_account_check (test_key, phase)
            VALUES (%s, 'reserved') ON CONFLICT (test_key) DO NOTHING
            RETURNING test_key''', (KEY,)).fetchone()
    return bool(row)


def load():
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        row = c.execute('SELECT phase, report, session_ciphertext FROM personal_account_check WHERE test_key=%s', (KEY,)).fetchone()
    return {'phase': row[0], 'report': row[1], 'session_ciphertext': row[2]} if row else None


def checkpoint(ciphertext):
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        c.execute('UPDATE personal_account_check SET session_ciphertext=%s WHERE test_key=%s', (ciphertext, KEY))


def finish(report, ciphertext):
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        c.execute('''UPDATE personal_account_check SET phase='finished', report=%s::jsonb,
            session_ciphertext=%s WHERE test_key=%s''', (json.dumps(report), ciphertext, KEY))
