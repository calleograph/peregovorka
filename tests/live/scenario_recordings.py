"""Сценарий: общая запись встречи. Два участника «говорят» одновременно со сдвигом (синусы 440 и 660 Гц; второй входит позже), встреча завершается настоящим путём,
в фоне собирается общая M4A (настоящий ffmpeg), затем запись проверяется по API (Range, права) и в браузере: блок «Записи», плеер поверх страницы, пауза/перемотка/скорость/Esc.
Нужен ffmpeg (переменная FFMPEG_BIN у стенда)."""
import asyncio, base64, http.cookiejar, json, os, subprocess, tempfile, time, urllib.error, urllib.request
import websockets
B = "http://127.0.0.1:8000/api/v1"
HOST = os.environ.get("PEREGOVORKA_URL", "http://localhost:5173")
EDGE = os.environ.get("BROWSER_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
OUT = os.environ.get("SMOKE_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")); os.makedirs(OUT, exist_ok=True)
res = []


def check(n, ok, extra=""):
    res.append(bool(ok)); print(("OK   " if ok else "FAIL ") + n + (f"  [{extra}]" if extra and not ok else ""), flush=True)


class Api:
    def __init__(self, login, pw):
        self.cj = http.cookiejar.CookieJar(); self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj)); self.csrf = ""
        self.csrf = self.call("POST", "/auth/login", {"login": login, "password": pw})["csrf_token"]

    def call(self, m, path, body=None, headers=None, raw=False):
        req = urllib.request.Request(B + path, method=m, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json", "X-CSRF-Token": self.csrf, **(headers or {})})
        try:
            with self.op.open(req) as r:
                t = r.read()
                if raw:
                    return r.status, {k.lower(): v for k, v in r.headers.items()}, t
                return json.loads(t) if t else {"_status": r.status}
        except urllib.error.HTTPError as e:
            b = e.read()
            return (e.code, {k.lower(): v for k, v in e.headers.items()}, b) if raw else {"_status": e.code, "_body": b.decode()[:300]}


def dev(path, body):
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/__dev/" + path, method="POST", data=json.dumps(body).encode(), headers={"content-type": "application/json"})).read()


def main_api():
    root, alice, bob = Api("root", "root-pass"), Api("alice", "alice-pass"), Api("bob", "bob-pass")
    rooms = {x["slug"]: x for x in root.call("GET", "/rooms")}
    ja = alice.call("POST", f"/rooms/{rooms['north']['id']}/join", {}); jb = bob.call("POST", f"/rooms/{rooms['north']['id']}/join", {})
    mid = ja["meeting_id"]
    check("двое вошли в комнату", "meeting_id" in ja and "meeting_id" in jb, str(ja)[:150])
    dev("pcm", {"meeting_id": mid, "identity": ja["identity"], "seconds": 12, "freq": 440, "start_offset": 0})
    dev("pcm", {"meeting_id": mid, "identity": jb["identity"], "seconds": 8, "freq": 660, "start_offset": 4})
    t0 = time.time()
    e = root.call("POST", f"/meetings/{mid}/end")
    check("встреча завершена сразу (сведение не блокирует завершение)", e.get("_status") in (200, 204) and time.time() - t0 < 8, f"{e} {time.time() - t0:.1f}s")
    mix = None
    for _ in range(60):
        m = root.call("GET", f"/meetings/{mid}/media")
        mix = next((x for x in m.get("mixes", []) if x["kind"] == "mix_audio"), None)
        if mix and mix["status"] != "processing":
            break
        time.sleep(1)
    check("общая запись сформирована", bool(mix) and mix["status"] == "ready", str(mix))
    if not mix or mix["status"] != "ready":
        return mid, None
    check("длительность общей записи ≈ 12 с (по самой длинной дорожке, со сдвигом)", 11 <= (mix["duration_s"] or 0) <= 13.5, str(mix["duration_s"]))
    check("у администратора есть файлы участников, а общая запись — одна", len(m["participants"]) == 2 and len(m["mixes"]) == 1, str(m)[:200])
    url = f"/meetings/{mid}/media/{mix['id']}/stream"
    st, h, body = root.call("GET", url, headers={"Range": "bytes=0-99"}, raw=True)
    check("Range: 206 и ровно 100 байт", st == 206 and len(body) == 100 and h.get("content-range", "").startswith("bytes 0-99/"), f"{st} {len(body)} {h.get('Content-Range')}")
    st, h, body = root.call("GET", url, raw=True)
    check("файл целиком: 200, audio/mp4, Accept-Ranges", st == 200 and h.get("content-type", "").startswith("audio/mp4") and h.get("accept-ranges") == "bytes" and len(body) == mix["size_bytes"], f"{st} {h.get('Content-Type')}")
    am = bob.call("GET", f"/meetings/{mid}/media")
    check("рядовой участник видит общую запись", len(am.get("mixes", [])) == 1, str(am)[:200])
    check("рядовой участник не видит файлы других участников", am.get("participants") == [], str(am.get("participants")))
    pid = m["participants"][0]["id"]
    s1, _, _ = bob.call("GET", f"/meetings/{mid}/media/{pid}/stream", raw=True)
    check("файл участника рядовому участнику недоступен", s1 in (403, 404), str(s1))
    s2, _, _ = bob.call("GET", url + "?download=true", raw=True)
    check("скачивание рядовому участнику запрещено", s2 == 403, str(s2))
    s3, _, _ = bob.call("GET", f"/meetings/00000000-0000-4000-8000-000000000000/media/{mix['id']}/stream", raw=True)
    check("чужой/несуществующий идентификатор встречи не открывает запись", s3 in (403, 404), str(s3))
    return mid, mix


async def browser(mid):
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--autoplay-policy=no-user-gesture-required", "--remote-debugging-port=9442", f"--user-data-dir={tempfile.mkdtemp(prefix='edge-s-')}", "--window-size=1366,900", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:9442/json").read(); break
            except Exception:
                time.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9442/json/new?about:blank", method="PUT")))
        ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None)
        n = 0; errs = []

        async def send(m, **p):
            nonlocal n
            n += 1; i = n
            await ws.send(json.dumps({"id": i, "method": m, "params": p}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == i:
                    return r.get("result", {})
                if r.get("method") == "Runtime.exceptionThrown":
                    errs.append(json.dumps(r["params"]["exceptionDetails"], ensure_ascii=False)[:300])

        async def js(e):
            r = await send("Runtime.evaluate", expression=e, awaitPromise=True, returnByValue=True)
            return r.get("result", {}).get("value")

        async def wait(expr, t=20):
            t0 = time.time()
            while time.time() - t0 < t:
                if await js(expr):
                    return True
                await asyncio.sleep(0.4)
            return False

        async def shot(name):
            r = await send("Page.captureScreenshot", format="png")
            open(os.path.join(OUT, name), "wb").write(base64.b64decode(r["data"]))

        async def key(k, code=None, text=None):
            for t in ("keyDown", "keyUp"):
                await send("Input.dispatchKeyEvent", type=t, key=k, code=code or k, windowsVirtualKeyCode={"Escape": 27, " ": 32, "ArrowRight": 39}.get(k, 0), text=text or "")

        await send("Page.enable"); await send("Runtime.enable")
        await send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        await send("Page.navigate", url=HOST + "/"); await asyncio.sleep(2)
        await js("fetch('/api/v1/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({login:'root',password:'root-pass'})}).then(r=>r.status)")
        await send("Page.navigate", url=f"{HOST}/history/{mid}"); await asyncio.sleep(3)
        await js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        check("в истории виден блок «Записи» с общей аудиозаписью", await wait("!!document.querySelector('.rec-block') && document.querySelector('.rec-block').innerText.includes('Аудиозапись встречи')", 15))
        await shot("rec-block.png")
        check("кнопка называется «Слушать»", await js("[...document.querySelectorAll('.rec-block button')].some(b=>b.textContent.trim()==='Слушать')"))
        await js("[...document.querySelectorAll('.rec-block button')].find(b=>b.textContent.trim()==='Слушать').click()")
        check("плеер открылся поверх страницы", await wait("!!document.querySelector('.mp-win[role=dialog]')", 10))
        check("аудио-плеер без пустого видеоокна", await js("!document.querySelector('.mp-win video') && !!document.querySelector('.mp-win audio')"))
        check("метаданные загружены, длительность ≈ 12 с", await wait("(()=>{const a=document.querySelector('.mp-win audio');return a&&a.readyState>=1&&a.duration>10&&a.duration<14})()", 15), str(await js("document.querySelector('.mp-win audio')?.duration")))
        await shot("rec-player-open.png")
        await key(" ", "Space", " ")
        check("пробел запускает воспроизведение", await wait("(()=>{const a=document.querySelector('.mp-win audio');return a&&!a.paused&&a.currentTime>0.3})()", 10), str(await js("document.querySelector('.mp-win audio')?.currentTime")))
        await key(" ", "Space", " ")
        check("повторный пробел ставит на паузу", await wait("document.querySelector('.mp-win audio').paused", 5))
        t_before = await js("document.querySelector('.mp-win audio').currentTime")
        await key("ArrowRight", "ArrowRight")
        t_after = await js("document.querySelector('.mp-win audio').currentTime")
        check("→ перематывает на 5 секунд вперёд", 4.5 <= t_after - t_before <= 5.5, f"{t_before}->{t_after}")
        await js("(()=>{const s=document.querySelector('.mp-speed');s.value='1.5';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
        await js("(()=>{const el=document.querySelector('.mp-speed');const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;set.call(el,'1.5');el.dispatchEvent(new Event('change',{bubbles:true}));})()")
        check("скорость 1,5× применяется к записи", await wait("document.querySelector('.mp-win audio').playbackRate===1.5", 5), str(await js("document.querySelector('.mp-win audio').playbackRate")))
        check("доступно скачивание? (для администратора — да)", await js("!!document.querySelector('.mp-win a[href*=download]')"))
        await shot("rec-player-playing.png")
        await key("Escape", "Escape")
        check("Esc закрывает плеер", await wait("!document.querySelector('.mp-win')", 5))
        check("после закрытия на странице не осталось работающего аудио", await js("[...document.querySelectorAll('audio,video')].every(a=>a.paused)"))
        print("ошибки страницы:", errs[:3])
        check("нет ошибок страницы", not errs, str(errs[:2]))
        await ws.close()
    finally:
        proc.terminate()


mid, mix = main_api()
if mix:
    asyncio.run(browser(mid))
print(f"\nИТОГО: {sum(res)} из {len(res)}")
