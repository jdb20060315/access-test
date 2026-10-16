"""SSH 执行器与 ssh 模式接线检查；不连接 Render、VPS 或运动世界。"""
import json
import os
import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet

import personal_service as service
import runner_remote


class FakeChannel:
    """脚本化 SSH 通道：记录输入，按剧本产出输出与退出码。"""

    def __init__(self, script):
        self.script = script

    def settimeout(self, value):
        self.timeout = value

    def exec_command(self, command):
        self.script['command'] = command

    def sendall(self, body):
        self.script['sent'] = body
        handler = self.script.get('handler')
        if handler:
            handler(body, self)

    def shutdown_write(self):
        pass

    def recv_ready(self):
        return bool(self.script.get('pending'))

    def recv(self, size):
        pending = self.script.get('pending') or b''
        chunk, self.script['pending'] = pending[:size], pending[size:]
        return chunk

    def exit_status_ready(self):
        return self.script.get('exit_ready', True)

    def recv_exit_status(self):
        return self.script.get('exit_status', 0)


class FakeTransport:
    scripted = {}
    connect_calls = []

    def __init__(self, address):
        self.address = address

    def connect(self, hostkey=None, username=None, pkey=None):
        FakeTransport.connect_calls.append({'hostkey': hostkey, 'username': username})

    def open_session(self, timeout=0):
        return FakeChannel(FakeTransport.scripted)

    def close(self):
        pass


class ParamikoStub:
    SSHException = type('SSHException', (Exception,), {})
    Transport = FakeTransport

    class Ed25519Key:
        def __init__(self, data=None):
            pass

        @classmethod
        def from_private_key(cls, file_obj):
            if 'WRONG' in file_obj.read():
                raise ParamikoStub.SSHException('bad private')
            return cls()

    RSAKey = Ed25519Key


def relay_reply(body, channel):
    payload = json.loads(body)
    FakeTransport.scripted['received'] = payload
    if payload.get('token') != 'shared-token' or payload.get('op') != 'ping':
        channel.script['pending'] = json.dumps({'ok': False, 'failure': 'token_rejected'}).encode()
    else:
        channel.script['pending'] = json.dumps({'ok': True, 'binary': True}).encode()


def configure_env():
    return {'SPORT_RELAY_HOST': 'vps.example', 'SPORT_RELAY_PORT': '35877',
            'SPORT_RELAY_USER': 'sportrelay', 'SPORT_RELAY_SSH_KEY': 'KEYLINE',
            'SPORT_RELAY_SHARED_TOKEN': 'shared-token',
            'SPORT_RELAY_HOSTKEY': 'ssh-ed25519 AAAA'}


class RelayJobTest(unittest.TestCase):
    def setUp(self):
        FakeTransport.scripted = {'handler': relay_reply}
        FakeTransport.connect_calls = []
        self.env_patch = patch.dict('os.environ', configure_env())
        self.env_patch.start()
        self.paramiko_patch = patch.dict('sys.modules', {'paramiko': ParamikoStub})
        self.paramiko_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.paramiko_patch.stop()

    def test_token_injected_and_response_parsed(self):
        result = runner_remote.run_job({'op': 'ping'})
        self.assertTrue(result['ok'])
        self.assertEqual(FakeTransport.scripted['received']['token'], 'shared-token')
        self.assertEqual(FakeTransport.connect_calls[0]['username'], 'sportrelay')
        self.assertIsNotNone(FakeTransport.connect_calls[0]['hostkey'])

    def test_nonzero_exit_raises(self):
        FakeTransport.scripted['exit_status'] = 255
        with self.assertRaisesRegex(runner_remote.RelayError, 'relay_exit_255'):
            runner_remote.run_job({'op': 'ping'})

    def test_invalid_output_raises(self):
        def bad_output(body, channel):
            channel.script['pending'] = b'not-json'
        FakeTransport.scripted['handler'] = bad_output
        with self.assertRaisesRegex(runner_remote.RelayError, 'relay_invalid_response'):
            runner_remote.run_job({'op': 'ping'})

    def test_rejected_result_raises(self):
        def rejected(body, channel):
            channel.script['pending'] = json.dumps({'ok': False, 'failure': 'x'}).encode()
        FakeTransport.scripted['handler'] = rejected
        with self.assertRaisesRegex(runner_remote.RelayError, 'relay_rejected'):
            runner_remote.run_job({'op': 'ping'})

    def test_timeout_raises_without_retry(self):
        FakeTransport.scripted = {'handler': lambda body, channel: None, 'exit_ready': False}
        with self.assertRaisesRegex(runner_remote.RelayError, 'relay_timeout'):
            runner_remote.run_job({'op': 'run'}, timeout=0.4)

    def test_missing_configuration_fails_closed(self):
        with patch.dict('os.environ', {'SPORT_RELAY_HOST': 'vps.example'}, clear=False):
            for name in ('SPORT_RELAY_SSH_KEY', 'SPORT_RELAY_SHARED_TOKEN', 'SPORT_RELAY_HOSTKEY'):
                os.environ.pop(name, None)
            with self.assertRaisesRegex(runner_remote.RelayError, 'relay_not_configured'):
                runner_remote.run_job({'op': 'ping'})

    def test_unsupported_hostkey_type(self):
        with patch.dict('os.environ', {**configure_env(), 'SPORT_RELAY_HOSTKEY': 'ssh-dss AAAA'}):
            with self.assertRaisesRegex(runner_remote.RelayError, 'relay_hostkey_type_unsupported'):
                runner_remote.run_job({'op': 'ping'})


