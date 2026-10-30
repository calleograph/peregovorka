"""Одна учётная запись — несколько одновременных подключений к одной комнате на НАСТОЯЩЕМ LiveKit (тестовый экземпляр): вкладка браузера, дубликат вкладки, приложение с идентификатором
установки, приложение без идентификатора (старые сборки), потеря сети, отключение одного из подключений.

Проверяется: у каждого подключения свой участник LiveKit, ни одно не закрывается чужим входом, на странице нет сообщения «уже вошла другая вкладка», отключение одного не трогает остальные,
после обрыва сети вкладка возвращается тем же участником, демонстрация экрана с одного подключения и голос с другого существуют независимо.
Запуск: PEREGOVORKA_URL=http://localhost:28480 PG_ADMIN_PASSWORD=… BROWSER_PATH=… python3 tests/live/multidevice_identity.py"""
import asyncio, json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import websockets
import smoke_room as S
from livekit import api as lkapi, rtc

URL = os.environ.get("PEREGOVORKA_URL", "http://localhost:28480"); PASSWORD = os.environ["PG_ADMIN_PASSWORD"]; ROOM = os.environ.get("PG_ROOM", "upd-check")
LK_WS = os.environ.get("LK_WS", "ws://127.0.0.1:27880")
ENVF = os.path.expanduser(os.environ.get("PG_ENV", "~/pg-work/docstand/.env"))
env = dict(l.split("=", 1) for l in open(ENVF).read().splitlines() if "=" in l and not l.startswith("#"))
KEY, SECRET = env["LIVEKIT_API_KEY"].strip().strip('"'), env["LIVEKIT_API_SECRET"].strip().strip('"')
S.HOST = URL
NODEVICES = "navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('нет устройств', 'NotFoundError')); navigator.mediaDevices.enumerateDevices = async () => [];"


