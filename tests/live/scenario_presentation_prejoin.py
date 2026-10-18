"""Сценарий (см. README.md): экран перед входом в презентационную комнату — вход руководителя и вход зрителя (сотрудник и гость), обычная комната без изменений.

Руководитель: есть проверка микрофона (устройство, уровень, кнопка проверки), камера, выбор состояния микрофона; «выключен» — микрофон после входа не включается, «включён» — включается;
предварительно включённая камера доходит до комнаты. Зритель: ни запроса микрофона и камеры (getUserMedia ни разу), ни проверки оборудования; текст «Вы входите в режиме зрителя…», а не «Микрофон
включится сразу». Состояние дорожек смотрится у самого сервера звонков (стенд из README: LiveKit с webhook на backend)."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from lk_helpers import INIT_HOOKS, lk_state

S.ROOM = "/rooms/stage"
S.INIT += INIT_HOOKS


class Anon(S.Client):
    async def start(self):          # гость: без входа в систему
        import subprocess, tempfile, urllib.request, json, time, websockets
        self.proc = subprocess.Popen([S.EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={self.port}", f"--user-data-dir={tempfile.mkdtemp(prefix='edge-g-')}",
                                      "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required", "--window-size=1366,768", *os.environ.get("BROWSER_FLAGS", "").split(), "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try: urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read(); break
            except Exception: time.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")))
        self.ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None, close_timeout=2)
        for d in ("Page", "Runtime", "Network"): await self.send(d + ".enable")
        await self.send("Page.addScriptToEvaluateOnNewDocument", source=S.INIT)


async def prejoin_text(c):
    await c.wait_for("!!document.querySelector('.pj-actions .btn.primary')", 40)
    await asyncio.sleep(1.0)
    return (await c.js("document.querySelector('.prejoin-pro')?.innerText || ''")) or ""


async def click_join(c):
    for _ in range(3):
        await c.js("document.querySelector('.pj-actions .btn.primary')?.click()")
        if await c.wait_for("!document.querySelector('.pj-actions .btn.primary') || document.querySelectorAll('.rbtn').length>3", 10):
            break
    return await c.wait_for("document.querySelectorAll('.rbtn').length>3", 40)


async def click_text(c, selector, text):
    return await c.js(f"(()=>{{const b=[...document.querySelectorAll({selector!r})].find(x=>x.textContent.trim().includes({text!r}));if(!b)return false;b.click();return true}})()")


async def main():
    R = S.Client("lead", "root", "root-pass", 9561)
    A = S.Client("alice", "alice", "alice-pass", 9562)
    G = Anon("guest", "", "", 9563)
    try:
        # ------------------------------------------------------------------------------ зритель-сотрудник
        await A.start()
        await A.goto(S.ROOM, 2.5)
        t = await prejoin_text(A)
        S.check("зритель: «Вы входите в режиме зрителя»", "Вы входите в режиме зрителя" in t, t[:200].replace(chr(10), " | "))
        S.check("зритель: «Микрофон и камера выключены. Руководитель может предоставить вам слово»", "Микрофон и камера выключены" in t and "Руководитель может предоставить вам слово" in t)
        S.check("зритель: после слова сможет сам включить микрофон, камеру и другие функции", "самостоятельно включить микрофон, камеру" in t)
        S.check("зритель: прежней надписи «Микрофон включится сразу» нет", "включится сразу" not in t)
        S.check("зритель: проверки оборудования и выбора микрофона на экране нет", not await A.js("!!document.querySelector('.precheck, .pj-micstate, .pj-preview')"))
        S.check("зритель: до входа браузер не запрашивал микрофон и камеру", await A.js("window.__gum") == 0)
        await A.shot("prejoin-viewer")
        S.check("зритель: вошёл", await click_join(A))
        await asyncio.sleep(3)
        S.check("зритель: и после входа getUserMedia ни разу не вызывался", await A.js("window.__gum") == 0, str(await A.js("window.__gum")))

        # ------------------------------------------------------------------------------ руководитель: микрофон выключен
        await R.start()
        await R.goto(S.ROOM, 2.5)
        t = await prejoin_text(R)
        S.check("руководитель: есть выбор «Микрофон при входе»", await R.js("!!document.querySelector('.pj-micstate[role=radiogroup]')"))
        S.check("руководитель: проверка оборудования на месте (микрофон, уровень, динамики)", await R.js("!!document.querySelector('.precheck select[aria-label=Микрофон]') && !!document.querySelector('.precheck [role=meter]')"))
        S.check("руководитель: есть предпросмотр и проверка камеры", await R.js("!!document.querySelector('.pj-preview')") and "Проверить камеру" in t)
        S.check("руководитель: по умолчанию «микрофон будет включён»", "микрофон будет включён" in t and "Микрофон включится сразу" not in t, t[t.find('Перед входом'):t.find('Перед входом') + 260].replace(chr(10), " | "))
        S.check("руководитель: до проверки устройства не запрашивались", await R.js("window.__gum") == 0)
        S.check("руководитель выбрал «Выключен»", await click_text(R, ".pj-micstate button", "Выключен"))
        await asyncio.sleep(0.4)
        t = (await R.js("document.querySelector('.prejoin-pro').innerText")) or ""
        S.check("надпись сменилась: «микрофон выключен… не включится сам»", "микрофон выключен" in t and "не включится сам" in t, t[:300].replace(chr(10), " | "))
        S.check("руководитель нажал «Проверить микрофон» — устройство запрошено, показан уровень", await click_text(R, ".precheck button", "Проверить микрофон") and await R.wait_for("window.__gum>=1", 10))
        await R.wait_for("!!document.querySelector('.precheck .badge.ok')", 10)
        S.check("проверка микрофона: отметка «работает»", await R.js("[...document.querySelectorAll('.precheck .badge')].some(b=>b.textContent.includes('работает'))"))
        S.check("руководитель: «Проверить камеру» работает (предпросмотр)", await click_text(R, ".precheck button", "Проверить камеру") and await R.wait_for("[...document.querySelectorAll('.precheck .badge')].some(b=>/включена/.test(b.textContent))", 15))
        await R.shot("prejoin-leader")
        S.check("руководитель вошёл", await click_join(R))
        room_name = await R.js("window.__join && window.__join.livekit_room")
        await asyncio.sleep(8)
        st = await lk_state(room_name)
        lead = next((v for k, v in st.items() if v["name"] == "Администратор"), None)
        S.check("сервер звонков: выбрано «Выключен» — микрофон руководителя НЕ включился", lead is not None and "mic" not in lead["tracks"], str(lead))
        S.check("предварительно проверенная камера доехала до комнаты", lead is not None and "cam" in lead["tracks"], str(lead))
        labels = await R.js("[...document.querySelectorAll('.rbtn')].map(b=>b.getAttribute('aria-label')+(b.disabled?'[off]':''))")
        S.check("в комнате кнопка «Микрофон выкл.» доступна — включить можно вручную", "Микрофон выкл." in labels, str(labels[:3]))
        S.check("зритель видит число зрителей и руководителя (презентация работает)", await A.wait_for("document.querySelectorAll('.tile').length>=1", 20))
        await R.close()

        # ------------------------------------------------------------------------------ руководитель: микрофон включён
        R = S.Client("lead2", "root", "root-pass", 9564)
        await R.start()
        await R.goto(S.ROOM, 2.5)
        await prejoin_text(R)
        S.check("руководитель: выбрал «Включён»", await click_text(R, ".pj-micstate button", "Включён"))
        S.check("руководитель вошёл со включённым микрофоном", await click_join(R))
        await asyncio.sleep(8)
        st = await lk_state(room_name)
        lead = next((v for k, v in st.items() if v["name"] == "Администратор"), None)
        S.check("сервер звонков: выбрано «Включён» — микрофон руководителя опубликован", lead is not None and "mic" in lead["tracks"], str(lead))
        await R.shot("leader-in-room")
        await R.close()

        # ------------------------------------------------------------------------------ гость
        await G.start()
        await G.goto("/guest/devguesttokenstage00001", 3.0)
        await G.wait_for("document.body.innerText.includes('Вы входите как гость')", 30)
        t = (await G.js("document.body.innerText")) or ""
        S.check("гость: «Вы входите в режиме зрителя. Микрофон и камера выключены»", "Вы входите в режиме зрителя" in t and "Микрофон и камера выключены" in t and "Руководитель может предоставить вам слово" in t, t[:300].replace(chr(10), " | "))
        S.check("гость: проверки оборудования нет, getUserMedia не вызывался", not await G.js("!!document.querySelector('.precheck')") and await G.js("window.__gum") == 0)
        await G.js("""(()=>{const i=document.querySelector('input');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'Гость Проверочный');i.dispatchEvent(new Event('input',{bubbles:true}))})()""")
        await asyncio.sleep(0.5)
        await G.js("[...document.querySelectorAll('button')].find(b=>/Присоединиться|Войти/.test(b.textContent)&&!b.disabled)?.click()")
        S.check("гость вошёл", await G.wait_for("document.querySelectorAll('.rbtn').length>3", 40))
        await asyncio.sleep(3)
        S.check("гость: и после входа микрофон и камера не запрашивались", await G.js("window.__gum") == 0)

        # ------------------------------------------------------------------------------ обычная комната
        B = S.Client("bob", "bob", "bob-pass", 9565)
        await B.start()
        await B.goto("/rooms/sales", 2.5)
        t = await prejoin_text(B)
        S.check("обычная комната: прежняя надпись «Микрофон включится сразу» и проверка оборудования", "Микрофон включится сразу" in t and await B.js("!!document.querySelector('.precheck')"), t[:200].replace(chr(10), " | "))
        S.check("обычная комната: выбора «Микрофон при входе» нет", not await B.js("!!document.querySelector('.pj-micstate')"))
        await B.close()
    finally:
        for c in (R, A, G):
            try: await c.close()
            except Exception: pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