class SshModeWiringTest(unittest.TestCase):
    def setUp(self):
        self.cipher = Fernet(Fernet.generate_key())
        self.patches = [
            patch.object(service, 'settings', return_value=('test-key', None, None, self.cipher)),
            patch.object(service, 'RUNNER_MODE', 'ssh'),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    @staticmethod
    def store_stub(load_result=None):
        calls = []
        fake = SimpleNamespace(load=lambda key: load_result,
                               finish=lambda *arguments: calls.append(arguments),
                               reserve=lambda *a: True, checkpoint=lambda *a: None,
                               save_profile=lambda *a: None, load_profile=lambda owner: None,
                               schema=lambda: None)
        return fake, calls

    def test_execute_finishes_with_encrypted_session_when_logout_unconfirmed(self):
        store, calls = self.store_stub()
        report = {'ok': False, 'record_uploaded': True, 'remote_logout': False, 'login_ok': True}
        session_state = {'session': 'SESSION-CONTENT', 'identity': {'city': '哈尔滨市'}}
        with patch.object(service, 'store', store), \
             patch('runner_remote.run_job', return_value={'ok': True, 'report': report,
                                                          'session_state': session_state}) as job:
            service.execute('browser-run-v2:' + 'a' * 64, 'account-run-v2:x', '13800000000',
                            'pw', {'city': '哈尔滨市'}, 2, 390)
        payload = job.call_args.args[0]
        self.assertEqual(payload['op'], 'run')
        self.assertEqual(payload['job_key'], 'a' * 64)
        self.assertNotIn('token', payload)
        self.assertEqual(len(calls), 1)
        key, finished, cipher = calls[0]
        self.assertIs(finished, report)
        self.assertEqual(json.loads(self.cipher.decrypt(cipher.encode())), session_state)

    def test_execute_finishes_without_cipher_after_confirmed_logout(self):
        store, calls = self.store_stub()
        report = {'ok': True, 'record_uploaded': True, 'remote_logout': True}
        with patch.object(service, 'store', store), \
             patch('runner_remote.run_job', return_value={'ok': True, 'report': report,
                                                          'session_state': {'session': 'x', 'identity': {}}}):
            service.execute('browser-run-v2:' + 'a' * 64, 'account-run-v2:x', '13800000000',
                            'pw', {'city': '哈尔滨市'}, 2, 390)
        self.assertIsNone(calls[0][2])

    def test_execute_failure_keeps_unknown_result(self):
        store, calls = self.store_stub(load_result={'session_ciphertext': None, 'phase': 'reserved'})
        with patch.object(service, 'store', store), \
             patch('runner_remote.run_job', side_effect=runner_remote.RelayError('relay_timeout')):
            service.execute('browser-run-v2:' + 'a' * 64, 'account-run-v2:x', '13800000000',
                            'pw', {'city': '哈尔滨市'}, 2, 390)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1].get('failure'), 'result_unknown_no_retry')
        self.assertIsNone(calls[0][2])

    def test_revoke_uses_relay_and_finishes(self):
        saved_state = {'session': 'OLD', 'identity': {'city': 'x'}}
        encrypted = self.cipher.encrypt(json.dumps(saved_state).encode()).decode()
        store, calls = self.store_stub(load_result={'session_ciphertext': encrypted,
                                                    'report': {'record_uploaded': True},
                                                    'phase': 'unknown'})
        with patch.object(service, 'store', store), \
             patch('runner_remote.run_job', return_value={'ok': True, 'found': True,
                                                          'remote_logout': True}) as job:
            service.revoke('browser-run-v2:' + 'a' * 64)
        payload = job.call_args.args[0]
        self.assertEqual(payload['op'], 'logout')
        self.assertEqual(payload['session_state'], saved_state)
        key, report, cipher = calls[0]
        self.assertTrue(report['remote_logout'])
        self.assertIsNone(cipher)

    def test_revoke_without_relay_state_leaves_row_alone(self):
        store, calls = self.store_stub(load_result={'session_ciphertext': None, 'report': None})
        with patch.object(service, 'store', store), \
             patch('runner_remote.run_job', return_value={'ok': True, 'found': False}):
            service.revoke('browser-run-v2:' + 'a' * 64)
        self.assertEqual(calls, [])

    def test_relay_error_in_revoke_keeps_state(self):
        encrypted = self.cipher.encrypt(json.dumps({'session': 'OLD', 'identity': {}}).encode()).decode()
        store, calls = self.store_stub(load_result={'session_ciphertext': encrypted, 'report': None,
                                                    'phase': 'unknown'})
        with patch.object(service, 'store', store), \
             patch('runner_remote.run_job', side_effect=runner_remote.RelayError('relay_exit_1')):
            service.revoke('browser-run-v2:' + 'a' * 64)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
