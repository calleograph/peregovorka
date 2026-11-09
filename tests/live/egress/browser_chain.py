#!/usr/bin/env python3
"""Лаборатория цепочки эфира с НАСТОЯЩИМ браузером ведущего: Chromium под виртуальным экраном (Xvfb) показывает весь экран, дальше настоящие LiveKit, Egress, MediaMTX/nginx и HLS.
Снимает на каждом участке (синтетические данные, свой стенд):
  экран → захват (track.getSettings) → исходящий WebRTC (outbound-rtp: размер, кадры/с, битрейт, причина ограничения, кодек) → канал (candidate-pair: доступная исходящая скорость, RTT, джиттер, потери)
  → нагрузка контейнеров LiveKit/Egress/web/MediaMTX (docker stats), процессор, сеть и диск сервера → сформированный HLS (ffprobe: размер, кадры/с; битрейт по размерам сегментов).
  DISPLAY=:98 (Xvfb 2560x1440: ~/pg-work/xvfb/run-xvfb.sh :98 2560x1440x24)  ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox PEREGOVORKA_URL=http://localhost:28480
  python browser_chain.py <качество 720p|1080p|1440p> <содержимое static|motion> [секунд=60] [профиль показа: по умолчанию как у сервера]
Результат: таблица в консоль и JSON в $SMOKE_OUT/chain-<качество>-<содержимое>.json"""
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import smoke_room as S  # noqa: E402

BASE = os.environ.get("PEREGOVORKA_URL", "http://localhost:28480")
QUALITY = sys.argv[1] if len(sys.argv) > 1 else "720p"
CONTENT = sys.argv[2] if len(sys.argv) > 2 else "static"
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 60
PROFILE = sys.argv[4] if len(sys.argv) > 4 else ""
DISPLAY = os.environ.get("DISPLAY", ":98")
W, H = (int(x) for x in os.environ.get("SCREEN", "2560x1440").split("x"))
CONTAINERS = ["pgdoc-livekit-1", "pgdoc-livekit-egress-1", "pgdoc-web-1", "pgdoc-mediamtx-1", "pgdoc-backend-1", "pgdoc-live-relay-1"]


def admin() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=90)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": sorted(d["sessions"], key=lambda s: s["created_at"])[0]["id"]})
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


