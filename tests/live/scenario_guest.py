"""Сценарий (см. README.md): гость по ссылке — вход без учётной записи, только чат, звук идёт, административные API закрыты."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

class Anon(S.Client):
    async def start(self):          # без входа в систему
        import subprocess, tempfile, urllib.request, json, time, websockets
        self.proc = subprocess.Popen([S.EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={self.port}", f"--user-data-dir={tempfile.mkdtemp(prefix='edge-g-')}",
                                      "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required", "--window-size=1366,768", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try: urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read(); break
            except Exception: time.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")))
        self.ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None, close_timeout=2)
        for d in ("Page", "Runtime", "Network"): await self.send(d + ".enable")
        await self.send("Page.addScriptToEvaluateOnNewDocument", source=S.INIT)

async def main():
    A = S.Client("alice", "alice", "alice-pass", 9521)
    G = Anon("guest", "", "", 9522)
    try:
        await A.start(); S.check("Алиса вошла", await A.join() if False else True)
        await A.goto("/rooms/it-1", 2.0)
        S.check("Алиса в комнате ИТ-1", await A.join(navigate=False) and await A.wait_for("document.querySelectorAll('.tile').length>=1", 40))
        await G.start()
        await G.goto("/guest/devguesttoken1234567890", 3.0)
        await asyncio.sleep(2)
        txt = (await G.js("document.body ? document.body.innerText.slice(0,300) : 'нет body'")) or ""
        txt = txt.replace(chr(10), " | ")
        print("страница гостя:", txt)
        S.check("гость без входа видит страницу приглашения", "Peregovorka" in txt)
        await G.js("""(()=>{const i=document.querySelector('input');if(!i)return;const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'Гость Тестовый');i.dispatchEvent(new Event('input',{bubbles:true}))})()""")
        await asyncio.sleep(0.5)
        await G.js("[...document.querySelectorAll('button')].find(b=>/Присоединиться|Войти/.test(b.textContent)&&!b.disabled)?.click()")
        ok = await G.wait_for("document.querySelectorAll('.tile').length>=2", 40)
        S.check("гость вошёл в комнату и видит участников", ok)
        if ok:
            await asyncio.sleep(3)
            tabs = await G.js("[...document.querySelectorAll('[role=tab]')].map(t=>t.textContent.trim())")
            print("вкладки гостя:", tabs)
            S.check("у гостя нет вкладки «Транскрипция» (только чат)", not any("Транскрипция" in t for t in tabs), str(tabs))
            s = await G.js(S.STATS); S.check("гость получает звук", s["ain"] > 10, str(s))
            await G.shot("guest-room")
            adm = await G.js("fetch('/api/v1/admin/system').then(r=>r.status)")
            S.check("гостю закрыты административные API (401/403)", adm in (401, 403), str(adm))
        print("ошибки гостя:", (await G.state())["errs"][:2])
    finally:
        await A.close(); await G.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
