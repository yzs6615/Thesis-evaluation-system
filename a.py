# a.py 正确写法
from http.server import SimpleHTTPRequestHandler
import socketserver

PORT = 8080
with socketserver.TCPServer(("", PORT), SimpleHTTPRequestHandler) as httpd:
    print(f"服务器已启动，访问 http://localhost:{PORT}")
    httpd.serve_forever()