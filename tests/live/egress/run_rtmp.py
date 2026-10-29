"""Внешние площадки трансляций на НАСТОЯЩЕМ Egress и контролируемом RTMP-приёмнике (MediaMTX в Docker, порт 1936): несколько площадок одновременно, отказ одной,
включение и выключение на лету, замена ключа, обрыв приёмника посреди эфира, эфир (HLS и MP4) при этом идёт, ключи нигде не видны.
Нужны контур pgtest-* и стенд run.sh (порт 8004). Запуск: python run_rtmp.py"""
import asyncio, base64, hashlib, json, math, os, re, struct, subprocess, sys, time, urllib.error, urllib.request
import httpx
from livekit import api as lkapi, rtc
from PIL import Image, ImageDraw, ImageFont

API = os.environ.get("API", "http://127.0.0.1:8004/api/v1")
NGINX = os.environ.get("NGINX_URL", "http://127.0.0.1:18088")
LIVE_DIR = os.path.expanduser(os.environ.get("LIVE_DIR", "~/pg-work/live-test/live"))
RTMP = os.environ.get("RTMP_CONTAINER", "pgtest-rtmp")
LK = "ws://127.0.0.1:7890"
KA, KB, KC, KD = "key-alpha-7731", "key-bravo-5120", "key-charlie-8842", "key-delta-3390"
results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    print(("OK   " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""), flush=True)


def login(user, pw):
    c = httpx.Client(base_url=API, timeout=60)
    r = c.post("/auth/login", json={"login": user, "password": pw})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def probe_stream(path, seconds=6):
    """Читает поток с приёмника (RTMP на 1936): есть ли H.264 и AAC. Пусто — на приёмнике никто не публикует."""
    r = subprocess.run(["ffprobe", "-v", "error", "-rw_timeout", str(seconds * 1_000_000), "-show_entries", "stream=codec_name", "-of", "csv=p=0", f"rtmp://127.0.0.1:1936/live/{path}"],
                       capture_output=True, text=True, timeout=seconds + 15)
    return sorted(x for x in r.stdout.split() if x)


