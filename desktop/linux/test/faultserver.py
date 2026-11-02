#!/usr/bin/env python3
"""Простой HTTP-сервер каталога для испытаний репозитория APT: python3 faultserver.py <каталог> <порт>.
Если в каталоге есть файл .truncate-once, первая же загрузка .deb обрывается после 1 МБ (заголовок Content-Length — полный),
файл-флаг удаляется: так проверяется поведение apt при прерванной загрузке пакета."""
import http.server, os, sys

ROOT, PORT = os.path.abspath(sys.argv[1]), int(sys.argv[2])


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def do_GET(self):
        flag = os.path.join(ROOT, ".truncate-once")
        if self.path.endswith(".deb") and os.path.exists(flag):
            os.remove(flag)
            path = self.translate_path(self.path)
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.debian.binary-package")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(path, "rb") as f:
                self.wfile.write(f.read(1 << 20))
            self.wfile.flush()
            self.close_connection = True
            print(f"оборвана загрузка {self.path} после 1 МБ из {size}", flush=True)
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        sys.stdout.write("%s\n" % (fmt % args)); sys.stdout.flush()


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