class Presenter(S.Client):
    """Окно браузера на весь виртуальный экран (не headless): показ экрана захватывает Xvfb целиком, как у настоящего ведущего."""
    cookie = ""

    async def start(self):
        self.profile = tempfile.mkdtemp(prefix="chain-")
        flags = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required", "--auto-select-desktop-capture-source=Screen",
                 "--start-fullscreen", f"--window-size={W},{H}", "--window-position=0,0", "--disable-infobars", "--no-first-run", *os.environ.get("BROWSER_FLAGS", "").split()]
        self.proc = subprocess.Popen([S.EDGE, f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}", *flags, "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, "DISPLAY": DISPLAY})
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read()
                break
            except Exception:
                time.sleep(0.5)
        tabs = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json")) if t.get("type") == "page"]
        self.ws = await websockets.connect(tabs[0]["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None, close_timeout=2)
        await self.send("Page.enable"); await self.send("Runtime.enable"); await self.send("Network.enable")
        await self.send("Page.addScriptToEvaluateOnNewDocument", source=S.INIT)
        await self.send("Network.setCookie", name="vm_session", value=self.cookie, url=BASE, httpOnly=True)


OVERLAY = r"""(mode => {
  const c = document.createElement('canvas'); c.id = '__anim'; c.width = innerWidth; c.height = innerHeight;
  Object.assign(c.style, {position: 'fixed', inset: 0, zIndex: 99999}); document.body.append(c);
  const g = c.getContext('2d'); let t = 0;
  const frame = () => {
    t++;
    if (mode === 'static') {
      if (t === 1) { g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); g.fillStyle = '#111'; g.font = '40px sans-serif';
        for (let i = 0; i < 30; i++) g.fillText('Слайд: строка текста для проверки чёткости изображения ' + i + ' — 0123456789', 80, 80 + i * 44); }
      g.fillStyle = '#d33'; g.fillRect(c.width - 220, 20, 190, 70); g.fillStyle = '#fff'; g.fillText(String(Math.floor(t / 30) % 100), c.width - 160, 70);   // единственное движение: счётчик раз в секунду
    } else {
      for (let i = 0; i < 120; i++) { g.fillStyle = `hsl(${(t * 4 + i * 9) % 360},70%,${30 + (i % 5) * 8}%)`; g.fillRect(Math.random() * c.width, Math.random() * c.height, 40 + Math.random() * 360, 30 + Math.random() * 200); }
    }
    requestAnimationFrame(frame);
  };
  frame();
})"""

PRES_STATS = r"""(async () => {
  const out = {pcs: []};
  for (const pc of window.__pcs) { try {
    const st = await pc.getStats(), o = {out: [], rin: []}, codecs = {};
    st.forEach(r => { if (r.type === 'codec') codecs[r.id] = r.mimeType; });
    st.forEach(r => {
      if (r.type === 'outbound-rtp' && r.kind === 'video') o.out.push({rid: r.rid || '', w: r.frameWidth, h: r.frameHeight, fps: r.framesPerSecond, tgt: r.targetBitrate, bytes: r.bytesSent, frames: r.framesEncoded,
        lim: r.qualityLimitationReason, limDur: r.qualityLimitationDurations, enc: r.encoderImplementation, ct: r.contentType, codec: codecs[r.codecId], active: r.active, qpSum: r.qpSum, pkts: r.packetsSent, nack: r.nackCount});
      if (r.type === 'candidate-pair' && (r.nominated || r.state === 'succeeded') && r.availableOutgoingBitrate !== undefined) o.pair = {avail: r.availableOutgoingBitrate, rtt: r.currentRoundTripTime, sent: r.bytesSent};
      if (r.type === 'remote-inbound-rtp' && r.kind === 'video') o.rin.push({jit: r.jitter, lost: r.fractionLost, rtt: r.roundTripTime, plost: r.packetsLost});
    });
    if (o.out.length) out.pcs.push(o);
  } catch (e) {} }
  out.settings = window.__pcs.flatMap(pc => pc.getSenders()).map(s => s.track).filter(t => t && t.kind === 'video').map(t => t.getSettings());
  out.screen = {w: screen.width, h: screen.height, dpr: devicePixelRatio, iw: innerWidth, ih: innerHeight};
  return out;
})()"""


def host_counters() -> dict:
    cpu = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    net = {}
    for line in open("/proc/net/dev").read().splitlines()[2:]:
        name, v = line.split(":")
        f = v.split()
        net[name.strip()] = (int(f[0]), int(f[8]))
    disk = {}
    for line in open("/proc/diskstats").read().splitlines():
        f = line.split()
        if re.fullmatch(r"(nvme\d+n\d+|sd[a-z]+|vd[a-z]+|dm-\d+)", f[2]):
            disk[f[2]] = (int(f[5]) * 512, int(f[9]) * 512, int(f[12]), int(f[3]), int(f[7]), int(f[10]) + int(f[6]))      # прочитано, записано (байт), io_ticks (мс), чтений, записей, время ожидания (мс)
    return {"t": time.time(), "cpu": cpu, "net": net, "disk": disk}


def host_rates(a: dict, b: dict) -> dict:
    dt = b["t"] - a["t"]
    da = [y - x for x, y in zip(a["cpu"], b["cpu"])]
    idle = da[3] + da[4]
    out = {"cpu_busy_pct": round(100 * (1 - idle / max(1, sum(da))), 1)}
    for n, (rx, tx) in b["net"].items():
        if n in ("lo",) or not n.startswith(("lo", "eth", "en", "wl", "eno")) and not n.startswith("docker"):
            continue
        prx, ptx = a["net"].get(n, (rx, tx))
        out.setdefault("net_mbit", {})[n] = (round((rx - prx) * 8 / dt / 1e6, 2), round((tx - ptx) * 8 / dt / 1e6, 2))
    best = max(b["disk"], key=lambda d: b["disk"][d][1] + b["disk"][d][0], default=None)
    if best:
        pb, qb = a["disk"].get(best, b["disk"][best]), b["disk"][best]
        ops = (qb[3] - pb[3]) + (qb[4] - pb[4])
        out["disk"] = {"dev": best, "read_mb_s": round((qb[0] - pb[0]) / dt / 1e6, 2), "write_mb_s": round((qb[1] - pb[1]) / dt / 1e6, 2), "util_pct": round(min(100, (qb[2] - pb[2]) / (dt * 10)), 1),
                       "await_ms": round((qb[5] - pb[5]) / ops, 1) if ops else 0.0}
    return out


def docker_stats() -> dict:
    out = {}
    r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}", *CONTAINERS], capture_output=True, text=True, timeout=60)
    for line in r.stdout.splitlines():
        n, cpu, mem = line.split("|")
        out[n.replace("pgdoc-", "").replace("-1", "")] = {"cpu_pct": float(cpu.strip("%") or 0), "mem": mem.split("/")[0].strip()}
    return out


def hls_facts(base_path: str, seconds_back: int = 12) -> dict:
    """Сформированный HLS: размер и кадры/с (ffprobe), битрейт по размерам последних сегментов, длительность сегмента, возраст свежего."""
    pl = httpx.get(BASE + base_path, timeout=20).text
    if "#EXT-X-STREAM-INF" in pl:
        sub = [x for x in pl.splitlines() if x and not x.startswith("#")][0]
        base_path = base_path.rsplit("/", 1)[0] + "/" + sub
        pl = httpx.get(BASE + base_path, timeout=20).text
    segs, dur = [], None
    for line in pl.splitlines():
        if line.startswith("#EXTINF:"):
            dur = float(line.split(":")[1].split(",")[0])
        elif line and not line.startswith("#") and dur is not None:
            segs.append((line, dur))
            dur = None
    segs = segs[-6:]
    tot_b, tot_d = 0, 0.0
    for uri, d in segs:
        r = httpx.get(BASE + base_path.rsplit("/", 1)[0] + "/" + uri, timeout=20)
        tot_b += len(r.content)
        tot_d += d
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+4", "-show_entries", "stream=codec_name,width,height,avg_frame_rate,r_frame_rate", "-of", "json", BASE + base_path],
                        capture_output=True, text=True, timeout=60)
    try:
        s = json.loads(pr.stdout)["streams"][0]
    except Exception:  # noqa: BLE001
        s = {}
    fr = s.get("avg_frame_rate", "0/1").split("/")
    return {"w": s.get("width"), "h": s.get("height"), "codec": s.get("codec_name"), "fps_avg": round(int(fr[0]) / max(1, int(fr[1])), 1) if len(fr) == 2 else None,
            "kbps": round(tot_b * 8 / max(0.1, tot_d) / 1000), "seg_s": round(tot_d / max(1, len(segs)), 2), "segs": len(segs)}


