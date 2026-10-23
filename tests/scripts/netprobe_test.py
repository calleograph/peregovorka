#!/usr/bin/env python3
"""Проверка scripts/netprobe.py на локальных серверах, имитирующих неисправности сети: передача останавливается после 100 КБ (как у npm
в отчёте), медленная передача, отказ в соединении, недоверенный TLS, прокси требует авторизацию, редирект, нормальная загрузка.
Без внешней сети. Запуск: python3 tests/scripts/netprobe_test.py  (код 0 — все случаи распознаны верно)."""
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROBE = os.path.join(ROOT, "scripts", "netprobe.py")


def server(handler):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 0)); s.listen(8)

    def loop():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            threading.Thread(target=handler, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return s.getsockname()[1]


def read_req(c):
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = c.recv(4096)
        if not d:
            break
        buf += d
    return buf


def h_ok(c):
    read_req(c); body = b"x" * (1024 * 1024)
    c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body); c.close()


def h_stall(c):
    read_req(c); c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 7000000\r\n\r\n" + b"y" * (100 * 1024)); time.sleep(30); c.close()


def h_slow(c):
    read_req(c); c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 7000000\r\n\r\n")
    try:
        for _ in range(40):
            c.sendall(b"z" * 2048); time.sleep(0.1)
    except OSError:
        pass
    c.close()


def h_redirect_to(port):
    def h(c):
        read_req(c); c.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:%d/file\r\nContent-Length: 0\r\n\r\n" % port); c.close()
    return h


def h_407(c):
    read_req(c); c.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n"); c.close()


def run(urls, env=None, *extra):
    out = subprocess.run([sys.executable, PROBE, *urls, "--stall", "2", "--max-time", "3", "--min-rate", "100", *extra],
                         capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=60)
    return [json.loads(l) for l in out.stdout.splitlines() if l.strip()]


def main():
    fails = 0

    def check(name, cond, got):
        nonlocal fails
        print(("PASS" if cond else "FAIL") + ": " + name + ("" if cond else f"  → {got}"))
        fails += 0 if cond else 1

    ok = server(h_ok); stall = server(h_stall); slow = server(h_slow); redir = server(h_redirect_to(ok)); p407 = server(h_407)
    closed = socket.socket(); closed.bind(("127.0.0.1", 0)); cport = closed.getsockname()[1]; closed.close()
    r = run([f"http://127.0.0.1:{ok}/f"])[0]
    check("нормальная загрузка: ok, скачано ≥ 1 МБ", r["result"] == "ok" and r["bytes"] >= 1024 * 1024, r)
    r = run([f"http://127.0.0.1:{stall}/f"])[0]
    check("передача остановилась после ~100 КБ (как npm в отчёте): fail, этап stall, получено ~100 КБ", r["result"] == "fail" and r["stage"] == "stall" and 90 * 1024 <= r["bytes"] <= 120 * 1024, r)
    r = run([f"http://127.0.0.1:{slow}/f"])[0]
    check("медленная передача (~20 КБ/с): slow, а не ok и не fail", r["result"] == "slow", r)
    r = run([f"http://127.0.0.1:{cport}/f"])[0]
    check("порт закрыт: fail, этап tcp, «отклонено»", r["result"] == "fail" and r["stage"] == "tcp" and "отклонено" in r["detail"], r)
    r = run(["http://name.invalid/f"])[0]
    check("имя не разрешается: этап dns", r["stage"] == "dns", r)
    r = run([f"http://127.0.0.1:{redir}/r"])[0]
    check("редирект выполняется, итог — по конечному адресу", r["result"] == "ok" and r.get("redirected_to", "").endswith("/file"), r)
    r = run(["https://registry.example.invalid/pkg.tgz"], {"HTTPS_PROXY": f"http://127.0.0.1:{p407}"})[0]
    check("прокси требует авторизацию: этап proxy, 407", r["stage"] == "proxy" and "407" in r["detail"], r)
    r = run(["https://registry.example.invalid/pkg.tgz"], {"HTTPS_PROXY": f"http://127.0.0.1:{p407}", "NO_PROXY": "example.invalid"})[0]
    check("NO_PROXY: адрес идёт напрямую (этап dns, не proxy)", r["stage"] == "dns", r)
    # недоверенный сертификат (самоподписанный) — этап tls, сертификат не принимается молча
    d = tempfile.mkdtemp()
    if subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{d}/k.pem", "-out", f"{d}/c.pem", "-days", "1", "-subj", "/CN=localhost"],
                      capture_output=True).returncode == 0:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(f"{d}/c.pem", f"{d}/k.pem")

        def h_tls(c):
            try:
                t = ctx.wrap_socket(c, server_side=True); h_ok(t)
            except OSError:
                c.close()
        tport = server(h_tls)
        r = run([f"https://localhost:{tport}/f"])[0]
        check("самоподписанный сертификат: fail, этап tls, «нет доверия»", r["stage"] == "tls" and "доверия" in r["detail"], r)
    else:
        print("SKIP: openssl недоступен — случай TLS не проверен")
    print(f"netprobe: провалено {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
