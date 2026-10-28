"""Реальная проверка видеозаписи: настоящие LiveKit + Egress (изолированный контур pgtest-*), два браузера с фейковыми камерами, «докладчик» (livekit.rtc)
публикует демонстрацию экрана 1920×1080 с мелким текстом. Фазы: 15 с только участники → 20 с показ экрана → 10 с снова только участники.
Запуск: python run_video.py <720p|1080p> [секунд_всего]"""
import asyncio, io, json, os, subprocess, sys, time, threading, uuid
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("PEREGOVORKA_URL", "http://127.0.0.1:5184")
import smoke_room as S
from livekit import api as lkapi, rtc
from PIL import Image, ImageDraw, ImageFont

QUALITY = sys.argv[1] if len(sys.argv) > 1 else "720p"
REC = os.environ.get("REC_DIR", os.path.expanduser("~/egress-test/rec"))
OUT = os.path.expanduser(f"~/egress-test/{os.environ.get('OUT_PREFIX', 'out')}-{QUALITY}")
os.makedirs(OUT, exist_ok=True)
LK = os.environ.get("LK_URL", "ws://127.0.0.1:7890")


async def api(c, method, path, body=None):
    js = (f"(async()=>{{const me=await (await fetch('/api/v1/auth/me')).json();const r=await fetch('/api/v1{path}',{{method:'{method}',headers:{{'content-type':'application/json',"
          f"'X-CSRF-Token':me.csrf_token||''}},body:{json.dumps(json.dumps(body)) if body is not None else 'undefined'}}});let j=null;try{{j=await r.json()}}catch(e){{}}return {{s:r.status,j}}}})()")
    return await c.js(js)


def slide(t: float) -> bytes:
    """Слайд 1920×1080: заголовок, абзац и «код» мелким шрифтом (проверка читаемости), движущаяся метка времени."""
    im = Image.new("RGB", (1920, 1080), (250, 250, 252))
    d = ImageDraw.Draw(im)
    big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54)
    mid = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 15)
    d.text((70, 50), "Квартальный план: сертификаты и бюджет", font=big, fill=(20, 30, 60))
    d.text((70, 140), "Читаемость мелкого текста при записи демонстрации экрана", font=mid, fill=(60, 60, 80))
    for i in range(44):
        d.text((70, 220 + i * 19), f"{i:02d}  def renew_certificate(host, days_left={i * 3}):  # срок действия {30 - i % 30} дн., заказ #{4000 + i * 7}", font=small, fill=(30, 30, 30))
    d.rectangle((1250, 220, 1850, 700), outline=(40, 80, 200), width=4)
    x = 1260 + int((t * 80) % 560)
    d.rectangle((x, 400, x + 30, 430), fill=(220, 60, 60))
    d.text((1270, 240), f"t = {t:6.1f} c", font=big, fill=(40, 80, 200))
    return im.convert("RGBA").tobytes()


async def screen_bot(room_name: str, start_after: float, duration: float):
    token = (lkapi.AccessToken("devkey", "s" * 40).with_identity("slides-bot").with_name("Докладчик")
             .with_grants(lkapi.VideoGrants(room_join=True, room=room_name, can_publish=True)).to_jwt())
    room = rtc.Room()
    await room.connect(LK, token)
    await asyncio.sleep(start_after)
    src = rtc.VideoSource(1920, 1080)
    track = rtc.LocalVideoTrack.create_video_track("screen", src)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE, video_encoding=rtc.VideoEncoding(max_bitrate=3_000_000, max_framerate=10)))
    t0 = time.time()
    while time.time() - t0 < duration:
        frame = rtc.VideoFrame(1920, 1080, rtc.VideoBufferType.RGBA, slide(time.time() - t0))
        src.capture_frame(frame)
        await asyncio.sleep(0.1)
    await room.local_participant.unpublish_track(track.sid)
    await asyncio.sleep(1)
    await room.disconnect()


