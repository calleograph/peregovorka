"""Заглушка backend/livekit для проверки web-образа: отвечает JSON с заголовками прокси, которые она получила. Использование: python stub.py ПОРТ"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"path": self.path, "xfp": self.headers.get("X-Forwarded-Proto"), "xff": self.headers.get("X-Forwarded-For"),
                           "host": self.headers.get("Host")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # тихо
        pass


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
