"""CDN и защита ресурсов на НАСТОЯЩИХ LiveKit + Egress + nginx: перед источником стоит кэширующий прокси (nginx proxy_cache, HTTPS, свой домен — контейнер pgtest-cdn),
три зрителя смотрят эфир только через него; считаем, сколько запросов дошло до источника; отзыв ссылки; предел нагрузки Egress.
Нужны контур pgtest-*, стенд run.sh (SCRIPT=run_cdn.py) и ~/pg-work/cdn-test (cdn.conf, cert.pem). Домен CDN — cdn.localhost (Chromium направляет *.localhost на 127.0.0.1)."""
import asyncio, json, math, os, re, struct, subprocess, sys, time, urllib.error, urllib.request
import httpx
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("PEREGOVORKA_URL", "http://127.0.0.1:5186")
os.environ["BROWSER_FLAGS"] = (os.environ.get("BROWSER_FLAGS", "") + " --ignore-certificate-errors").strip()
import smoke_room as S
from livekit import api as lkapi, rtc
from PIL import Image, ImageDraw, ImageFont

API = "http://127.0.0.1:8004/api/v1"
CDN = "https://cdn.localhost:18443"           # *.localhost Chromium сам отправляет на 127.0.0.1
check = S.check


class Anon(S.Client):
    async def start(self):
        await super().start()
        await self.send("Network.clearBrowserCookies")


def api_login(user, pw):
    c = httpx.Client(base_url=API, timeout=60)
    r = c.post("/auth/login", json={"login": user, "password": pw})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


async def bot(room_name, stop):
    tok = lkapi.AccessToken("devkey", "s" * 40).with_identity("speaker-bot").with_name("Докладчик").with_grants(lkapi.VideoGrants(room_join=True, room=room_name, can_publish=True)).to_jwt()
    room = rtc.Room(); await room.connect("ws://127.0.0.1:7890", tok)
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


def cdn_log():
    out = subprocess.run(["docker", "exec", "pgtest-cdn", "cat", "/var/log/nginx/cdn.log"], capture_output=True, text=True).stdout
    rows = [l.split(" ", 3) for l in out.splitlines() if " /hls/" in l and "healthz" not in l]
    return rows            # [время, статус, кэш, адрес]