def kbps(prev: dict | None, cur: dict, key: str = "bytes") -> float | None:
    if not prev or cur.get("t") is None or prev.get("t") is None:
        return None
    return round((cur[key] - prev[key]) * 8 / (cur["t"] - prev["t"]) / 1000, 1)


async def main():
    api = admin()
    slug = f"chain-{time.strftime('%H%M%S')}"
    rid = api.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    api.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": QUALITY, "live_cdn": False}).raise_for_status()
    api.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3, "allow_private_destinations": True, "rtc_viewers_max": 0, "ll_hls": True, "adaptive_viewers": True}).raise_for_status()
    token = api.get(f"/api/v1/rooms/{rid}/live").json()["view_path"].rsplit("/", 1)[-1]
    S.ROOM = f"/rooms/{slug}"
    p = Presenter("chain", "admin", "-", 9871)
    p.cookie = api.headers["Cookie"].split("=", 1)[1]
    res: dict = {"quality": QUALITY, "content": CONTENT, "screen_xvfb": f"{W}x{H}", "samples": []}
    mid = None
    try:
        await p.start()
        if PROFILE:
            await p.goto("/", 1.0)
            await p.js(f"localStorage.setItem('pg:screen:profile', {json.dumps(PROFILE)})")
        await p.goto(S.ROOM, 2.5)
        ok_tile = False
        for _ in range(70):                                         # вход повторяется, пока не появится плитка (страница входа монтируется не сразу; комната трансляции спрашивает роль)
            if await p.js("document.querySelectorAll('.tile').length>0"):
                ok_tile = True
                break
            await p.js("(() => { const r = [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith('Выступающий')); if (r) r.click(); document.querySelector('.pj-actions .btn.primary')?.click(); })()")
            await asyncio.sleep(1)
        S.check("ведущий вошёл в комнату, плитка появилась", ok_tile)
        if not ok_tile:
            print("текст страницы:", (await p.js("document.body.innerText.slice(0, 700)")))
            await p.shot("no-tile")
        mid = api.post(f"/api/v1/rooms/{rid}/join", json={}).json()["meeting_id"]
        labels = await p.js("[...document.querySelectorAll('button')].map(b => b.getAttribute('aria-label') || b.textContent.trim()).filter(Boolean).slice(0, 40)")
        print("кнопки комнаты:", labels)
        S.check("кнопка «Показать экран» нажата", await p.click_btn("Показать экран"))
        got = await p.wait_for("window.__pcs.some(pc => pc.getSenders().some(s => s.track && s.track.kind === 'video' && /screen|display|window/i.test((s.track.label || '') + (s.track.contentHint || ''))) || pc.getSenders().filter(s=>s.track&&s.track.kind==='video').length>0)", 30)
        S.check("показ экрана пошёл (видеодорожка в соединении)", got)
        await asyncio.sleep(2)
        await p.js(f"({OVERLAY})({json.dumps(CONTENT)})")
        r = api.post(f"/api/v1/meetings/{mid}/live", json={"enabled": True})
        if r.status_code >= 400:
            print("запуск эфира:", r.status_code, r.text[:300])
            r.raise_for_status()
        vc = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=30)
        t0 = time.time()
        for _ in range(240):
            if vc.get(f"/api/v1/live/{token}").json()["status"] == "live":
                break
            await asyncio.sleep(0.5)
        res["time_to_live_s"] = round(time.time() - t0, 1)
        viewer = vc.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Лаборатория"}).json()["viewer"]
        acc = vc.post(f"/api/v1/live/{token}/access", json={"viewer": viewer}).json()
        origin = acc.get("origin_path") or acc["path"]
        await asyncio.sleep(12)                                     # установление: кодировщик подстраивается, битрейт растёт
        h0, prev, prev_pair = host_counters(), None, None
        end = time.time() + SECONDS
        while time.time() < end:
            st = await p.js(PRES_STATS)
            now = time.time()
            h1 = host_counters()
            row = {"t": round(now - (end - SECONDS), 1), "host": host_rates(h0, h1), "screen": st.get("screen"), "capture": st.get("settings")}
            h0 = h1
            layers = []
            for pc in st.get("pcs", []):
                for o in pc["out"]:
                    o["t"] = now
                    o.setdefault("bytes", 0)
                    key = (o.get("rid", ""), o.get("codec"))
                    before = (prev or {}).get(key)
                    layers.append({"rid": o.get("rid", ""), "w": o.get("w"), "h": o.get("h"), "fps": o.get("fps"), "kbps": kbps(before, o), "target_kbps": round((o.get("tgt") or 0) / 1000),
                                   "limit": o.get("lim"), "limit_s": o.get("limDur"), "encoder": o.get("enc"), "codec": o.get("codec"), "type": o.get("ct"), "active": o.get("active")})
                    prev = {**(prev or {}), key: o}
                row["pair"] = pc.get("pair")
                row["remote"] = pc.get("rin")
            row["layers"] = layers
            res["samples"].append(row)
            await asyncio.sleep(max(0.2, 3 - (time.time() - now)))
        try:                                                          # что видит панель диагностики: цепочка у ведущего, находки и журнал
            dg = api.get(f"/api/v1/meetings/{mid}/live/diag").json()
            snap = dg.get("now") or {}
            res["diag"] = {"keys": sorted(dg.keys())[:20], "publishers": (snap.get("viewers") or {}).get("publishers"),
                           "findings": [{"code": f.get("code"), "text": f.get("text"), "cause": f.get("cause")} for f in snap.get("findings") or []],
                           "source": {k: (snap.get("source") or {}).get(k) for k in ("max_w", "max_h", "known")}, "media": snap.get("media"),
                           "events": [e.get("message") for e in dg.get("events") or []][:25], "stages": [e.get("message") for e in dg.get("stages") or []]}
        except Exception as exc:  # noqa: BLE001
            res["diag"] = {"error": str(exc)[:200]}
        res["docker"] = docker_stats()
        res["hls"] = hls_facts(origin)
        # итоги
        lay = [l for s in res["samples"][2:] for l in s["layers"] if l["w"]]
        top = [l for l in lay if l["w"] == max(x["w"] for x in lay)] if lay else []
        res["summary"] = {
            "capture": res["samples"][-1]["capture"], "screen": res["samples"][-1]["screen"],
            "sent_top": {"w": top[-1]["w"], "h": top[-1]["h"], "fps_med": statistics.median([l["fps"] or 0 for l in top]) if top else None,
                         "kbps_med": statistics.median([l["kbps"] or 0 for l in top]) if top else None, "limit": sorted({l["limit"] for l in top}), "encoder": top[-1]["encoder"], "codec": top[-1]["codec"]} if top else None,
            "avail_out_kbps_med": statistics.median([(s.get("pair") or {}).get("avail", 0) / 1000 for s in res["samples"][2:]]) if res["samples"] else None,
            "rtt_ms_med": statistics.median([((s.get("pair") or {}).get("rtt") or 0) * 1000 for s in res["samples"][2:]]),
            "hls": res["hls"], "docker": res["docker"], "host_cpu_busy_pct_med": statistics.median([s["host"]["cpu_busy_pct"] for s in res["samples"][2:]]),
            "disk_write_mb_s_med": statistics.median([(s["host"].get("disk") or {}).get("write_mb_s", 0) for s in res["samples"][2:]]),
        }
    finally:
        try:
            await p.close()
        except Exception:  # noqa: BLE001
            pass
        if mid:
            api.post(f"/api/v1/meetings/{mid}/live", json={"enabled": False})
            await asyncio.sleep(3)
            api.post(f"/api/v1/meetings/{mid}/end")
        api.delete(f"/api/v1/admin/rooms/{rid}")
    os.makedirs(S.OUT, exist_ok=True)
    path = os.path.join(S.OUT, f"chain-{QUALITY}-{CONTENT}{'-' + PROFILE if PROFILE else ''}.json")
    json.dump(res, open(path, "w"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(res.get("summary"), ensure_ascii=False, indent=1, default=str))
    print("файл:", path)


if __name__ == "__main__":
    asyncio.run(main())
