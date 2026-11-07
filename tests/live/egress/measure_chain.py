#!/usr/bin/env python3
"""Сквозные замеры эфира на настоящих LiveKit, Egress, nginx: время запуска, задержка до сегмента HLS, до площадки RTMP и до настоящего плеера зрителя (среднее, p50, p95, максимум),
остановки и потерянные кадры плеера, синхронизация звука. Синтетический выступающий — latency_probe.py. Приёмник RTMP — контейнер mediamtx (площадка без интернета).
  ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox python measure_chain.py [качество=1080p] [секунд=60] [viewer=hls|rtc]"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
os.environ.setdefault("PEREGOVORKA_URL", "http://localhost:28480")
import latency_probe as P  # noqa: E402
import smoke_room as S  # noqa: E402

BASE = os.environ["PEREGOVORKA_URL"]
QUALITY = sys.argv[1] if len(sys.argv) > 1 else "1080p"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 60
MODE = sys.argv[3] if len(sys.argv) > 3 else "hls"
SIZE = {"720p": "1280x720", "1080p": "1920x1080", "1440p": "2560x1440"}[QUALITY]
RTMP_HOST, RTMP_LOCAL = os.environ.get("RTMP_HOST", "pgrtmp"), os.environ.get("RTMP_LOCAL", "rtmp://127.0.0.1:19360")


def admin() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=90)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][-1]["id"]})
    r.raise_for_status()
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def watch(stop: threading.Event, res: list) -> None:
    """Раз в минуту: процессор и память служб, число процессов браузера. По сдвигу между началом и концом видна утечка."""
    while not stop.wait(60):
        r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}} {{.MemUsage}}", "pgdoc-livekit-egress-1", "pgdoc-mediamtx-1", "pgdoc-backend-1"], capture_output=True, text=True).stdout
        n = subprocess.run(["docker", "exec", "pgdoc-livekit-egress-1", "sh", "-c", "ps -eo comm | grep -c chrome; du -sk /home/egress /dev/shm | cut -f1 | tr '\\n' ' '"], capture_output=True, text=True).stdout
        res.append({"t": int(time.time()), "службы": [" ".join(x.split()[:3]) for x in r.strip().splitlines()], "chrome_диск": " ".join(n.split())})


class Viewer(S.Client):
    async def start(self):
        try:
            await S.Client.start(self)
        except AssertionError:
            pass                                 # вход не нужен: зритель без учётной записи


async def player(token: str, viewer: str, seconds: float) -> dict:
    v = Viewer("viewer", "alice", "alice-pass", 9841)
    lags, res, dropped = [], set(), {}
    try:
        await v.start()
        await v.goto(f"/live/{token}", 1.0)
        await v.js(f"sessionStorage.setItem('pg:live:viewer:{token}', {json.dumps(viewer)}); location.reload()")
        await asyncio.sleep(3)
        await v.js("document.querySelector('.live-stage video')?.play?.()")
        if MODE == "rtc":
            print(await v.js("(()=>{const f=document.querySelector('iframe');return JSON.stringify({f:!!f,src:f&&f.src.slice(0,60),txt:f&&f.contentDocument&&f.contentDocument.body.innerText.slice(0,120),v:f&&f.contentDocument&&f.contentDocument.querySelectorAll('video').length,cls:document.querySelector('.live-stage')?.className})})()"), file=sys.stderr, flush=True)
        end = time.time() + seconds
        while time.time() < end:
            r = await v.js(P.JS_DECODER.replace("document.querySelector('.live-stage video')", "document.querySelector('iframe')?.contentDocument?.querySelector('video')") if MODE == "rtc" else P.JS_DECODER)
            if r and r.get("lag") is not None:
                lags.append(r["lag"])
                res.add(f"{r['w']}x{r['h']}")
            await asyncio.sleep(0.25)
        last = await v.js("(()=>{const e=document.querySelector('.live-stage video');const r=performance.getEntriesByType('resource').map(x=>x.name).filter(n=>/hls\\//.test(n)).slice(-4).map(n=>n.replace(/^.*\\/([^/]+)\\/\\d+\\/[^/]+\\//,'').slice(0,60));return {r,cur:e&&e.currentTime,end:e&&e.buffered.length?e.buffered.end(e.buffered.length-1):null,rate:e&&e.playbackRate,engine:e&&e.dataset.engine}})()")
        print(json.dumps(last, ensure_ascii=False), file=sys.stderr, flush=True)
        dropped = await v.js("(()=>{const q=document.querySelector('.live-stage video')?.getVideoPlaybackQuality?.();return q?{всего:q.totalVideoFrames,потеряно:q.droppedVideoFrames}:null})()") or {}
    finally:
        await v.close()
    return {"задержка_плеера_мс": P.stat(lags[len(lags) // 5:]), "разрешение": sorted(res), "кадры": dropped}


async def main() -> None:
    c = admin()
    c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3, "allow_private_destinations": True, "rtc_viewers_max": 50 if MODE == "rtc" else 0}).raise_for_status()
    slug = f"m-{time.strftime('%H%M%S')}"
    rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": QUALITY, "live_cdn": False}).json()
    token = lv["view_path"].rsplit("/", 1)[-1]
    r = c.post(f"/api/v1/rooms/{rid}/live/destinations", json={"platform": "custom", "name": "Приёмник", "server_url": f"rtmp://{RTMP_HOST}:1935/live", "key": slug})
    r.raise_for_status()
    j = c.post(f"/api/v1/rooms/{rid}/join", json={}).json()
    mid = j["meeting_id"]
    pub = subprocess.Popen([sys.executable, str(HERE / "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"], "--size", SIZE, "--seconds", str(int(SECONDS * 3 + 120))],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "PUBLISHING" in pub.stdout.readline()
    await asyncio.sleep(4)
    out: dict = {"качество": QUALITY}
    try:
        t0 = time.time()
        c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": True}).raise_for_status()
        v = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=30)
        for _ in range(240):
            if v.get(f"/api/v1/live/{token}").json()["status"] == "live":
                break
            await asyncio.sleep(0.25)
        out["запуск_до_эфира_с"] = round(time.time() - t0, 1)
        viewer = v.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Замер"}).json()["viewer"]
        acc = v.post(f"/api/v1/live/{token}/access", json={"viewer": viewer}).json()
        path = acc.get("origin_path") or acc["path"]          # обычный HLS: сегменты .ts, которые умеет разбирать проба
        out["путь_плеера"] = acc["path"].split("/")[1]
        tmp = os.path.expanduser("~/pg-work/diag")
        hls = subprocess.Popen([sys.executable, str(HERE / "latency_probe.py"), "hls", "--playlist", BASE + path, "--seconds", str(SECONDS), "--tmp", tmp], stdout=subprocess.PIPE, text=True) if MODE == "hls" else None
        rt = subprocess.Popen([sys.executable, str(HERE / "latency_probe.py"), "rtmp", "--url", f"{RTMP_LOCAL}/live/{slug}", "--seconds", str(SECONDS)], stdout=subprocess.PIPE, text=True)
        stop = threading.Event()
        res: list = []
        threading.Thread(target=watch, args=(stop, res), daemon=True).start()
        out["плеер"] = await player(token, viewer, SECONDS)
        stop.set()
        out["ресурсы"] = {"начало": res[:1], "конец": res[-1:], "замеров": len(res)}
        for name, proc in (("hls", hls), ("rtmp", rt)):
            if proc is not None:
                o = proc.communicate(timeout=SECONDS + 60)[0].strip().splitlines()
                out[name] = json.loads(o[-1]) if o else None
    finally:
        c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": False})
        await asyncio.sleep(3)
        pub.terminate()
        c.post(f"/api/v1/meetings/{mid}/end")
    print(json.dumps(out, ensure_ascii=False, indent=1))


asyncio.run(main())