async def main():
    root, alice = api_login("root", "root-pass"), api_login("alice", "alice-pass")
    r = root.put("/admin/settings/live", json={"enabled": True, "min_free_gb": 1, "max_concurrent": 1, "cdn_enabled": True, "cdn_base_url": CDN, "egress_health_url": "http://127.0.0.1:8092/", "max_cpu_load": 85})
    check("администратор подключил CDN и health-порт Egress", r.status_code == 200 and r.json()["cdn_base_url"] == CDN, r.text[:160])
    sales = next(x for x in root.get("/admin/rooms").json() if x["slug"] == "sales")
    room = root.post("/admin/rooms", json={"slug": "cdn-room", "name": "Большой вебинар", "room_type": "broadcast", "acl": sales["acl"], "moderators": sales["moderators"], "max_participants": 20}).json()
    rid = room["id"]
    st = alice.get(f"/rooms/{rid}/live").json()
    check("в комнате доступен переключатель CDN", st["cdn_available"] is True and st["cdn"] is False)
    st = alice.patch(f"/rooms/{rid}/live", json={"live_cdn": True}).json()
    check("владелец включил раздачу через CDN; ссылка на поток — на адресе CDN", st["cdn"] is True and st["stream_path"].startswith(CDN + "/hls/"), str(st["stream_path"])[:80])
    token = st["view_path"].split("/")[-1]
    j = alice.post(f"/rooms/{rid}/join", json={}).json(); mid, lk_room = j["meeting_id"], j["livekit_room"]
    stop = asyncio.Event(); bt = asyncio.create_task(bot(lk_room, stop)); await asyncio.sleep(4)
    r = alice.post(f"/meetings/{mid}/live", json={"enabled": True})
    check("эфир запущен (нагрузка Egress ниже предела)", r.status_code == 200, r.text[:200])
    for _ in range(40):
        if alice.get(f"/meetings/{mid}/live").json()["hls_ready"]:
            break
        await asyncio.sleep(2)
    # ------------------------------------------------------------------ три зрителя через CDN
    V = [Anon(f"cdn{i}", "alice", "alice-pass", 9760 + i) for i in range(3)]
    try:
        for v in V:
            await v.start()
        t_before = len(cdn_log())
        for v in V:
            await v.goto(f"/live/{token}", 2.0)
        ok = [await v.wait_for("(()=>{const x=document.querySelector('.live-stage video');return !!x&&x.videoWidth>0&&x.currentTime>3})()", 90) for v in V]
        check("три зрителя смотрят эфир через CDN", all(ok), str(ok))
        res = await V[0].js("performance.getEntriesByType('resource').map(e=>e.name).filter(n=>n.includes('/hls/'))")
        check("видео загружается с адреса CDN, а не с сервера страницы", bool(res) and all(n.startswith(CDN + "/hls/") for n in res), str(res[:2]))
        errs = await V[0].js("JSON.stringify(window.__errs||[])")
        check("политика безопасности страницы /live/… не блокирует CDN (нет ошибок плеера)", "Content Security Policy" not in errs and "blocked" not in errs.lower(), errs[:200])
        await asyncio.sleep(25)
        rows = cdn_log()[t_before:]
        segs = [r for r in rows if r[3].strip().endswith(".ts")]
        pl = [r for r in rows if "index.m3u8" in r[3]]
        miss = sum(1 for r in segs if r[2] == "MISS"); hit = sum(1 for r in segs if r[2] in ("HIT", "UPDATING", "STALE"))
        uniq = len({r[3].strip().split("?")[0] for r in segs})
        print(f"   запросов сегментов: {len(segs)} (разных {uniq}); из них MISS {miss}, HIT {hit}; запросов плейлиста: {len(pl)}", flush=True)
        check("сегменты кэшируются: каждый уникальный сегмент дошёл до источника не более одного раза (≈ 3 зрителя → 1 запрос к источнику)", len(segs) >= 2 * uniq and miss <= uniq + 2 and hit >= miss, f"segs={len(segs)} uniq={uniq} miss={miss} hit={hit}")
        # ------------------------------------------------------------------ отзыв ссылки: плейлист закрывается быстро, закэшированные сегменты — по сроку кэша CDN
        old = (await V[0].js(f"fetch('/api/v1/live/{token}/access',{{method:'POST',headers:{{'content-type':'application/json'}},body:'{{}}'}}).then(r=>r.json())"))
        pl_url = CDN + old["path"]
        ctx = __import__("ssl")._create_unverified_context()

        def code(u):
            try:
                with urllib.request.urlopen(urllib.request.Request(u.replace("cdn.localhost", "127.0.0.1"), headers={"Host": "cdn.localhost:18443"}), timeout=10, context=ctx) as rr:
                    return rr.status
            except urllib.error.HTTPError as e:
                return e.code
        check("плейлист через CDN открывается", code(pl_url) == 200)
        ctx2 = __import__("ssl")._create_unverified_context()
        with urllib.request.urlopen(urllib.request.Request(pl_url.replace("cdn.localhost", "127.0.0.1"), headers={"Host": "cdn.localhost:18443"}), timeout=10, context=ctx2) as rr:
            seg = re.findall(r"seg_\d+\.ts", rr.read().decode())[-1]
        seg_url = pl_url.rsplit("/", 1)[0] + "/" + seg
        code(seg_url)                                     # сегмент попал в кэш CDN
        r = alice.post(f"/rooms/{rid}/live/links/view/revoke")
        await asyncio.sleep(3)
        check("после отзыва ссылки плейлист через CDN перестаёт отдаваться в пределах нескольких секунд (404)", code(pl_url) == 404, str(code(pl_url)))
        print(f"   сегмент, уже лежащий в кэше CDN, после отзыва ссылки: HTTP {code(seg_url)} (кэш CDN живёт до пяти минут; новых сегментов по отозванной ссылке получить нельзя)", flush=True)
        check("новая ссылка зрителя выпущена, старая страница — «не найдена»", r.status_code == 200 and (await V[1].js(f"fetch('/api/v1/live/{token}').then(r=>r.status)")) == 404)
        # ------------------------------------------------------------------ предел нагрузки
        r = alice.post(f"/meetings/{mid}/live", json={"enabled": False}); await asyncio.sleep(10)
        check("эфир остановлен", r.status_code == 200)
        st = root.get("/admin/live/status").json()
        check("в администрировании видна нагрузка Egress и CDN", st["egress_cpu_load"] is not None and st["cdn"]["enabled"] is True, f"{st['egress_cpu_load']}")
        load = st["egress_cpu_load"]
        root.put("/admin/settings/live", json={"max_cpu_load": 10})
        r = alice.post(f"/meetings/{mid}/live", json={"enabled": True})
        if load is not None and load >= 10:
            check("при нагрузке Egress выше предела новый эфир не начинается, причина названа", r.status_code == 409 and r.json()["detail"]["code"] == "server_busy", f"{load}% {r.text[:160]}")
        else:
            print(f"   нагрузка {load}% ниже минимального предела 10%: проверка предела пропущена", flush=True)
        root.put("/admin/settings/live", json={"max_cpu_load": 85})
        r = alice.post(f"/meetings/{mid}/live", json={"enabled": True})
        check("при нормальном пределе эфир снова запускается", r.status_code == 200, r.text[:160])
        await asyncio.sleep(5)
        alice.post(f"/meetings/{mid}/live", json={"enabled": False})
    finally:
        stop.set(); await bt
        for v in V:
            try:
                await v.close()
            except Exception:
                pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