class Api:
    """Приложение без браузера: свой сеанс входа (cookie хранится вручную — сервер за HTTPS-прокси), свой идентификатор установки (или без него)."""
    def __init__(self, client_id): self.cid, self.cookie, self.csrf = client_id, "", ""

    def call(self, method, path, body=None):
        h = {"Origin": "https://meet.example.org:28443", "Content-Type": "application/json", "X-Forwarded-Proto": "https", "User-Agent": "PeregovorkaApp/0.1 (Android; Mobile)"}
        if self.cid: h["X-Client-Id"] = self.cid
        if self.cookie: h["Cookie"] = self.cookie
        if self.csrf: h["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(URL + "/api/v1" + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                for c in r.headers.get_all("Set-Cookie") or []:
                    k, v = c.split(";", 1)[0].split("=", 1); self.cookie = f"{k}={v}"
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, e.read()[:200]

    def login(self):
        c, b = self.call("POST", "/auth/login", {"login": "admin", "password": PASSWORD}); assert c == 200, (c, b); self.csrf = b.get("csrf_token", "")

    def join(self):
        c, b = self.call("POST", f"/rooms/{ROOM}/join", {}); assert c == 200, (c, b); return b


async def participants(name):
    async with lkapi.LiveKitAPI(LK_WS.replace("ws://", "http://"), KEY, SECRET) as lk:
        return {p.identity: p for p in (await lk.room.list_participants(lkapi.ListParticipantsRequest(room=name))).participants}


def mine(ps): return sorted(i for i in ps if i.startswith("u-"))


async def connect(join, tracks=()):
    room = rtc.Room()
    await room.connect(LK_WS, join["token"])
    for kind in tracks:
        if kind == "screen":
            src = rtc.VideoSource(640, 360); t = rtc.LocalVideoTrack.create_video_track("screen", src)
            await room.local_participant.publish_track(t, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE))
            asyncio.create_task(feed(src))
        else:
            src = rtc.AudioSource(48000, 1, queue_size_ms=100); t = rtc.LocalAudioTrack.create_audio_track("mic", src)
            await room.local_participant.publish_track(t, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            asyncio.create_task(tone(src))
    return room


async def feed(src):
    buf = bytes((40, 90, 200, 255)) * (640 * 360)
    while True:
        src.capture_frame(rtc.VideoFrame(640, 360, rtc.VideoBufferType.RGBA, buf)); await asyncio.sleep(0.2)


async def tone(src):
    import math, struct
    k = 0
    while True:
        pcm = struct.pack("<" + "h" * 480, *[int(3000 * math.sin(2 * math.pi * 300 * (k * 480 + j) / 48000)) for j in range(480)]); k += 1
        await src.capture_frame(rtc.AudioFrame(pcm, 48000, 1, 480))


async def second_tab(A, port):
    """Дубликат вкладки: window.open копирует sessionStorage (и идентификатор вкладки), вкладка живёт в том же браузере (общий BroadcastChannel)."""
    before = {t["id"] for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json")) if t["type"] == "page"}
    await A.js("window.open(location.href, '_blank'); 1")
    await asyncio.sleep(2)
    tabs = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json")) if t["type"] == "page" and t["id"] not in before]
    B = S.Client("tab2", "admin", PASSWORD, port); B.n = 0
    B.ws = await websockets.connect(tabs[0]["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None, close_timeout=2)
    for m in ("Page.enable", "Runtime.enable", "Network.enable"): await B.send(m)
    return B


async def main():
    A = S.Client("tabA", "admin", PASSWORD, 9651)
    apps = []
    try:
        await A.start()
        await A.send("Page.addScriptToEvaluateOnNewDocument", source=NODEVICES)
        await A.goto(f"/rooms/{ROOM}", 2.0)
        S.check("вкладка A вошла в комнату", await A.join(navigate=False))
        await asyncio.sleep(3)
        async with lkapi.LiveKitAPI(LK_WS.replace("ws://", "http://"), KEY, SECRET) as lk:
            rooms = (await lk.room.list_rooms(lkapi.ListRoomsRequest())).rooms
        lkroom = next(r.name for r in rooms if r.num_participants > 0)
        ids_a = mine(await participants(lkroom)); S.check("в комнате один участник пользователя (вкладка A)", len(ids_a) == 1, str(ids_a))
        cid_a = await A.js("sessionStorage.getItem('pg.clientId')")

        # дубликат вкладки с тем же sessionStorage
        B = await second_tab(A, 9651)
        await B.js(f"sessionStorage.setItem('pg.clientId','{cid_a}')") if False else None       # window.open уже скопировал идентификатор; BroadcastChannel заменит его в более поздней вкладке
        await asyncio.sleep(1.5)
        cid_b = await B.js("sessionStorage.getItem('pg.clientId')")
        S.check("дубликат вкладки получил другой идентификатор (занятый идентификатор не используется дважды)", cid_b and cid_b != cid_a, f"{cid_a[:6]}… / {str(cid_b)[:6]}…")
        await B.send("Page.navigate", url=f"{URL}/rooms/{ROOM}"); await asyncio.sleep(2)
        await B.send("Page.addScriptToEvaluateOnNewDocument", source=NODEVICES)
        S.check("вторая вкладка вошла в ту же комнату", await B.join(navigate=False))
        await asyncio.sleep(3)
        ids = mine(await participants(lkroom)); S.check("два участника: обе вкладки подключены, ни одна не закрыта", len(ids) == 2, str(ids))

        # приложения: с идентификатором установки и без (старая сборка) — свои сеансы входа
        new_app, old_app = Api("app-0123456789abcdef0123"), Api("")
        for a in (new_app, old_app): a.login()
        jn, jo = new_app.join(), old_app.join()
        S.check("у приложения без идентификатора — собственный участник, не совпадающий со вкладками", jo["identity"] not in ids and jn["identity"] not in ids and jo["identity"] != jn["identity"], f"{jo['identity'][-10:]} {jn['identity'][-10:]}")
        rn = await connect(jn, ["mic"]); ro = await connect(jo, ["screen"]); apps = [rn, ro]
        await asyncio.sleep(5)
        ps = await participants(lkroom); ids = mine(ps)
        S.check("четыре независимых подключения одного пользователя", len(ids) == 4, str(ids))
        tr = {i: sorted(str(t.source) for t in ps[i].tracks) for i in ids}
        S.check("демонстрация экрана с одного подключения и голос с другого существуют независимо", any("SCREEN_SHARE" in "".join(v) for v in tr.values()) and any("MICROPHONE" in "".join(v) for v in tr.values()), str(tr))
        txtA = await A.js("document.body.innerText"); txtB = await B.js("document.body.innerText")
        S.check("на страницах нет ложного сообщения о повторном входе", "уже вошла другая вкладка" not in txtA + txtB and "Нет соединения" not in txtA, "")
        await asyncio.sleep(8)
        S.check("через 8 с все четыре подключения на месте (ничто не закрывается само)", len(mine(await participants(lkroom))) == 4)

        # отключение одного не трогает остальные
        await ro.disconnect(); await asyncio.sleep(3)
        left = mine(await participants(lkroom)); S.check("отключилось приложение без идентификатора — остальные три на месте", len(left) == 3 and jo["identity"] not in left, str(left))
        old_app.call("POST", f"/meetings/{jo['meeting_id']}/leave")
        left = mine(await participants(lkroom)); S.check("явный выход одного устройства не завершает остальных", len(left) == 3)

        # потеря сети у вкладки A и возврат: тот же участник
        await A.send("Network.emulateNetworkConditions", offline=True, latency=0, downloadThroughput=-1, uploadThroughput=-1); await asyncio.sleep(10)
        await A.send("Network.emulateNetworkConditions", offline=False, latency=0, downloadThroughput=-1, uploadThroughput=-1)
        ok = False
        for _ in range(40):
            await asyncio.sleep(2)
            if ids_a[0] in mine(await participants(lkroom)): ok = True; break
        txtA = await A.js("document.body.innerText")
        S.check("после потери сети вкладка вернулась тем же участником, без сообщений о повторном входе", ok and "уже вошла другая вкладка" not in txtA, str(mine(await participants(lkroom))))
        S.check("остальные подключения не затронуты потерей сети одной вкладки", jn["identity"] in mine(await participants(lkroom)))

        # устаревшая вкладка с занятым идентификатором: старый клиент без BroadcastChannel — автоматический перезаход
        cidB = await B.js("sessionStorage.getItem('pg.clientId')")
        C = S.Client("tabC", "admin", PASSWORD, 9652)
        await C.start(); await C.send("Page.addScriptToEvaluateOnNewDocument", source=NODEVICES)
        await C.js(f"sessionStorage.setItem('pg.clientId','{cidB}')")
        await C.goto(f"/rooms/{ROOM}", 2.0); await C.js(f"sessionStorage.setItem('pg.clientId','{cidB}')")
        await C.join(navigate=False)
        await asyncio.sleep(12)
        ids = mine(await participants(lkroom)); txtB = await B.js("document.body.innerText")
        S.check("тот же идентификатор у двух браузеров: вытесненная вкладка сама входит снова отдельным подключением", len(ids) >= 4 and "Это подключение закрыто" not in txtB, f"{len(ids)} {ids}")
        await C.close()
    finally:
        for r in apps:
            try: await r.disconnect()
            except Exception: pass
        await A.close()


if __name__ == "__main__":
    asyncio.run(main())
    bad = [r for r in S.results if not r[1]]
    print(f"\nПроверок: {len(S.results)}, не прошли: {len(bad)}")
    sys.exit(1 if bad else 0)
