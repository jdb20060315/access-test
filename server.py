"""同校多账号网页服务和兼容性检查。"""
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from check_database import check_database
import personal_service

ROOT = Path(__file__).parent
DATABASE_REPORT = {'configured': False, 'connection_verified': False, 'failure': 'check_not_run'}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/health', '/runtime', '/database'):
            personal_service.handle(self, 'GET')
            return
        path = ROOT / 'runtime_report.json'
        report = json.loads(path.read_text()) if path.exists() else {'runtime_verified': False, 'failure': 'check_not_run'}
        report['database'] = DATABASE_REPORT
        report['personal_test_ready'] = personal_service.READY
        with personal_service.LOCK:
            active_tasks = len(personal_service.ACTIVE_KEYS)
        data = {'ok': True, 'personal_test_ready': personal_service.READY, 'multi_account_ready': personal_service.READY, 'release': 'multi-school-v12-vps-relay', 'runner_mode': personal_service.RUNNER_MODE, 'max_active_runs': personal_service.MAX_ACTIVE_RUNS, 'active_tasks': active_tasks} if self.path == '/health' else DATABASE_REPORT if self.path == '/database' else report
        body = json.dumps(data).encode()
        kind = 'application/json'
        self.send_response(200)
        for key, value in {'Content-Type': kind, 'Content-Length': str(len(body)),
                           'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                           'X-Frame-Options': 'DENY'}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

    def do_POST(self):
        personal_service.handle(self, 'POST')


if __name__ == '__main__':
    DATABASE_REPORT = check_database()
    personal_service.initialize()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
