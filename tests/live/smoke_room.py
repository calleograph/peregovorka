"""Живой smoke-test комнаты (см. tests/live/README.md): настоящий LiveKit + три браузера с поддельными устройствами. Не часть CI: нужен локальный стенд."""
import asyncio, base64, json, os, subprocess, sys, tempfile, time, urllib.request
import websockets

EDGE = os.environ.get("BROWSER_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
OUT = os.environ.get("SMOKE_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
os.makedirs(OUT, exist_ok=True)
HOST = os.environ.get("PEREGOVORKA_URL", "http://localhost:5173")
ROOM = "/rooms/sales"
results = []
RUN_ID = str(int(time.time()))          # текст сообщения уникален: остаток прошлого прогона в той же встрече дал бы ложный успех

INIT = r"""
window.__errs = []; window.__osc = 0; window.__pcs = [];
window.addEventListener('error', e => window.__errs.push(String(e.message) + ' @ ' + String(e.filename || '').split('/').slice(-2).join('/') + ':' + e.lineno + ' ' + String((e.error && e.error.stack) || '').replace(/\s+/g, ' ').slice(0, 400)));
window.addEventListener('unhandledrejection', e => window.__errs.push('rej:' + String(e.reason && e.reason.message || e.reason)));
const _co = AudioContext.prototype.createOscillator;
AudioContext.prototype.createOscillator = function () { window.__osc++; return _co.apply(this, arguments); };
const _PC = window.RTCPeerConnection;
window.RTCPeerConnection = function (...a) { const pc = new _PC(...a); window.__pcs.push(pc); return pc; };
window.RTCPeerConnection.prototype = _PC.prototype;
const _log = console.error; console.error = function (...a) { try { window.__errs.push('console:' + a.map(String).join(' ').slice(0, 200)); } catch (e) {} return _log.apply(this, a); };
"""

STATS = r"""(async () => {
  let ain = 0, vin = 0, aout = 0, vout = 0;
  for (const pc of window.__pcs) { try { const st = await pc.getStats(); st.forEach(r => {
    if (r.type === 'inbound-rtp' && r.kind === 'audio') ain += r.packetsReceived || 0;
    if (r.type === 'inbound-rtp' && r.kind === 'video') vin += r.framesDecoded || 0;
    if (r.type === 'outbound-rtp' && r.kind === 'audio') aout += r.packetsSent || 0;
    if (r.type === 'outbound-rtp' && r.kind === 'video') vout += r.framesEncoded || 0;
  }); } catch (e) {} }
  return {ain, vin, aout, vout};
})()"""


def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    print(("OK   " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)


class Client:
    def __init__(self, label, login, password, port):
        self.label, self.login, self.password, self.port = label, login, password, port
        self.n = 0

    async def start(self):
        self.proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={self.port}", f"--user-data-dir={tempfile.mkdtemp(prefix='edge-s-')}",
                                      "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required",
                                      "--window-size=1366,768", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read(); break
            except Exception:
                time.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")))
        self.ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None, close_timeout=2)
        await self.send("Page.enable"); await self.send("Runtime.enable"); await self.send("Network.enable")
        await self.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False)
        await self.send("Page.addScriptToEvaluateOnNewDocument", source=INIT)
        await self.goto("/")
        r = await self.js(f"fetch('/api/v1/auth/login',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{login:'{self.login}',password:'{self.password}'}})}}).then(r=>r.status)")
        assert r == 200, f"login {self.login}: {r}"

    async def send(self, m, **p):
        self.n += 1; i = self.n
        await self.ws.send(json.dumps({"id": i, "method": m, "params": p}))
        while True:
            r = json.loads(await self.ws.recv())
            if r.get("id") == i:
                return r.get("result", {})

    async def js(self, e):
        r = await self.send("Runtime.evaluate", expression=e, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    async def goto(self, path, wait=2.0):
        await self.send("Page.navigate", url=HOST + path); await asyncio.sleep(wait)
        await self.js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")

    async def shot(self, name):
        r = await self.send("Page.captureScreenshot", format="png")
        open(os.path.join(OUT, f"{self.label}-{name}.png"), "wb").write(base64.b64decode(r["data"]))

    async def join(self, navigate=True):
        if navigate: await self.goto(ROOM, 2.0)
        await self.wait_for("!!document.querySelector('.pj-actions .btn.primary')", 30)
        await self.js("[...document.querySelectorAll('.precheck button')].filter(b=>/Разрешить доступ|Проверить микрофон/.test(b.textContent)).forEach(b=>b.click())")
        await asyncio.sleep(0.8)
        for _ in range(3):
            await self.js("document.querySelector('.pj-actions .btn.primary')?.click()")
            if await self.wait_for("!document.querySelector('.pj-actions .btn.primary') || document.querySelectorAll('.tile').length>0", 8):
                return True
        return False

    async def wait_for(self, expr, timeout=25):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if await self.js(expr): return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    async def click_btn(self, label):
        return await self.js(f"(()=>{{const b=[...document.querySelectorAll('button')].find(x=>(x.getAttribute('aria-label')||'')==='{label}');if(!b)return false;b.click();return true}})()")

    async def state(self):
        return await self.js("""(()=>({tiles:document.querySelectorAll('.tile').length, names:[...document.querySelectorAll('.tile')].map(t=>t.getAttribute('title')),
          errs:window.__errs.slice(0,6), osc:window.__osc,
          audio:[...document.querySelectorAll('audio[data-identity]')].map(a=>({id:a.dataset.identity.slice(0,6),muted:a.muted,live:!!(a.srcObject&&a.srcObject.getAudioTracks().some(t=>t.readyState==='live'))}))}))()""")

    async def close(self):
        try: await self.ws.close()
        except Exception: pass
        self.proc.terminate()


async def main():
    A = Client("alice", "alice", "alice-pass", 9421)
    B = Client("bob", "bob", "bob-pass", 9422)
    C = Client("root", "root", "root-pass", 9423)
    cs = [A, B, C]
    try:
        for c in cs: await c.start()
        # --- вход
        for c in cs:
            check(f"{c.label}: кнопка «Присоединиться» привела в комнату", await c.join()); await asyncio.sleep(1.0)
        ok = await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=3", 45) for c in cs])
        for c, o in zip(cs, ok):
            check(f"{c.label}: в комнате видны 3 плитки", o)
            if not o:
                print(c.label, "TEXT:", (await c.js("document.body.innerText.slice(0,500)")).replace(chr(10), " | "))
                await c.shot("join-fail")
        await asyncio.sleep(4)
        for c in cs:
            st = await c.state(); print(c.label, json.dumps(st, ensure_ascii=False))
            check(f"{c.label}: нет ошибок страницы после входа (в т.ч. «more hooks»)", not [e for e in st["errs"] if "hooks" in e or e.startswith("rej") or "Uncaught" in e], str(st["errs"]))
            check(f"{c.label}: слышит двоих (2 звуковых элемента, живые треки)", len(st["audio"]) == 2 and all(a["live"] for a in st["audio"]), str(st["audio"]))
        await A.shot("room")
        # --- реальный поток медиа (RTP)
        await asyncio.sleep(3)
        for c in cs:
            s = await c.js(STATS)
            check(f"{c.label}: входящее аудио идёт по WebRTC (пакеты)", s["ain"] > 50, str(s))
            check(f"{c.label}: исходящее аудио идёт (микрофон)", s["aout"] > 50)
        # --- камера: Боб включает → видео у остальных, аватар скрыт; выключает → аватар возвращается
        pre_av = await C.js("[...document.querySelectorAll('.tile')].map(t=>({n:t.getAttribute('title'),img:!!t.querySelector('.avatar img'),av:!!t.querySelector('.avatar')}))")
        print("avatars on root view:", json.dumps(pre_av, ensure_ascii=False))
        check("аватар Алисы (без видео) показан картинкой в плитке у Администратора", any(t["n"] and "Алиса" in t["n"] and t["img"] for t in pre_av), str(pre_av))
        check("у участника без фото — инициалы (картинки нет)", any(t["n"] and "Борис" in t["n"] and t["av"] and not t["img"] for t in pre_av))
        clicked = await B.click_btn("Камера выкл.")
        check("Боб: кнопка камеры нажата", clicked)
        ok = await C.wait_for("(()=>{const t=[...document.querySelectorAll('.tile')].find(x=>(x.getAttribute('title')||'').includes('Борис'));return !!t&&!t.querySelector('.avatar')&&!!t.querySelector('video')})()", 20)
        check("камера Бориса: у Администратора видео вместо аватара", ok)
        await asyncio.sleep(2)
        s = await C.js(STATS); check("видеокадры от Бориса декодируются", s["vin"] > 5, str(s))
        await C.shot("with-bob-camera")
        await B.click_btn("Камера")
        ok = await C.wait_for("(()=>{const t=[...document.querySelectorAll('.tile')].find(x=>(x.getAttribute('title')||'').includes('Борис'));return !!t&&!!t.querySelector('.avatar')})()", 15)
        check("после выключения камеры аватар возвращается", ok)
        # --- «заглушить для себя»
        await C.js("""(()=>{const t=[...document.querySelectorAll('.tile')].find(x=>(x.getAttribute('title')||'').includes('Алиса'));
          t.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:300,clientY:300,button:2}))})()""")
        await asyncio.sleep(0.5)
        has_item = await C.js("[...document.querySelectorAll('.ctx-item')].some(i=>i.textContent.includes('Заглушить для себя'))")
        check("контекстное меню плитки содержит «Заглушить для себя»", has_item)
        await C.js("[...document.querySelectorAll('.ctx-item')].find(i=>i.textContent.includes('Заглушить для себя')).click()")
        await asyncio.sleep(0.6)
        st = await C.state()
        alice_audio = [a for a in st["audio"]]
        marks = await C.js("[...document.querySelectorAll('.tile')].map(t=>({n:t.getAttribute('title'),m:!!t.querySelector('.tile-lmute')}))")
        check("красная метка на плитке Алисы у Администратора", any(m["m"] and "Алиса" in (m["n"] or "") for m in marks), str(marks))
        check("ровно один звуковой элемент заглушён локально", sum(1 for a in alice_audio if a["muted"]) == 1, str(alice_audio))
        stb = await B.state()
        check("у Боба Алиса НЕ заглушена (локальное действие)", not any(a["muted"] for a in stb["audio"]), str(stb["audio"]))
        await C.shot("local-mute")
        # --- рука + звук
        osc0 = await C.js("window.__osc")
        hb = await B.js("(()=>{const b=document.querySelector('button[aria-label=\"Поднять руку\"]');return b?{dis:b.disabled}:null})()")
        print("hand button on bob:", hb)
        check("Боб: кнопка «Поднять руку» нажата", await B.click_btn("Поднять руку"))
        ok = await C.wait_for("!!document.querySelector('.tile-hand')", 12)
        await C.shot("hand-root")
        print("root hands-bar:", await C.js("document.querySelector('.hands-bar')?.innerText || 'нет hands-bar'"), "| tiles:", await C.js("[...document.querySelectorAll('.tile')].map(t=>t.className+'|'+(t.getAttribute('title')))"))
        print("hands API (root):", await C.js("fetch('/api/v1/meetings?limit=1').then(r=>r.json()).then(m=>fetch('/api/v1/meetings/'+m[0].id+'/hands').then(r=>r.json()))"))
        print("hand dbg bob:", await B.js("document.body.innerText.includes('рук') ? [...document.querySelectorAll('[class*=hand]')].map(e=>e.className).slice(0,6) : 'нет'"), "root errs:", (await C.state())["errs"][:3])
        check("поднятая рука Бориса видна у Администратора", ok)
        await asyncio.sleep(1.0)
        print("audio ctx dbg root:", await C.js("(async()=>{const c=new AudioContext();const a=c.state;try{await Promise.race([c.resume(),new Promise(r=>setTimeout(r,1500))])}catch(e){};return {before:a,after:c.state,hs:localStorage.getItem('pg:handSound'),vol:localStorage.getItem('pg:soundVolume'),act:navigator.userActivation&&navigator.userActivation.hasBeenActive}})()"))
        osc1 = await C.js("window.__osc")
        check("звук руки: у Администратора созданы 3 тона", osc1 - osc0 >= 3, f"{osc0}->{osc1}")
        await B.click_btn("Опустить руку")
        if os.environ.get("STOP_AFTER_HAND"): return
        # --- чат + звук
        await A.click_btn("Чат")
        await asyncio.sleep(0.6)
        oscb0 = await B.js("window.__osc")
        await A.js("document.querySelector('textarea[aria-label=\"Текст сообщения\"]').focus()")
        await A.send("Input.insertText", text=f"Проверка чата {RUN_ID}")
        check("пока Алиса печатает, у Боба видно «печатает…» и в консоли Алисы нет ошибок", await B.wait_for("document.body.innerText.includes('печатает')", 8) and not [e for e in (await A.state())["errs"] if "Illegal invocation" in e])
        await A.send("Input.dispatchKeyEvent", type="rawKeyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        await A.send("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        ok = await B.wait_for("document.body.innerText.includes('Проверка чата "+RUN_ID+"')", 12)
        check("сообщение Алисы дошло до Боба", ok)
        if not ok:      # диагностика: что видят Алиса и Боб, пока сообщение не дошло
            print("A: ошибка в чате:", await A.js("[...document.querySelectorAll('.chat .alert, .chat .field-err, .chat-error, .chat [role=alert]')].map(e=>e.innerText).join(' | ')"), "| значение:", await A.js("document.querySelector('.chat textarea')?.value"), "| фокус:", await A.js("document.activeElement?.tagName"), "| видимость:", await A.js("document.visibilityState + ' ' + document.hasFocus()"))
            print("A: ошибки страницы:", (await A.state())["errs"][:3])
            for c in (A, B):
                await c.shot("chat-fail")
                print(c.label, "чат:", (await c.js("(document.querySelector('.chat-history, .chat, aside')||document.body).innerText.slice(-350).replace(/\n/g,' | ')")) or "")
        await asyncio.sleep(1.0)
        oscb1 = await B.js("window.__osc")
        check("звук сообщения: у Боба созданы тоны", oscb1 - oscb0 >= 3, f"{oscb0}->{oscb1}")
        print("lmute storage before reload:", await C.js("JSON.stringify(Object.fromEntries(Object.entries(sessionStorage).filter(([k])=>k.startsWith('pg:lmute'))))"))
        # --- перезагрузка страницы внутри встречи: Администратор (у него Алиса заглушена)
        await C.send("Page.reload"); await asyncio.sleep(3)
        await C.js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        ok = await C.join(navigate=False) and await C.wait_for("document.querySelectorAll('.tile').length>=3", 35)
        check("после перезагрузки Администратор снова в комнате (3 плитки)", ok)
        await asyncio.sleep(4)
        print("lmute storage after reload:", await C.js("JSON.stringify(Object.fromEntries(Object.entries(sessionStorage).filter(([k])=>k.startsWith('pg:lmute'))))"), await C.js("JSON.stringify([...document.querySelectorAll('.tile')].map(t=>t.className.includes('x')||!!t.querySelector('.tile-lmute')))"))
        st = await C.state()
        check("после перезагрузки Алиса остаётся заглушённой для себя", sum(1 for a in st["audio"] if a["muted"]) == 1, str(st["audio"]))
        check("после перезагрузки нет ошибок страницы", not [e for e in st["errs"] if "hooks" in e or "Uncaught" in e], str(st["errs"]))
        # --- обрыв сети у Боба на 6 секунд
        await B.send("Network.emulateNetworkConditions", offline=True, latency=0, downloadThroughput=0, uploadThroughput=0)
        await asyncio.sleep(6)
        mid = await B.js("document.body.innerText.match(/Переподключ[^.\\n]*/i)?.[0] || ''")
        await B.send("Network.emulateNetworkConditions", offline=False, latency=0, downloadThroughput=-1, uploadThroughput=-1)
        await asyncio.sleep(12)
        sb = await B.js(STATS); stb = await B.state()
        print("bob after offline:", mid, json.dumps(stb, ensure_ascii=False)[:300])
        check("Боб после обрыва сети: плитки и звук на месте", stb["tiles"] >= 3 and sum(1 for a in stb["audio"] if a["live"]) >= 1, str(stb))
        await B.shot("after-offline")
        # --- итог
        for c in cs:
            st = await c.state()
            print(c.label, "errs:", st["errs"][:4])
    finally:
        for c in cs: await c.close()
    bad = [r for r in results if not r[1]]
    print(f"\nИТОГО: {len(results) - len(bad)} из {len(results)} проверок прошли; не прошли: {[b[0] for b in bad]}")

if __name__ == "__main__":
    asyncio.run(main())
