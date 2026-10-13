"""浏览器独立结果；每日预约和未退出会话锁防止重复提交。"""
import json
from check_database import connect_database


def schema():
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        c.execute("""CREATE TABLE IF NOT EXISTS personal_account_check (
            test_key TEXT PRIMARY KEY, phase TEXT NOT NULL,
            report JSONB, session_ciphertext TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")


def reserve(key, account_key, day):
    # 浏览器和账号任一冲突均不启动登录；两个预约在同一事务中提交。
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        c.execute("SET LOCAL lock_timeout = '5s'")
        row = c.execute("""INSERT INTO personal_account_check (test_key, phase, report)
            VALUES (%s, 'reserved', %s::jsonb)
            ON CONFLICT (test_key) DO UPDATE SET phase='reserved', report=EXCLUDED.report
            WHERE personal_account_check.phase='finished'
              AND personal_account_check.session_ciphertext IS NULL
              AND personal_account_check.report->>'day' <> %s
            RETURNING test_key""", (account_key, json.dumps({'day': day}), day)).fetchone()
        if not row:
            return False
        row = c.execute("""INSERT INTO personal_account_check (test_key, phase, report)
            VALUES (%s, 'reserved', %s::jsonb) ON CONFLICT (test_key) DO NOTHING
            RETURNING test_key""", (key, json.dumps({'account_key': account_key, 'day': day}))).fetchone()
        if not row:
            c.rollback()
            return False
    return True


def load(key):
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        row = c.execute('SELECT phase, report, session_ciphertext FROM personal_account_check WHERE test_key=%s', (key,)).fetchone()
    if not row:
        return None
    metadata = row[1] or {}
    return {'phase': row[0], 'report': metadata.get('result'),
            'account_key': metadata.get('account_key'), 'day': metadata.get('day'),
            'session_ciphertext': row[2]}


def checkpoint(key, ciphertext):
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        c.execute("""UPDATE personal_account_check SET session_ciphertext=%s
            WHERE test_key=%s OR test_key=(SELECT report->>'account_key'
            FROM personal_account_check WHERE test_key=%s)""", (ciphertext, key, key))


def finish(key, report, ciphertext):
    phase = 'unknown' if report.get('record_uploaded') is None else 'finished'
    with connect_database() as c:
        c.execute("SET LOCAL statement_timeout = '10s'")
        c.execute("""UPDATE personal_account_check SET phase=%s,
            report=jsonb_set(report, '{result}', %s::jsonb), session_ciphertext=%s
            WHERE test_key=%s OR test_key=(SELECT report->>'account_key'
            FROM personal_account_check WHERE test_key=%s)""", (phase, json.dumps(report), ciphertext, key, key))
