"""Тестовый HTTP-сервер для models.sh: отдаёт файлы с поддержкой Range; первый GET каждого файла обрывается на половине (нестабильное соединение)."""
import http.server, os, sys

ROOT, CUT = sys.argv[1], set()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _file(self):
        p = os.path.join(ROOT, os.path.basename(self.path))
        return p if os.path.isfile(p) else None

    def do_HEAD(self):
        p = self._file()
        if not p: return self.send_error(404)
        self.send_response(200); self.send_header("Content-Length", str(os.path.getsize(p))); self.end_headers()

    def do_GET(self):
        p = self._file()
        if not p: return self.send_error(404)
        size, start = os.path.getsize(p), 0
        r = self.headers.get("Range")
        if r: start = int(r.split("=")[1].split("-")[0])
        self.send_response(206 if r else 200)
        self.send_header("Content-Length", str(size - start))
        if r: self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.end_headers()
        data = open(p, "rb").read()[start:]
        if p not in CUT and not r:
            CUT.add(p); self.wfile.write(data[: len(data) // 2]); self.wfile.flush(); self.close_connection = True; return
        self.wfile.write(data)


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
