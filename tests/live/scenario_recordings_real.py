"""Сценарий: общая запись по РЕАЛЬНОМУ звуку через LiveKit. Два браузера входят в комнату и «говорят» в поддельный микрофон Chrome, который воспроизводит настоящую
русскую речь (SAPI: Pavel и Irina) с паузой и частичным наложением реплик. Звук идёт по WebRTC (Opus, шумоподавление, DTX) через настоящий LiveKit; запись ведёт
настоящий ASR-воркер (asr-service/app/worker.py + PcmRecorder, без модели распознавания); встреча завершается настоящим путём, backend сводит общую запись настоящим ffmpeg.
Затем analyze_mix.py сверяет дорожки с источниками и общую запись с дорожками.
Нужны: стенд (devserver_live.py с FFMPEG_BIN), vite, livekit-server, Edge, Windows с голосами SAPI (Pavel, Irina), venv ASR с livekit (ASR_PY).
Ограничение: микрофон «настоящий» только в смысле WebRTC-тракта; физические микрофоны и эхоподавление живых устройств этим не проверяются."""
import asyncio, http.cookiejar, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request, wave
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
B = "http://127.0.0.1:8000/api/v1"
WORK = os.environ.get("REC_WORK", os.path.join(tempfile.gettempdir(), "peregovorka-real-rec"))
ASR_PY = os.environ["ASR_PY"]
REC_DIR = os.environ["REC_DIR"]                         # каталог записей стенда (<LIVE_DATA>/recordings)
SR = 16000
S.ROOM = "/rooms/north"
LEAD, PAUSE, B_OVERLAP = 12.0, 5.0, 2.5


class Cl(S.Client):
    def __init__(self, label, login, pw, port, wav):
        super().__init__(label, login, pw, port); self.wav = wav

    async def start(self):                              # как Client.start, но микрофон — файл с речью
        self.proc = subprocess.Popen([S.EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={self.port}", f"--user-data-dir={tempfile.mkdtemp(prefix='edge-s-')}",
                                      "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={self.wav}%noloop",
                                      "--autoplay-policy=no-user-gesture-required", "--window-size=1366,768", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read(); break
            except Exception:
                time.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")))
        self.ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None, close_timeout=2)
        await self.send("Page.enable"); await self.send("Runtime.enable"); await self.send("Network.enable")
        await self.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False)
        await self.send("Page.addScriptToEvaluateOnNewDocument", source=S.INIT)
        await self.goto("/")
        r = await self.js(f"fetch('/api/v1/auth/login',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{login:'{self.login}',password:'{self.password}'}})}}).then(r=>r.status)")
        assert r == 200, f"login {self.login}: {r}"


class Api:
    def __init__(self, login, pw):
        self.cj = http.cookiejar.CookieJar(); self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj)); self.csrf = ""
        self.csrf = self.call("POST", "/auth/login", {"login": login, "password": pw})["csrf_token"]

    def call(self, m, path, body=None, raw=False):
        req = urllib.request.Request(B + path, method=m, data=json.dumps(body).encode() if body is not None else None, headers={"content-type": "application/json", "X-CSRF-Token": self.csrf})
        try:
            with self.op.open(req) as r:
                t = r.read()
                return t if raw else (json.loads(t) if t else {"_status": r.status})
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "_body": e.read().decode()[:300]}


