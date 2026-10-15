"""并发入口检查：不连接数据库，也不登录或提交第三方平台。"""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import personal_service as service
from server import Handler


class ConcurrencyTest(unittest.TestCase):
    def test_container_memory_limit_blocks_new_tasks(self):
        def read_text(path):
            paths = {
                '/proc/meminfo': 'MemTotal: 8388608 kB\nMemAvailable: 6291456 kB\n',
                '/sys/fs/cgroup/memory.max': str(512 * 1024 * 1024),
                '/sys/fs/cgroup/memory.current': str(400 * 1024 * 1024),
            }
            name = str(path).replace('\\', '/')
            if name not in paths:
                raise FileNotFoundError(str(path))
            return paths[name]

        with patch.object(service.Path, 'read_text', read_text):
            self.assertEqual(service.memory_available(),
                             (512 * 1024 * 1024, 112 * 1024 * 1024))
            self.assertFalse(service.resources_available())

    def test_http_run_and_status_with_thirty_two_simulated_users(self):
        release = threading.Event()
        started = threading.Event()
        started_count = 0
        started_lock = threading.Lock()

        def fake_execute(key, *_args):
            nonlocal started_count
            with started_lock:
                started_count += 1
                if started_count == 32:
                    started.set()
            release.wait(5)
            with service.LOCK:
                service.ACTIVE_KEYS.discard(key)

        def fetch(url, method='GET', csrf='first', payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            request = Request(url, data=data, method=method, headers={
                'Origin': service.ORIGIN, 'X-CSRF-Token': csrf,
                'X-Test-Session': csrf, 'Content-Type': 'application/json',
            })
            try:
                with urlopen(request, timeout=3) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)

        payload = {
            'username': '13800000000', 'password': 'test-only', 'consent': True,
            'city': 'test', 'latitude': '1', 'longitude': '1',
            'distance_km': '2.26', 'cadence': '160', 'step_length_m': '1',
        }
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        url = f'http://127.0.0.1:{server.server_port}'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        service.ACTIVE_KEYS.clear()
        try:
            with ThreadPoolExecutor(max_workers=32) as pool, \
                 patch.object(service, 'READY', True), \
                 patch.object(service, 'MAX_ACTIVE_RUNS', None), \
                 patch.object(service, 'resources_available', return_value=True) as resources, \
                 patch.object(service, 'start_task', side_effect=lambda target, *args: pool.submit(target, *args)), \
                 patch.object(service, 'session', side_effect=lambda handler: handler.headers.get('X-Test-Session')), \
                 patch.object(service, 'settings', return_value=('test-secret', None, None, None)), \
                 patch.object(service, 'location', return_value={}), \
                 patch.object(service.store, 'reserve', return_value=True), \
                 patch.object(service.store, 'load', return_value=None), \
                 patch.object(service, 'execute', side_effect=fake_execute):
                self.assertEqual([fetch(url + '/api/run', 'POST', f'user-{i}', payload)[0]
                                  for i in range(32)], [202] * 32)
                self.assertTrue(started.wait(2))
                self.assertEqual(fetch(url + '/api/status', csrf='user-0')[1]['busy'], True)
                resources.return_value = False
                self.assertEqual(fetch(url + '/api/run', 'POST', 'user-32', payload)[0], 503)
                self.assertEqual(fetch(url + '/api/run', 'POST', 'user-0', payload)[0], 409)
                release.set()
                pool.shutdown(wait=True)
                self.assertEqual(fetch(url + '/api/status', csrf='user-0')[1]['busy'], False)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            service.ACTIVE_KEYS.clear()

    def test_four_independent_sessions_fit_and_fifth_waits(self):
        def request(csrf):
            payload = json.dumps({
                'username': '13800000000', 'password': 'test-only', 'consent': True,
                'city': 'test', 'latitude': '1', 'longitude': '1',
                'distance_km': '2.26', 'cadence': '160', 'step_length_m': '1',
            }).encode()
            return SimpleNamespace(
                path='/api/run', csrf=csrf,
                headers={'Origin': service.ORIGIN, 'Content-Length': str(len(payload)),
                         'X-CSRF-Token': csrf},
                connection=SimpleNamespace(settimeout=lambda _: None),
                rfile=io.BytesIO(payload),
            )

        replies = []
        service.ACTIVE_KEYS.clear()
        try:
            with patch.object(service, 'READY', True), \
                 patch.object(service, 'MAX_ACTIVE_RUNS', 4), \
                 patch.object(service, 'session', side_effect=lambda handler: handler.csrf), \
                 patch.object(service, 'settings', return_value=('test-secret', None, None, None)), \
                 patch.object(service, 'location', return_value={}), \
                 patch.object(service.store, 'reserve', return_value=True), \
                 patch.object(service, 'resources_available', return_value=True), \
                 patch.object(service, 'start_task'), \
                 patch.object(service, 'send', side_effect=lambda _h, status, body: replies.append((status, body))):
                for csrf in ('first', 'second', 'third', 'fourth', 'fifth', 'first'):
                    service.handle(request(csrf), 'POST')
            self.assertEqual([status for status, _ in replies], [202, 202, 202, 202, 503, 409])
            self.assertEqual(service.ACTIVE_KEYS, {
                'browser-run-v2:first', 'browser-run-v2:second',
                'browser-run-v2:third', 'browser-run-v2:fourth',
            })
        finally:
            service.ACTIVE_KEYS.clear()


if __name__ == '__main__':
    unittest.main()
