#!/usr/bin/env python3
"""Нагрузка на LL-HLS: VIEWERS клиентов ведут себя как hls.js (долгие запросы плейлиста с _HLS_msn, части сегментов) через настоящий nginx и MediaMTX на всё время HOLD секунд.
Меряет задержку ответа плейлиста и доли ошибок и показывает загрузку служб. Адрес — аргумент: путь /llhls/… из ответа /access."""
import asyncio
import os
import re
import statistics
import subprocess
import sys
import time

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
VIEWERS, HOLD = int(os.environ.get("VIEWERS", "300")), float(os.environ.get("HOLD", "40"))
path = sys.argv[1]
root = path.rsplit("/", 1)[0] + "/"
stat = {"ok": 0, "err": 0, "bytes": 0}
waits: list[float] = []


async def viewer(c: httpx.AsyncClient, i: int, end: float):
    await asyncio.sleep(i * 0.02)
    jar: dict = {}

    async def get(rel):
        r = await c.get(root + rel, headers={"Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())}, timeout=20)
        if "hlsSession" in r.cookies:
            jar["hlsSession"] = r.cookies["hlsSession"]
        return r
    main = await get("index.m3u8")
    if main.status_code != 200:
        stat["err"] += 1
        return
    var = re.findall(r"^(video\S+\.m3u8)", main.text, re.M)[0]
    msn, part = None, 0
    seen: set = set()
    while time.time() < end:
        t0 = time.perf_counter()
        r = await get(var + (f"?_HLS_msn={msn}&_HLS_part={part}" if msn is not None else ""))
        if r.status_code != 200:
            stat["err"] += 1
            await asyncio.sleep(0.5)
            continue
        waits.append(time.perf_counter() - t0)
        stat["ok"] += 1
        parts = re.findall(r'#EXT-X-PART:[^\n]*URI="([^"]+)"', r.text)
        for p in parts[-2:]:
            if p not in seen:
                seen.add(p)
                b = await get(p)
                stat["bytes"] += len(b.content)
                stat["ok" if b.status_code == 200 else "err"] += 1
        m = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", r.text)
        segs = len(re.findall(r"^#EXTINF", r.text, re.M))
        msn = int(m.group(1)) + segs
        part = len(re.findall(r"#EXT-X-PART:", r.text.rsplit("#EXTINF", 1)[-1]))


async def main():
    end = time.time() + HOLD
    limits = httpx.Limits(max_connections=VIEWERS * 2, max_keepalive_connections=VIEWERS * 2)
    async with httpx.AsyncClient(base_url=BASE, limits=limits) as c:
        task = asyncio.gather(*(viewer(c, i, end) for i in range(VIEWERS)), return_exceptions=True)
        await asyncio.sleep(HOLD * 0.7)
        print(subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}} {{.MemUsage}}", "pgdoc-mediamtx-1", "pgdoc-web-1", "pgdoc-backend-1"], capture_output=True, text=True).stdout.strip(), flush=True)
        res = await task
    bad = [r for r in res if isinstance(r, Exception)]
    w = sorted(waits)
    print(f"зрителей {VIEWERS}: ответов {stat['ok']}, ошибок {stat['err']}, сбоев клиента {len(bad)}, {stat['bytes'] >> 20} МБ; плейлист p50 {statistics.median(w) * 1000:.0f} мс, p95 {w[int(len(w) * .95)] * 1000:.0f} мс" if w else f"нет ответов {stat} {bad[:2]}")


asyncio.run(main())