def read16(p):
    with wave.open(p) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def write48(p, x16):
    x = np.interp(np.arange(0, len(x16), SR / 48000), np.arange(len(x16)), x16.astype(np.float32)).astype("<i2")
    with wave.open(p, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(x.tobytes())


async def main():
    os.makedirs(WORK, exist_ok=True)
    sp = os.path.join(WORK, "speech")
    subprocess.run(["pwsh", "-NoProfile", "-File", os.path.join(HERE, "make_speech.ps1"), "-Out", sp], check=True, capture_output=True)
    p1, p2, p3 = (read16(os.path.join(sp, f"p{i}.wav")) for i in (1, 2, 3))
    z = lambda s: np.zeros(int(s * SR), dtype="<i2")
    b_shift = len(p1) / SR - B_OVERLAP
    srcA = np.concatenate([z(LEAD), p1, z(PAUSE), p3]); srcB = np.concatenate([z(LEAD), z(b_shift), p2, z(1.0)])
    run = os.path.join(WORK, "run"); os.makedirs(run, exist_ok=True)
    for f in os.listdir(run):
        os.remove(os.path.join(run, f))
    write48(os.path.join(run, "A.wav"), srcA); write48(os.path.join(run, "B.wav"), srcB)
    srcA.tofile(os.path.join(run, "srcA.pcm")); srcB.tofile(os.path.join(run, "srcB.pcm"))
    json.dump({"lead": LEAD, "p1": len(p1) / SR, "p2": len(p2) / SR, "p3": len(p3) / SR, "pause": PAUSE, "b_shift": b_shift}, open(os.path.join(run, "plan.json"), "w"))

    A = Cl("A-alice", "alice", "alice-pass", 9611, os.path.join(run, "A.wav")); Bc = Cl("B-bob", "bob", "bob-pass", 9612, os.path.join(run, "B.wav"))
    worker = None; stop = os.path.join(run, "stop")
    try:
        await asyncio.gather(A.start(), Bc.start())
        S.check("вход двоих через интерфейс", all(await asyncio.gather(A.join(), Bc.join())))
        root = Api("root", "root-pass")
        rooms = {r["slug"]: r for r in root.call("GET", "/rooms")}
        mid = next((x["id"] for x in root.call("GET", "/admin/meetings?active=true") if "Север" in x["room_name"]), None)
        S.check("встреча найдена", bool(mid), str(rooms["north"])[:200])
        lk_room = "m-" + mid.replace("-", "")
        worker = subprocess.Popen([ASR_PY, os.path.join(HERE, "asr_recorder_live.py"), lk_room, REC_DIR, stop], cwd=os.path.join(HERE, "..", "..", "asr-service"), stdout=open(os.path.join(run, "worker.log"), "w"), stderr=subprocess.STDOUT)
        t0 = time.time()
        total = LEAD + len(p1) / SR + PAUSE + len(p3) / SR + 6
        await asyncio.sleep(max(0, total))
        print(f"прошло {time.time() - t0:.0f} с, останавливаем запись", flush=True)
        open(stop, "w").write("1")
        worker.wait(timeout=30)
        d = os.path.join(REC_DIR, lk_room)
        files = sorted(os.listdir(d)) if os.path.isdir(d) else []
        S.check("ASR-воркер записал дорожки двух участников (.pcm и .t0)", len([f for f in files if f.endswith(".pcm")]) == 2 and len([f for f in files if f.endswith(".t0")]) == 2, str(files) + open(os.path.join(run, "worker.log")).read()[-300:])
        # какая дорожка чья: identity = u-<id пользователя>; сопоставляем по именам участников встречи
        jp = [x for x in root.call("GET", f"/meetings/{mid}")["participants"] if x.get("user_id")]
        pcms = [f[:-4] for f in files if f.endswith(".pcm")]
        mapping = {}
        for x in jp:
            ident = "u-" + x["user_id"].replace("-", "")
            if ident in pcms:
                mapping["A" if "Алиса" in x["display_name"] else "B"] = ident
        S.check("дорожки сопоставлены с участниками", set(mapping) == {"A", "B"}, str(mapping) + str(pcms))
        for k, ident in mapping.items():                  # копия дорожек для анализа (backend заберёт и удалит оригиналы при завершении встречи)
            for ext in ("pcm", "t0"):
                open(os.path.join(run, f"{k}.{ext}"), "wb").write(open(os.path.join(d, f"{ident}.{ext}"), "rb").read())
        e = root.call("POST", f"/meetings/{mid}/end")
        S.check("встреча завершена", e.get("_status") in (200, 204), str(e))
        mix = None
        for _ in range(90):
            m = root.call("GET", f"/meetings/{mid}/media")
            mix = next((x for x in m.get("mixes", []) if x["kind"] == "mix_audio"), None)
            if mix and mix["status"] != "processing":
                break
            await asyncio.sleep(1)
        S.check("общая запись сформирована", bool(mix) and mix["status"] == "ready", str(mix))
        if mix and mix["status"] == "ready":
            open(os.path.join(run, "mix.m4a"), "wb").write(root.call("GET", f"/meetings/{mid}/media/{mix['id']}/stream", raw=True))
            r = subprocess.run([ASR_PY, os.path.join(HERE, "analyze_mix.py"), run], capture_output=True, text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            print(r.stdout, r.stderr[-500:])
            S.check("анализ: дорожки совпадают с источниками, общая запись совпадает с дорожками, одновременная речь и пауза сохранены", r.returncode == 0)
    finally:
        open(stop, "w").write("1")
        if worker and worker.poll() is None:
            worker.terminate()
        await A.close(); await Bc.close()
    ok = [x for x in S.results if x[1]]; print(f"\nИТОГО: {len(ok)} из {len(S.results)}")


asyncio.run(main())
