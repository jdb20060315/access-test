"""展示后台兼容性检查结果；无账号接口、无记录上传接口。"""
import html
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).parent


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/health', '/runtime'):
            self.send_error(404)
            return
        path = ROOT / 'runtime_report.json'
        report = json.loads(path.read_text()) if path.exists() else {'runtime_verified': False, 'failure': 'check_not_run'}
        if self.path == '/':
            state = '后台程序启动检查通过' if report.get('runtime_verified') else '后台兼容性检查未通过'
            page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>后台兼容性测试</title><body style="font:18px/1.7 system-ui;margin:2rem"><h1>' + state + '</h1><p>这是运行环境检查。账号登录、运动提交和持久化数据尚未接入。</p><pre style="white-space:pre-wrap">' + html.escape(json.dumps(report, ensure_ascii=False, indent=2)) + '</pre></body></html>'
            body = page.encode()
            kind = 'text/html; charset=utf-8'
        else:
            data = {'ok': True, 'account_accessed': False} if self.path == '/health' else report
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


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
