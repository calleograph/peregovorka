#!/usr/bin/env python3
"""Проверка доступа к внешнему репозиторию так, как его использует сборка: DNS → TCP → (прокси CONNECT) → TLS (SNI) → HTTP → ЗАГРУЗКА данных.
Обычная проверка «интернет есть» (DNS, TCP, код 200 на маленький запрос) не видит, что передача большого ответа останавливается,
поэтому здесь скачивается заданный объём (по умолчанию до 4 МБ, для больших файлов — запросом Range) и меряется скорость и паузы.

Только стандартная библиотека: запускается внутри базового образа сборки (python:3.12-slim) — в той же сети, где выполняются шаги RUN.
    netprobe.py URL [URL…] [--bytes N] [--stall S] [--max-time S] [--min-rate КБ/с]
Каждая строка вывода — JSON одного адреса: {"url","result","stage","detail",...}. result: ok | slow | fail.
stage (где сбой): dns | tcp | proxy | tls | http | stall | none. Прокси — из HTTPS_PROXY/HTTP_PROXY (с учётом NO_PROXY), как у pip и npm.
Код возврата: 0 — все ok/slow, 1 — есть fail.
"""
from __future__ import annotations

import argparse
import base64
import errno
import http.client
import json
import os
import socket
import ssl
import sys
import time
import urllib.parse

UA = "peregovorka-netprobe/1"


def _proxy_for(url: urllib.parse.SplitResult) -> str | None:
    host = (url.hostname or "").lower()
    for item in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(","):
        item = item.strip().lower().lstrip(".")
        if item and (item == "*" or host == item or host.endswith("." + item)):
            return None
    keys = ("HTTPS_PROXY", "https_proxy") if url.scheme == "https" else ("HTTP_PROXY", "http_proxy")
    for k in keys:
        if os.environ.get(k):
            return os.environ[k]
    return None


def _connect(host: str, port: int, timeout: float, res: dict) -> socket.socket:
    t0 = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        res.update(stage="dns", detail=f"имя {host} не разрешается: {exc.strerror or exc}")
        raise
    res["dns_ms"] = round((time.monotonic() - t0) * 1000)
    fams = sorted({"ipv6" if i[0] == socket.AF_INET6 else "ipv4" for i in infos})
    res["families"] = fams
    # Как сборка: сначала IPv4 (без IPv6-маршрута попытки IPv6 только тратят время), затем остальные адреса
    infos.sort(key=lambda i: 0 if i[0] == socket.AF_INET else 1)
    errs = []
    for fam, typ, proto, _c, addr in infos[:6]:
        s = socket.socket(fam, typ, proto)
        s.settimeout(timeout)
        t1 = time.monotonic()
        try:
            s.connect(addr)
            res["connect_ms"] = round((time.monotonic() - t1) * 1000)
            res["address"] = addr[0]
            return s
        except OSError as exc:
            s.close()
            errs.append(f"{addr[0]}: {'нет ответа' if isinstance(exc, socket.timeout) else (exc.strerror or exc)}")
            res.setdefault("_tcp_errnos", []).append("timeout" if isinstance(exc, socket.timeout) else exc.errno)
    kinds = res.pop("_tcp_errnos", [])
    why = "нет ответа (таймаут — файрвол или маршрут)" if all(k == "timeout" for k in kinds) else \
        "соединение отклонено" if errno.ECONNREFUSED in kinds else "сеть недоступна"
    res.update(stage="tcp", detail=f"TCP {host}:{port}: {why}; " + "; ".join(errs[:3]))
    raise OSError(res["detail"])


