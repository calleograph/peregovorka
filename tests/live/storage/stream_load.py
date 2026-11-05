#!/usr/bin/env python3
"""Много одновременно открытых записей: соединения с базой не должны быть заняты, пока файл передаётся. Открывает STREAMS потоков с произвольной позиции и читает их медленно,
в это время меряет обычный запрос API. До исправления пул соединений кончался, и API отвечал 503."""
import asyncio
import os
import statistics
import subprocess
import sys
import time

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
HDR = {"Origin": BASE.replace("127.0.0.1", "localhost")}
STREAMS, HOLD = int(os.environ.get("STREAMS", "300")), float(os.environ.get("HOLD", "25"))


def sql(q):
    return subprocess.run(["docker", "exec", "pgdoc-postgres-1", "sh", "-c", f'psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc "{q}"'], capture_output=True, text=True, check=True).stdout.strip()


async def main():
    async with httpx.AsyncClient(base_url=BASE, headers=HDR, timeout=60, limits=httpx.Limits(max_connections=STREAMS + 20)) as c:
        r = await c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.path.expanduser("~/pg-work/cdnlab/admin.pw")).read().strip()})
        if r.status_code == 409:
            d = r.json()["detail"]
            r = await c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][0]["id"]})
        c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
        c.cookies.clear()
        mid, rec = sql("select meeting_id||'|'||id from recordings where export_status='exported' and kind='mix_video' and size_bytes > 100000000 order by created_at desc limit 1").split("|")
        opened, stop = [0], asyncio.Event()

        async def stream(i):
            async with c.stream("GET", f"/api/v1/meetings/{mid}/media/{rec}/stream", headers={"Range": f"bytes={1024 * (i + 1)}-"}) as r:
                assert r.status_code == 206, r.status_code
                opened[0] += 1
                it = r.aiter_bytes(65536)
                await it.__anext__()
                await stop.wait()                 # соединение открыто, файл передаётся медленно

        tasks = [asyncio.create_task(stream(i)) for i in range(STREAMS)]
        t0 = time.time()
        while opened[0] < STREAMS and time.time() - t0 < 40 and not any(t.done() and t.exception() for t in tasks):
            await asyncio.sleep(0.2)
        print(f"открыто потоков: {opened[0]} из {STREAMS} за {time.time() - t0:.1f} с", flush=True)
        ms, bad = [], 0
        end = time.time() + HOLD
        while time.time() < end:
            t1 = time.perf_counter()
            try:
                r = await c.get("/api/v1/auth/me", timeout=8)
                r2 = await c.get("/api/v1/admin/storages", timeout=8)
                ok = r.status_code == 200 and r2.status_code == 200
            except httpx.TimeoutException:
                ok = False
            ms.append((time.perf_counter() - t1) * 1000)
            bad += not ok
            await asyncio.sleep(0.2)
        held = sql("select count(*) from pg_stat_activity where state like 'idle in transaction%' and datname=current_database()")
        print(f"запросы API при открытых потоках: {len(ms)}, ошибок {bad}, медиана {statistics.median(ms):.0f} мс, худший {max(ms):.0f} мс; соединений в незавершённой транзакции: {held}", flush=True)
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return 0 if opened[0] == STREAMS and bad == 0 and int(held) < 5 else 1


sys.exit(asyncio.run(main()))