def sample_stats(stop: threading.Event, out: list):
    while not stop.is_set():
        r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", os.environ.get("EG_CONTAINER", "pgtest-egress")], capture_output=True, text=True)
        if r.stdout.strip():
            cpu, mem = r.stdout.strip().split("|")
            out.append((time.time(), float(cpu.strip("%")), mem.split("/")[0].strip()))


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9731)
    B = S.Client("bob", "bob", "bob-pass", 9732)
    R = S.Client("root", "root", "root-pass", 9733)
    try:
        await A.start(); await B.start(); await R.start()
        r = await api(R, "PUT", "/admin/settings/video", {"enabled": True, "min_free_gb": 1, "max_concurrent": 2})
        S.check("администратор включил видеозапись", r["s"] == 200, str(r)[:120])
        rooms = (await api(R, "GET", "/admin/rooms"))["j"]
        room = next(x for x in rooms if x["slug"] == "sales")
        r = await api(R, "PATCH", f"/rooms/{room['id']}/manage", {"video_mode": "manual", "video_quality": QUALITY})
        S.check(f"режим «вручную», качество {QUALITY}", r["s"] == 200 and r["j"]["video_quality"] == QUALITY, str(r)[:150])
        await A.goto("/rooms/sales", 2.0); S.check("Алиса вошла", await A.join(navigate=False))
        await B.goto("/rooms/sales", 2.0); S.check("Борис вошёл", await B.join(navigate=False))
        await asyncio.sleep(2)
        for c in (A, B):
            await c.wait_for("!!document.querySelector('button[aria-label=\"Камера выкл.\"]')", 20)
            S.check(f"камера включена ({c.label})", bool(await c.click_btn("Камера выкл.")))
        await asyncio.sleep(2)
        await asyncio.sleep(4)
        mid = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>x.room_name==='Продажи'&&!x.ended_at)||{}).id})()")
        room_name = "m-" + mid.replace("-", "")
        bot = asyncio.create_task(screen_bot(room_name, 15, 20))
        stats: list = []
        stop = threading.Event()
        th = threading.Thread(target=sample_stats, args=(stop, stats), daemon=True)
        th.start()
        t_start = time.time()
        r = await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": True})
        S.check("запись началась: 200", r["s"] == 200, str(r)[:200])
        S.check("у Бориса индикатор «ВИДЕОЗАПИСЬ»", await B.wait_for("[...document.querySelectorAll('.rec-badge')].some(b=>b.textContent.includes('ВИДЕОЗАПИСЬ'))", 20))
        await asyncio.sleep(max(0, 45 - (time.time() - t_start)))
        r = await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": False})
        S.check("остановка: 200", r["s"] == 200, str(r)[:200])
        t_stop = time.time()
        ready = None
        for _ in range(60):
            m = await api(A, "GET", f"/meetings/{mid}/media")
            vids = [x for x in m["j"]["mixes"] if x["has_video"]]
            if vids and vids[-1]["status"] in ("ready", "failed"):
                ready = vids[-1]; break
            await asyncio.sleep(2)
        stop.set(); th.join(timeout=5)
        await bot
        S.check("файл готов (ready)", bool(ready) and ready["status"] == "ready", json.dumps(ready, ensure_ascii=False, default=str)[:300])
        if ready:
            S.check("итог после остановки ≤ 30 с", time.time() - t_stop < 30, f"{time.time() - t_stop:.0f} с")
            row = (await api(R, "GET", f"/meetings/{mid}/media"))["j"]["mixes"][-1]
            files = subprocess.run(["find", REC, "-name", "video-*.mp4", "-newer", "/tmp/claude-1000/egress_marker"], capture_output=True, text=True).stdout.split("\n")
            path = [f for f in files if f][-1]
            pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,profile,width,height,r_frame_rate,bit_rate,sample_rate:format=duration,size,bit_rate", "-of", "json", path], capture_output=True, text=True)
            info = json.loads(pr.stdout)
            v = next(s for s in info["streams"] if s["codec_type"] == "video"); a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
            size, dur = int(info["format"]["size"]), float(info["format"]["duration"])
            print("ФАЙЛ:", path)
            print(json.dumps({"video": {k: v.get(k) for k in ("codec_name", "profile", "width", "height", "r_frame_rate")}, "audio": a and {k: a.get(k) for k in ("codec_name", "sample_rate")},
                              "duration_s": round(dur, 1), "size_bytes": size, "avg_kbps": round(size * 8 / dur / 1000), "GB_per_hour": round(size / dur * 3600 / 1e9, 2),
                              "db_duration_s": ready.get("duration_s"), "db_size": ready.get("size_bytes")}, ensure_ascii=False))
            S.check("H.264 + AAC, MP4", v["codec_name"] == "h264" and a is not None and a["codec_name"] == "aac", str(a))
            w, h = (1280, 720) if QUALITY == "720p" else (1920, 1080)
            S.check(f"разрешение {w}×{h}", (v["width"], v["height"]) == (w, h), f"{v['width']}x{v['height']}")
            S.check("длительность ≈ 45 с (±8)", abs(dur - 45) < 8, f"{dur:.1f}")
            S.check("размер и длительность в базе совпадают с файлом", abs((ready.get("size_bytes") or 0) - size) < 4096 and abs((ready.get("duration_s") or 0) - dur) < 3, f"{ready.get('size_bytes')} vs {size}")
            for sec in (7, 25, 40):
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(sec), "-i", path, "-frames:v", "1", f"{OUT}/frame-{sec}s.png"])
            if stats:
                cpus = [c for _, c, _ in stats]
                print("НАГРУЗКА egress:", json.dumps({"samples": len(stats), "cpu_avg_%": round(sum(cpus) / len(cpus)), "cpu_max_%": round(max(cpus)), "mem_last": stats[-1][2], "mem_max": max(stats, key=lambda x: float(x[2].replace('GiB', '000').replace('MiB', '').replace('KiB', '0')))[2]}, ensure_ascii=False))
            # через приложение: воспроизведение с Range
            rng = await api(A, "GET", f"/meetings/{mid}/media")
            rid = ready["id"]
            rr = await A.js(f"fetch('/api/v1/meetings/{mid}/media/{rid}/stream',{{headers:{{Range:'bytes=0-99'}}}}).then(r=>({{s:r.status,ct:r.headers.get('content-type')}}))")
            S.check("отдача записи через приложение с Range: 206 video/mp4", rr["s"] == 206 and rr["ct"] == "video/mp4", str(rr))
    finally:
        for c in (A, B, R):
            try: await c.close()
            except Exception: pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
