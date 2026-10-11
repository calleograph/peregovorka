"""Сценарий (см. README.md): гость по ссылке в презентационной комнате — проверки оборудования нет, микрофон и камера не запрашиваются, гость зритель,
сервер звонков не видит у него дорожек; руководитель даёт ему слово — права появляются, забирает — исчезают."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from lk_helpers import INIT_HOOKS, lk_state  # noqa: E402

S.ROOM = "/rooms/stage"
S.INIT += INIT_HOOKS


class Anon(S.Client):
    async def start(self):          # без входа в систему
        import subprocess, tempfile, urllib.request, json, time, websockets
        self.proc = subprocess.Popen([S.EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={self.port}", f"--user-data-dir={tempfile.mkdtemp(prefix='edge-g-')}",
                                      "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required", "--window-size=1366,768", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try: urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read(); break
            except Exception: time.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")))
        self.ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None, close_timeout=2)
        for d in ("Page", "Runtime", "Network"): await self.send(d + ".enable")
        await self.send("Page.addScriptToEvaluateOnNewDocument", source=S.INIT)


async def main():
    R = S.Client("lead", "root", "root-pass", 9551)
    G = Anon("guest", "", "", 9552)
    try:
        await R.start()
        S.check("руководитель вошёл", await R.join())
        await asyncio.sleep(2)
        await G.start()
        await G.goto("/guest/devguesttokenstage00001", 3.0)
        await G.wait_for("document.body.innerText.includes('Вы входите как гость')", 30)
        txt = (await G.js("document.body.innerText")) or ""
        S.check("страница гостя: сказано, что это презентация и микрофон не понадобится", "Это презентация" in txt, txt[:160].replace(chr(10), " | "))
        S.check("страница гостя: проверки оборудования нет", not await G.js("!!document.querySelector('.precheck')") and "Проверить микрофон" not in txt)
        await G.js("""(()=>{const i=document.querySelector('input');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'Гость Тестовый');i.dispatchEvent(new Event('input',{bubbles:true}))})()""")
        await asyncio.sleep(0.5)
        await G.js("[...document.querySelectorAll('button')].find(b=>/Присоединиться|Войти/.test(b.textContent)&&!b.disabled)?.click()")
        ok = await G.wait_for("document.querySelectorAll('.rbtn').length>3", 40)
        S.check("гость вошёл в комнату", ok)
        await asyncio.sleep(3)
        S.check("гость: браузер не запрашивал микрофон и камеру", await G.js("window.__gum") == 0, str(await G.js("window.__gum")))
        labels = await G.js("[...document.querySelectorAll('.rbtn')].map(b=>b.getAttribute('aria-label')+(b.disabled?'[off]':''))")
        S.check("гость: кнопка микрофона «Слушаете» недоступна", any(x.startswith("Слушаете") and x.endswith("[off]") for x in labels), str(labels[:3]))
        room_name = await R.js("window.__join && window.__join.livekit_room")
        print("комната LiveKit:", room_name)
        if room_name:
            st = await lk_state(room_name)
            guests = {k: v for k, v in st.items() if k.startswith("g-")}
            S.check("сервер звонков: гость есть, дорожек и прав публикации у него нет", len(guests) == 1 and all(v["tracks"] == [] and not v["can_publish"] for v in guests.values()), str(guests))
        await R.shot("guest-presentation")
    finally:
        for c in (R, G):
            try: await c.close()
            except Exception: pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
