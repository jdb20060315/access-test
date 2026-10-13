"""仅验证浏览器访问，不接入运动账号或提交记录。"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>境外访问测试</title><body style="font-family:system-ui;margin:2rem;line-height:1.7">
<h1>连接成功</h1><p>你的浏览器已打开访问测试页面。</p>
<p>本页不登录账号，不生成或上传运动记录。</p>
<p>请分别使用移动网络和 Wi-Fi 打开，确认是否都能访问。</p></body></html>'''.encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/health'):
            self.send_error(404)
            return
        health = self.path == '/health'
        body = json.dumps({'ok': True, 'account_accessed': False}).encode() if health else PAGE
        self.send_response(200)
        self.send_header('Content-Type', 'application/json' if health else 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