def probe(url: str, want: int, stall: float, max_time: float, min_rate_kbps: float, hops: int = 0) -> dict:
    u = urllib.parse.urlsplit(url)
    res: dict = {"url": url, "result": "fail", "stage": "none", "detail": ""}
    port = u.port or (443 if u.scheme == "https" else 80)
    proxy = _proxy_for(u)
    started = time.monotonic()
    try:
        if proxy:
            p = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
            res["proxy"] = f"{p.hostname}:{p.port or 3128}"
            sock = _connect(p.hostname or "", p.port or 3128, 15, res)
            if u.scheme == "https":
                hdr = f"CONNECT {u.hostname}:{port} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\n"
                if p.username:
                    cred = base64.b64encode(f"{urllib.parse.unquote(p.username)}:{urllib.parse.unquote(p.password or '')}".encode()).decode()
                    hdr += f"Proxy-Authorization: Basic {cred}\r\n"
                sock.sendall((hdr + "\r\n").encode())
                sock.settimeout(20)
                buf = b""
                while b"\r\n\r\n" not in buf and len(buf) < 65536:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                line = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
                code = line.split(" ")[1] if len(line.split(" ")) > 1 else "?"
                if code != "200":
                    res.update(stage="proxy", detail=f"прокси {res['proxy']} не открыл туннель к {u.hostname}: {line[:120]}"
                               + (" (нужна авторизация на прокси)" if code == "407" else ""))
                    return res
        else:
            sock = _connect(u.hostname or "", port, 15, res)
        if u.scheme == "https":
            ctx = ssl.create_default_context()
            sock.settimeout(20)
            t1 = time.monotonic()
            try:
                sock = ctx.wrap_socket(sock, server_hostname=u.hostname)
            except ssl.SSLCertVerificationError as exc:
                res.update(stage="tls", detail=f"сертификату {u.hostname} нет доверия: {exc.verify_message} (прокси с подменой сертификатов? нужен корневой сертификат организации в образе)")
                return res
            except (ssl.SSLError, OSError) as exc:
                res.update(stage="tls", detail=f"TLS с {u.hostname} не установлен: {'нет ответа' if isinstance(exc, socket.timeout) else exc}")
                return res
            res["tls_ms"] = round((time.monotonic() - t1) * 1000)
        sock.settimeout(stall)                     # дальше таймаут = «сколько ждать новых данных»
        conn = http.client.HTTPConnection(u.hostname or "", port, timeout=stall)
        conn.sock = sock
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        if proxy and u.scheme == "http":
            path = url
        headers = {"User-Agent": UA, "Accept": "*/*", "Connection": "close"}
        if want:
            headers["Range"] = f"bytes=0-{want - 1}"
        t2 = time.monotonic()
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
        except (socket.timeout, TimeoutError):
            res.update(stage="http", detail=f"сервер не прислал ответ за {stall:.0f} с после запроса")
            return res
        res["status"] = resp.status
        res["ttfb_ms"] = round((time.monotonic() - t2) * 1000)
        if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location") and hops < 4:
            nxt = urllib.parse.urljoin(url, resp.getheader("Location"))
            sub = probe(nxt, want, stall, max_time, min_rate_kbps, hops + 1)
            sub["url"], sub["redirected_to"] = url, nxt
            return sub
        if resp.status == 407:
            res.update(stage="proxy", detail="прокси требует авторизацию (HTTP 407)")
            return res
        if resp.status >= 400:
            res.update(stage="http", detail=f"HTTP {resp.status} {resp.reason} — репозиторий отказал (адрес, доступ или ограничение по числу запросов)")
            return res
        got, t3, last = 0, time.monotonic(), time.monotonic()
        try:
            while not want or got < want:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                got += len(chunk)
                last = time.monotonic()
                if last - t3 > max_time:
                    break
        except (socket.timeout, TimeoutError):
            secs = time.monotonic() - t3
            res.update(stage="stall", bytes=got, seconds=round(secs, 2),
                       detail=f"передача остановилась: за {secs:.0f} с получено {got // 1024} КБ, новых данных нет {stall:.0f} с "
                              f"(DNS, TCP и TLS в порядке — ограничение или потери на пути к {u.hostname}: прокси, DPI, MTU, канал)")
            return res
        except (ssl.SSLError, OSError) as exc:
            secs = time.monotonic() - t3
            res.update(stage="stall", bytes=got, seconds=round(secs, 2), detail=f"соединение оборвано при загрузке после {got // 1024} КБ: {exc}")
            return res
        secs = max(time.monotonic() - t3, 1e-3)
        rate = got / 1024 / secs
        res.update(bytes=got, seconds=round(secs, 2), rate_kbps=round(rate), stage="none")
        if want and got < want and secs >= max_time:
            res.update(result="slow", stage="stall", detail=f"медленно: за {secs:.0f} с получено {got // 1024} КБ ({rate:.0f} КБ/с)")
        elif got >= 256 * 1024 and rate < min_rate_kbps:
            res.update(result="slow", detail=f"медленно: {rate:.0f} КБ/с (сборка займёт заметно дольше обычного)")
        else:
            res.update(result="ok", detail=f"{got // 1024} КБ за {secs:.2f} с ({rate:.0f} КБ/с)")
        return res
    except OSError:
        return res
    finally:
        res["total_ms"] = round((time.monotonic() - started) * 1000)


def resolve(spec: str) -> str:
    """Адрес настоящего файла для проверки: pypi:ПАКЕТ==ВЕРСИЯ — самое большое колесо (wheel) этой версии с PyPI; apt: — индекс пакетов
    зеркала, которое прописано в самом образе (/etc/apt/sources*); остальное — как есть."""
    if spec.startswith("pypi:"):
        name, _, ver = spec[5:].partition("==")
        api = f"https://pypi.org/pypi/{name}/{ver}/json" if ver else f"https://pypi.org/pypi/{name}/json"
        try:
            import urllib.request
            data = json.load(urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": UA}), timeout=30))
            files = sorted(data.get("urls", []), key=lambda f: f.get("size", 0), reverse=True)
            return files[0]["url"] if files else api
        except Exception:  # noqa: BLE001 — недоступность самого PyPI покажет проверка адреса API
            return api
    if spec == "apt:":
        import glob
        import re
        for f in glob.glob("/etc/apt/sources.list.d/*.sources") + glob.glob("/etc/apt/sources.list"):
            txt = open(f, encoding="utf-8", errors="replace").read()
            m = re.search(r"^URIs:\s*(\S+).*?^Suites:\s*(\S+)", txt, re.M | re.S) or re.search(r"^deb\s+(\S+)\s+(\S+)", txt, re.M)
            if m:
                return f"{m.group(1).rstrip('/')}/dists/{m.group(2)}/main/binary-amd64/Packages.xz"
        return "http://deb.debian.org/debian/dists/stable/Release"
    return spec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--bytes", type=int, default=4 * 1024 * 1024, help="сколько скачать (0 — весь ответ)")
    ap.add_argument("--stall", type=float, default=20.0, help="сколько секунд без новых данных считается остановкой")
    ap.add_argument("--max-time", type=float, default=60.0)
    ap.add_argument("--min-rate", type=float, default=100.0, help="КБ/с, ниже — «медленно»")
    a = ap.parse_args()
    bad = 0
    for spec in a.urls:
        url = resolve(spec)
        r = probe(url, a.bytes, a.stall, a.max_time, a.min_rate)
        if url != spec:
            r["source"] = spec
        bad += r["result"] == "fail"
        print(json.dumps(r, ensure_ascii=False), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