async def wait(cond, timeout=60, step=2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        await asyncio.sleep(step)
    return None


async def bot(room_name, stop):
    tok = lkapi.AccessToken("devkey", "s" * 40).with_identity("speaker-bot").with_name("Докладчик").with_grants(lkapi.VideoGrants(room_join=True, room=room_name, can_publish=True)).to_jwt()
    room = rtc.Room(); await room.connect(LK, tok)
    vs = rtc.VideoSource(1280, 720); await room.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track("cam", vs), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    asrc = rtc.AudioSource(48000, 1); await room.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("mic", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60); t0 = time.time()

    async def video():
        while not stop.is_set():
            im = Image.new("RGB", (1280, 720), (30, 60, 110)); ImageDraw.Draw(im).text((60, 280), f"t = {time.time()-t0:6.1f} c", font=font, fill=(255, 255, 255))
            vs.capture_frame(rtc.VideoFrame(1280, 720, rtc.VideoBufferType.RGBA, im.convert("RGBA").tobytes())); await asyncio.sleep(1 / 15)

    async def audio():
        k = 0
        while not stop.is_set():
            await asrc.capture_frame(rtc.AudioFrame(struct.pack("<480h", *[int(8000 * math.sin(2 * math.pi * 440 * (k + i) / 48000)) for i in range(480)]), 48000, 1, 480)); k += 480
    tasks = [asyncio.create_task(video()), asyncio.create_task(audio())]
    await stop.wait()
    for t in tasks:
        t.cancel()
    await room.disconnect()


def hls_ok(token):
    r = httpx.post(f"{API}/live/{token}/access", json={}, timeout=20)
    if r.status_code != 200:
        return None
    try:
        with urllib.request.urlopen(NGINX + r.json()["path"], timeout=10) as resp:
            return resp.status == 200 and b"#EXTM3U" in resp.read()
    except urllib.error.URLError:
        return False


async def main():
    root, alice = login("root", "root-pass"), login("alice", "alice-pass")
    r = root.put("/admin/settings/live", json={"enabled": True, "min_free_gb": 1, "max_concurrent": 1, "allow_private_destinations": True, "segment_keep_min": 3, "after_end_keep_min": 0})
    check("администратор включил трансляции и разрешил приёмник во внутренней сети (петля)", r.status_code == 200 and r.json()["allow_private_destinations"], r.text[:150])
    sales = next(x for x in root.get("/admin/rooms").json() if x["slug"] == "sales")
    room = root.post("/admin/rooms", json={"slug": "rtmp-room", "name": "Площадки", "room_type": "broadcast", "acl": sales["acl"], "moderators": sales["moderators"], "max_participants": 20}).json()
    rid = room["id"]
    live = alice.get(f"/rooms/{rid}/live").json()
    token = live["view_path"].split("/")[-1]
    # ------------------------------------------------------------------ площадки
    def add(name, url, key, platform="custom", **kw):
        return alice.post(f"/rooms/{rid}/live/destinations", json={"platform": platform, "name": name, "server_url": url, "key": key, **kw})
    ra = add("Площадка A", "rtmp://127.0.0.1:1936/live", KA, watch_url="https://watch.example.org/a")
    rb = add("Площадка B", "rtmp://127.0.0.1:1936/live", KB, platform="rutube")
    rc = add("Мёртвая C", "rtmp://127.0.0.1:19350/live", KC)
    check("три площадки добавлены", all(x.status_code == 201 for x in (ra, rb, rc)), f"{ra.status_code} {rb.status_code} {rc.status_code}")
    check("ключи не возвращаются API", all(k not in rc.text for k in (KA, KB, KC)) and all(d["has_key"] for d in rc.json()["destinations"]))
    ids = {d["name"]: d["id"] for d in rc.json()["destinations"]}
    check("внутренний адрес вне политики отклоняется: 192.168.* (приватный) при allow_private=True проходит, служебный 169.254.* — нет",
          add("метаданные", "rtmp://169.254.169.254/x", "k-meta-1111").status_code == 422)
    r = alice.post(f"/rooms/{rid}/live/destinations/probe", json={"server_url": "rtmp://127.0.0.1:1936/live"}).json()
    check("проверка сервера: приёмник отвечает по сети", r["ok"], str(r))
    r = alice.post(f"/rooms/{rid}/live/destinations/{ids['Мёртвая C']}/probe").json()
    check("проверка сервера: мёртвый порт — «не отвечает»", r["ok"] is False and "не отвечает" in r["message"], str(r))
    # ------------------------------------------------------------------ встреча и эфир
    j = alice.post(f"/rooms/{rid}/join", json={}).json(); mid, lk_room = j["meeting_id"], j["livekit_room"]
    stop = asyncio.Event()
    bt = asyncio.create_task(bot(lk_room, stop))
    await asyncio.sleep(4)
    t0 = time.time()
    r = alice.post(f"/meetings/{mid}/live", json={"enabled": True})
    check("эфир запущен с тремя площадками", r.status_code == 200, r.text[:200])

    def dstate():
        return {d["name"]: d for d in alice.get(f"/meetings/{mid}/live").json().get("destinations", [])}
    st = await wait(lambda: (lambda s: s if s.get("Площадка A", {}).get("status") == "live" and s.get("Площадка B", {}).get("status") == "live" and s.get("Мёртвая C", {}).get("status") == "failed" else None)(dstate()), 60)
    check("A и B — «идёт», C — «ошибка» (отказ одной площадки не мешает остальным)", bool(st), json.dumps({k: v["status"] for k, v in dstate().items()}, ensure_ascii=False))
    if st:
        check("у площадки C показана причина без ключа", bool(st["Мёртвая C"]["last_error"]) and KC not in st["Мёртвая C"]["last_error"], str(st["Мёртвая C"]["last_error"]))
        check("у площадки A есть время последнего успешного подключения", bool(st["Площадка A"]["last_ok_at"]))
    check("приёмник получает A: H.264 + AAC", probe_stream(KA) == ["aac", "h264"], str(probe_stream(KA)))
    check("приёмник получает B: H.264 + AAC", probe_stream(KB) == ["aac", "h264"])
    check("собственный просмотр HLS идёт параллельно (плейлист 200)", bool(await wait(lambda: hls_ok(token), 40, 3)))
    # ------------------------------------------------------------------ «на лету»
    r = alice.post(f"/meetings/{mid}/live/destinations/{ids['Площадка A']}", json={"enabled": False})
    check("площадка A выключена во время эфира", r.status_code == 200, r.text[:160])
    await asyncio.sleep(6)
    check("на приёмнике A больше нет, B продолжает", probe_stream(KA, 4) == [] and probe_stream(KB) == ["aac", "h264"])
    check("эфир (HLS) не прерывался", bool(hls_ok(token)) and alice.get(f"/meetings/{mid}/live").json()["live"] is True)
    r = alice.post(f"/rooms/{rid}/live/destinations", json={"platform": "custom", "name": "Площадка D", "server_url": "rtmp://127.0.0.1:1936/live", "key": KD})
    did = [d for d in r.json()["destinations"] if d["name"] == "Площадка D"][0]["id"]
    alice.post(f"/meetings/{mid}/live/destinations/{did}", json={"enabled": False}); await asyncio.sleep(1)
    r = alice.post(f"/meetings/{mid}/live/destinations/{did}", json={"enabled": True})
    check("новая площадка D подключена во время эфира", r.status_code == 200 and bool(await wait(lambda: dstate().get("Площадка D", {}).get("status") == "live", 40)), json.dumps({k: v["status"] for k, v in dstate().items()}, ensure_ascii=False))
    check("на приёмнике D есть", probe_stream(KD) == ["aac", "h264"])
    r = alice.post(f"/meetings/{mid}/live/destinations/{ids['Площадка A']}", json={"enabled": True})
    check("A включена снова — новое подключение", r.status_code == 200 and bool(await wait(lambda: dstate().get("Площадка A", {}).get("status") == "live", 40)) and probe_stream(KA) == ["aac", "h264"])
    # замена адреса и ключа у мёртвой C и повторное включение
    alice.patch(f"/rooms/{rid}/live/destinations/{ids['Мёртвая C']}", json={"server_url": "rtmp://127.0.0.1:1936/live", "key": KC})
    alice.post(f"/meetings/{mid}/live/destinations/{ids['Мёртвая C']}", json={"enabled": False})
    await asyncio.sleep(2)
    r = alice.post(f"/meetings/{mid}/live/destinations/{ids['Мёртвая C']}", json={"enabled": True})
    check("C: после исправления адреса площадка подключилась", r.status_code == 200 and bool(await wait(lambda: dstate().get("Мёртвая C", {}).get("status") == "live", 40)) and probe_stream(KC) == ["aac", "h264"], json.dumps({k: v["status"] for k, v in dstate().items()}, ensure_ascii=False))
    # ------------------------------------------------------------------ обрыв приёмника
    print("   приёмник остановлен посреди эфира…", flush=True)
    subprocess.run(["docker", "stop", RTMP], capture_output=True)
    down = await wait(lambda: all(dstate().get(n, {}).get("status") == "failed" for n in ("Площадка A", "Площадка B", "Площадка D", "Мёртвая C")), 90, 3)
    check("потеря соединения с площадками: все показаны «ошибка» с причиной", bool(down), json.dumps({k: v["status"] for k, v in dstate().items()}, ensure_ascii=False))
    s = alice.get(f"/meetings/{mid}/live").json()
    check("эфир и собственный просмотр при этом продолжаются", s["live"] is True and bool(hls_ok(token)), str(s["status"]))
    check("конференция не затронута (встреча идёт)", alice.get(f"/meetings/{mid}/live").status_code == 200)
    subprocess.run(["docker", "start", RTMP], capture_output=True)
    await asyncio.sleep(5)
    alice.post(f"/meetings/{mid}/live/destinations/{ids['Площадка B']}", json={"enabled": False}); await asyncio.sleep(2)
    r = alice.post(f"/meetings/{mid}/live/destinations/{ids['Площадка B']}", json={"enabled": True})
    check("после возвращения приёмника площадка включается заново", r.status_code == 200 and bool(await wait(lambda: dstate().get("Площадка B", {}).get("status") == "live", 40)) and probe_stream(KB) == ["aac", "h264"])
    # ------------------------------------------------------------------ остановка и утечки
    r = alice.post(f"/meetings/{mid}/live", json={"enabled": False})
    check("эфир остановлен", r.status_code == 200)
    await asyncio.sleep(12)
    fin = {k: v["status"] for k, v in dstate().items()} if alice.get(f"/meetings/{mid}/live").status_code == 200 else {}
    root_state = alice.get(f"/rooms/{rid}/live").json()
    check("после эфира нет «идущих» площадок (завершена/ошибка)", all(d["status"] in ("finished", "failed", "idle") for d in root_state["destinations"]), json.dumps({d["name"]: d["status"] for d in root_state["destinations"]}, ensure_ascii=False))
    check("на приёмнике никого нет", probe_stream(KA, 4) == [] and probe_stream(KB, 4) == [] and probe_stream(KD, 4) == [])
    stop.set(); await bt
    blob = json.dumps([root_state, alice.get(f"/rooms/{rid}/live").json()], ensure_ascii=False)
    audit = root.get("/admin/audit?limit=200").text
    journal = root.get("/admin/journal?limit=200").text if root.get("/admin/journal?limit=1").status_code == 200 else ""
    stand_log = open(os.path.expanduser("~/pg-work/live-stand.log"), errors="ignore").read()
    egress_log = subprocess.run(["docker", "logs", "pgtest-egress-live"], capture_output=True, text=True).stdout
    leaks = {name: [k for k in (KA, KB, KC, KD) if k in text] for name, text in (("ответы API", blob), ("аудит", audit), ("журнал", journal), ("журнал стенда", stand_log))}
    check("ключи не попали в ответы API, аудит, журнал и журнал стенда", not any(leaks.values()), str(leaks))
    eg_leak = [k for k in (KA, KB, KC, KD) if k in egress_log]
    print("   (ключи в журнале самого Egress:", eg_leak or "нет", ")", flush=True)
    data_dir = open(os.path.expanduser("~/pg-work/live-test/current-data")).read().strip() if os.path.exists(os.path.expanduser("~/pg-work/live-test/current-data")) else ""
    dbleak = []
    for root_dir, _d, files in os.walk(os.path.expanduser("~/pg-work/live-test")):
        for fn in files:
            if fn.endswith(".db"):
                data = open(os.path.join(root_dir, fn), "rb").read()
                dbleak += [k for k in (KA, KB, KC, KD) if k.encode() in data]
    check("в базе ключи только в зашифрованном виде", not dbleak, str(dbleak))
    bad = [r for r in results if not r[1]]
    print(f"ИТОГО: {len(results) - len(bad)} из {len(results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
