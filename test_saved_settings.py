"""真实加密及本地数据库检查；不连接 Render 或运动世界。"""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
import personal_service as service
import personal_store as store


class LocalConnection:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.connection.rollback() if error[0] else self.connection.commit()
        self.connection.close()

    def execute(self, sql, parameters=()):
        if sql.startswith('SET LOCAL'):
            return None
        return self.connection.execute(sql.replace('%s', '?').replace('::jsonb', ''), parameters)


class SavedSettingsTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = Path(self.folder.name) / 'settings.db'
        with LocalConnection(self.database) as connection:
            connection.execute('CREATE TABLE personal_account_check '
                               '(test_key TEXT PRIMARY KEY, phase TEXT, report TEXT)')
        self.cipher = Fernet(Fernet.generate_key())
        self.fields = dict(username='13800000000', password='dummy-test-password',
                           city='哈尔滨市', latitude='45.86', longitude='126.56',
                           distance_km='2', cadence='160', step_length_m='1')
        self.patches = [
            patch.object(store, 'connect_database', side_effect=lambda: LocalConnection(self.database)),
            patch.object(service, 'settings', return_value=('test-key', None, None, self.cipher)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def request(self, path, payload=None, owner='browser-a', origin=None, csrf=None):
        body = json.dumps(payload).encode() if payload is not None else b''
        handler = SimpleNamespace(path=path, headers={
            'Origin': service.ORIGIN if origin is None else origin,
            'Content-Length': str(len(body)), 'X-CSRF-Token': owner if csrf is None else csrf,
        }, rfile=io.BytesIO(body), connection=SimpleNamespace(settimeout=lambda _: None))
        with patch.object(service, 'READY', True), \
             patch.object(service, 'session', return_value=owner), \
             patch.object(service, 'send') as send:
            service.handle(handler, 'POST' if payload is not None else 'GET')
            return send.call_args.args[1:]

    def test_persistent_encrypted_round_trip_and_no_password_in_response(self):
        self.assertEqual(self.request('/api/profile', {**self.fields, 'remember': True})[0], 200)
        # 通过独立新连接恢复，配置不依赖原连接或进程中的字典。
        self.assertEqual(service.load_profile('browser-a'), self.fields)
        with LocalConnection(self.database) as connection:
            stored = connection.execute('SELECT report FROM personal_account_check').fetchone()[0]
        for value in ('dummy-test-password', '13800000000', '45.86', '126.56'):
            self.assertNotIn(value, stored)
        status, body = self.request('/api/profile')
        self.assertEqual(status, 200)
        self.assertTrue(body['saved'])
        self.assertNotIn('password', body['fields'])
        self.assertNotIn('dummy-test-password', json.dumps(body))

    def test_saved_credentials_run_without_retyping_password(self):
        service.save_profile('browser-a', self.fields)
        service.ACTIVE_KEYS.clear()
        try:
            with patch.object(service, 'resources_available', return_value=True), \
                 patch.object(store, 'reserve', return_value=True), \
                 patch.object(service, 'start_task') as start:
                status, _ = self.request('/api/run', {'use_saved': True, 'consent': True})
            self.assertEqual(status, 202)
            self.assertEqual(start.call_args.args[3:5], ('13800000000', 'dummy-test-password'))
            self.assertEqual(start.call_args.args[-2:], (2.0, 375))
        finally:
            service.ACTIVE_KEYS.clear()

    def test_diagnosis_uses_saved_account_and_keeps_reservation_guards(self):
        service.save_profile('browser-a', self.fields)
        service.ACTIVE_KEYS.clear()
        try:
            with patch.object(service, 'resources_available', return_value=True), \
                 patch.object(store, 'reserve', return_value=True) as reserve, \
                 patch.object(service, 'start_task') as start:
                self.assertEqual(self.request('/api/diagnose', {'use_saved': True})[0], 400)
                self.assertEqual(self.request('/api/diagnose', {'use_saved': True, 'consent': True})[0], 202)
                self.assertIs(start.call_args.args[-1], True)
                self.assertEqual(start.call_count, 1)
                service.ACTIVE_KEYS.clear()
                reserve.return_value = False
                self.assertEqual(self.request('/api/diagnose', {'use_saved': True, 'consent': True})[0], 409)
                self.assertEqual(start.call_count, 1)
        finally:
            service.ACTIVE_KEYS.clear()

    def test_save_does_not_start_or_reserve_a_run(self):
        with patch.object(service, 'start_task') as start, patch.object(store, 'reserve') as reserve:
            self.assertEqual(self.request('/api/profile', {**self.fields, 'remember': True})[0], 200)
            start.assert_not_called()
            reserve.assert_not_called()

    def test_forget_leaves_task_rows_and_disables_reuse(self):
        service.save_profile('browser-a', self.fields)
        with LocalConnection(self.database) as connection:
            connection.execute("INSERT INTO personal_account_check VALUES ('account-run-v2:test', 'unknown', '{}')")
        self.assertEqual(self.request('/api/profile', {'forget': True})[0], 200)
        self.assertIsNone(service.load_profile('browser-a'))
        with LocalConnection(self.database) as connection:
            self.assertEqual(connection.execute("SELECT phase FROM personal_account_check WHERE test_key='account-run-v2:test'").fetchone()[0], 'unknown')

    def test_owner_isolation_and_ciphertext_swap_rejected(self):
        service.save_profile('browser-a', self.fields)
        self.assertIsNone(service.load_profile('browser-b'))
        store.save_profile('browser-b', store.load_profile('browser-a'))
        self.assertIsNone(service.load_profile('browser-b'))

    def test_expired_and_tampered_settings_are_not_used(self):
        clear = json.dumps({'owner': 'browser-a', 'fields': self.fields}).encode()
        expired = self.cipher.encrypt_at_time(clear, int(time.time()) - service.PROFILE_TTL - 1)
        store.save_profile('browser-a', expired.decode())
        self.assertIsNone(service.load_profile('browser-a'))
        store.save_profile('browser-a', 'invalid-encrypted-data')
        self.assertIsNone(service.load_profile('browser-a'))

    def test_other_account_cannot_reuse_saved_password(self):
        service.save_profile('browser-a', self.fields)
        status, body = self.request('/api/run', {'use_saved': True, 'consent': True, 'username': '13900000000'})
        self.assertEqual((status, body['failure']), (400, 'saved_settings_unavailable'))

    def test_origin_csrf_consent_and_location_remain_required(self):
        payload = {**self.fields, 'remember': True}
        self.assertEqual(self.request('/api/profile', payload, origin='https://other.example')[0], 403)
        self.assertEqual(self.request('/api/profile', payload, csrf='other-browser')[0], 403)
        self.assertEqual(self.request('/api/profile', self.fields)[0], 400)
        self.assertEqual(self.request('/api/profile', {**payload, 'latitude': 'bad'})[0], 400)
        self.assertEqual(self.request('/api/run', payload)[0], 400)

    def test_home_renews_cookie_without_changing_profile_owner(self):
        nonce = 'a' * 32
        old_value = str(int(time.time()) + 60) + '.' + nonce
        handler = SimpleNamespace(path='/', headers={'Cookie': service.COOKIE + '=' + old_value + '.' + service.sign(old_value)})
        owner = service.session(handler)
        with patch.object(service, 'READY', True), patch.object(service, 'send') as send:
            service.handle(handler, 'GET')
        cookie = send.call_args.kwargs['cookie']
        handler.headers['Cookie'] = service.COOKIE + '=' + cookie
        self.assertEqual(service.session(handler), owner)
        self.assertGreater(int(cookie.split('.')[0]), int(old_value.split('.')[0]))


if __name__ == '__main__':
    unittest.main()
