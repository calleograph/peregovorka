#!/usr/bin/env python3
"""Масштабирование зрителей: N одновременных «плееров» HLS (раздача nginx прямо с сервера) на настоящем эфире (синтетический выступающий latency_probe.py, настоящие LiveKit/Egress/nginx).
Каждый «плеер» читает плейлист и скачивает новые сегменты так же, как плеер зрителя. Снимается: время ответа плейлиста и сегмента (p50/p95/макс), ошибки HTTP, повторные загрузки,
доля опозданий (сегмент скачан дольше своей длительности — буфер у настоящего зрителя пустел бы), исходящий трафик nginx и нагрузка сервера (CPU, диск, сеть, контейнеры).
  ADMIN_PW_FILE=… PEREGOVORKA_URL=http://localhost:28480 python viewer_scale.py <качество 720p|1080p|1440p> [N через запятую: 1,10,30,60] [секунд на ступень=30] [kbit/s на зрителя, 0 — без ограничения]
Результат: таблица в консоль и JSON в $SMOKE_OUT/scale-<качество>.json"""
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import smoke_room as S  # noqa: E402
from browser_chain import docker_stats, host_counters, host_rates  # noqa: E402  (модуль запускает main только при прямом вызове)

BASE = os.environ.get("PEREGOVORKA_URL", "http://localhost:28480")
QUALITY = sys.argv[1] if len(sys.argv) > 1 else "720p"
STEPS = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "1,10,30,60").split(",")]
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 30
LIMIT_KBIT = int(sys.argv[4]) if len(sys.argv) > 4 else 0
SIZE = {"720p": "1280x720", "1080p": "1920x1080", "1440p": "2560x1440"}[QUALITY]


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 3)


async def player(client: httpx.AsyncClient, playlist: str, stop: asyncio.Event, acc: dict, limit_bps: int) -> None:
    base = playlist.rsplit("/", 1)[0]
    seen: set[str] = set()
    while not stop.is_set():
        t0 = time.perf_counter()
        try:
            r = await client.get(BASE + playlist)
            acc["pl_ms"].append((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                acc["err"] += 1
                await asyncio.sleep(1)
                continue
        except httpx.HTTPError:
            acc["err"] += 1
            await asyncio.sleep(1)
            continue
        dur, new = 2.0, []
        for line in r.text.splitlines():
            if line.startswith("#EXTINF:"):
                dur = float(line.split(":")[1].split(",")[0])
            elif line and not line.startswith("#") and line not in seen:
                new.append((line, dur))
        for uri, d in new[-3:]:                                   # новый плеер берёт последние сегменты, как при входе в эфир
            seen.add(uri)
            t1 = time.perf_counter()
            try:
                s = await client.get(f"{BASE}{base}/{uri}")
                el = time.perf_counter() - t1
                if limit_bps:
                    await asyncio.sleep(max(0, len(s.content) * 8 / limit_bps - el))     # канал зрителя ограничен: скачивание не быстрее заданной скорости
                    el = time.perf_counter() - t1
                if s.status_code != 200:
                    acc["err"] += 1
                    continue
                acc["seg_ms"].append(el * 1000)
                acc["bytes"] += len(s.content)
                acc["segs"] += 1
                if el > d:
                    acc["late"] += 1
            except httpx.HTTPError:
                acc["err"] += 1
        await asyncio.sleep(max(0.3, (new[-1][1] if new else 2.0) * 0.5))


async def main():
    c = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=90)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": sorted(d["sessions"], key=lambda s: s["created_at"])[0]["id"]})
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3, "allow_private_destinations": True, "rtc_viewers_max": 0, "ll_hls": False, "adaptive_viewers": False}).raise_for_status()
    slug = f"sc-{time.strftime('%H%M%S')}"
    rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": QUALITY, "live_cdn": False}).json()
    token = lv["view_path"].rsplit("/", 1)[-1]
    j = c.post(f"/api/v1/rooms/{rid}/join", json={}).json()
    mid = j["meeting_id"]
    pub = subprocess.Popen([sys.executable, os.path.join(HERE, "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"], "--size", SIZE, "--seconds", "900"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "PUBLISHING" in pub.stdout.readline()
    out = {"quality": QUALITY, "limit_kbit": LIMIT_KBIT, "steps": []}
    try:
        await asyncio.sleep(3)
        c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": True}).raise_for_status()
        vc = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=30)
        for _ in range(240):
            if vc.get(f"/api/v1/live/{token}").json()["status"] == "live":
                break
            await asyncio.sleep(0.5)
        viewer = vc.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Нагрузка"}).json()["viewer"]
        acc0 = vc.post(f"/api/v1/live/{token}/access", json={"viewer": viewer}).json()
        playlist = acc0.get("origin_path") or acc0["path"]
        await asyncio.sleep(12)
        limits = httpx.Limits(max_connections=max(STEPS) * 2 + 10, max_keepalive_connections=max(STEPS) * 2 + 10)
        async with httpx.AsyncClient(timeout=30, limits=limits) as client:
            for n in STEPS:
                acc = {"pl_ms": [], "seg_ms": [], "err": 0, "bytes": 0, "segs": 0, "late": 0}
                stop = asyncio.Event()
                tasks = [asyncio.create_task(player(client, playlist, stop, acc, LIMIT_KBIT * 1000)) for _ in range(n)]
                h0 = host_counters()
                await asyncio.sleep(SECONDS / 2)
                dk = docker_stats()
                await asyncio.sleep(SECONDS / 2)
                h1 = host_counters()
                stop.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                hr = host_rates(h0, h1)
                web = (hr.get("net_mbit") or {})
                row = {"viewers": n, "pl_ms_p50": pct(acc["pl_ms"], .5), "pl_ms_p95": pct(acc["pl_ms"], .95), "seg_ms_p50": pct(acc["seg_ms"], .5), "seg_ms_p95": pct(acc["seg_ms"], .95),
                       "seg_ms_max": round(max(acc["seg_ms"]), 1) if acc["seg_ms"] else None, "errors": acc["err"], "segments": acc["segs"], "late_pct": round(100 * acc["late"] / max(1, acc["segs"]), 1),
                       "payload_mbit_s": round(acc["bytes"] * 8 / SECONDS / 1e6, 1), "host_cpu_pct": hr["cpu_busy_pct"], "disk": hr.get("disk"), "docker": dk,
                       "net_if": {k: v for k, v in web.items() if k.startswith(("lo", "docker", "br"))}}
                out["steps"].append(row)
                print(f'N={n:>3}: плейлист p50/p95 {row["pl_ms_p50"]}/{row["pl_ms_p95"]} мс | сегмент p50/p95/макс {row["seg_ms_p50"]}/{row["seg_ms_p95"]}/{row["seg_ms_max"]} мс | ошибок {row["errors"]} | опозданий {row["late_pct"]} % '
                      f'| отдано {row["payload_mbit_s"]} Мбит/с | CPU хоста {row["host_cpu_pct"]} % | web {dk.get("web", {}).get("cpu_pct")} % egress {dk.get("livekit-egress", {}).get("cpu_pct")} %', flush=True)
    finally:
        c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": False})
        await asyncio.sleep(3)
        pub.terminate()
        c.post(f"/api/v1/meetings/{mid}/end")
        c.delete(f"/api/v1/admin/rooms/{rid}")
    os.makedirs(S.OUT, exist_ok=True)
    path = os.path.join(S.OUT, f"scale-{QUALITY}{'-' + str(LIMIT_KBIT) if LIMIT_KBIT else ''}.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1, default=str)
    print("файл:", path)


if __name__ == "__main__":
    asyncio.run(main())
