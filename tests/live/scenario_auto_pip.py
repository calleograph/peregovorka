"""Сценарий (см. README.md): плавающее окно встречи при уходе со вкладки (Chrome/Edge — Document Picture-in-Picture).

Браузер сам вызывает действие Media Session «enterpictureinpicture», когда человек переключает вкладку со встречей (микрофон включён). В автоматическом
браузере переключения вкладок нет — сценарий перехватывает зарегистрированный страницей обработчик и вызывает его так же, как браузер: без нажатия на
странице (CDP userGesture — только чтобы headless-окно открылось; настоящий автопереход жеста не требует).
Проверяется: обработчик зарегистрирован только во встрече; окно открывается; в нём видео собеседника с идущими кадрами и кнопки, время, участники; «Микрофон» в окне
выключает и включает микрофон во встрече; возврат на вкладку закрывает окно; «Выйти» в окне завершает участие; после выхода обработчик снят.
Запасной путь (браузер без Document PiP): системное окно для видео — главное видео страницы.
"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
S.ROOM = "/rooms/support"
HOOK = """
window.__pipH = null;
if (navigator.mediaSession) { const orig = navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
  navigator.mediaSession.setActionHandler = (a, h) => { if (a === 'enterpictureinpicture') window.__pipH = h; try { orig(a, h); } catch (e) {} }; }
"""
S.INIT += HOOK + """
navigator.mediaDevices.getDisplayMedia = async function () {
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d'); let n = 0;
  setInterval(() => { g.fillStyle = '#23405e'; g.fillRect(0, 0, 1280, 720); g.fillStyle = '#fff'; g.font = '72px sans-serif'; g.fillText('ЭКРАН БОРИСА ' + (n++), 80, 380); }, 100);
  return c.captureStream(10);
};
"""
NO_DOC_PIP = "\ndelete window.documentPictureInPicture; Object.defineProperty(window, 'documentPictureInPicture', {value: undefined});\n"
PIPDOC = "documentPictureInPicture.window?.document"
# кадры видео в плавающем окне: размер известен и счётчик кадров растёт
FRAMES = """(async () => { const v = %s?.querySelector('.pip video'); if (!v) return {video: false};
  const q = () => v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality().totalVideoFrames : 0; const a = q(); await new Promise(r => setTimeout(r, 1500));
  return {video: true, w: v.videoWidth, h: v.videoHeight, grew: q() > a}; })()""" % PIPDOC


async def pip_shot(c, name):
    """Снимок самого плавающего окна: это отдельная вкладка браузера (target) со своим адресом отладки."""
    import urllib.request, websockets, base64
    t = [x for x in json.load(urllib.request.urlopen(f"http://127.0.0.1:{c.port}/json")) if x.get("type") == "page" and "/rooms/" not in x.get("url", "") and x.get("webSocketDebuggerUrl")]
    for x in t:
        async with websockets.connect(x["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "!!document.querySelector('.pip')", "returnByValue": True}}))
            if not json.loads(await ws.recv()).get("result", {}).get("result", {}).get("value"): continue
            await ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == 2: open(os.path.join(S.OUT, f"{c.label}-{name}.png"), "wb").write(base64.b64decode(r["result"]["data"])); return True
    return False


async def gesture(c, expr):
    r = await c.send("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True, userGesture=True)
    return r.get("result", {}).get("value")


async def main():
    A = S.Client("pip-alice", "alice", "alice-pass", 9651)
    B = S.Client("pip-bob", "bob", "bob-pass", 9652)
    try:
        for c in (A, B):
            await c.start()
        S.check("до входа во встречу обработчика нет", await A.js("window.__pipH === null"))
        for c in (A, B):
            S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        await B.click_btn("Камера выкл."); await asyncio.sleep(3)
        S.check("во встрече страница зарегистрировала автопереход в окно", await A.wait_for("typeof window.__pipH === 'function'", 10))
        S.check("Document PiP в браузере есть", await A.js("'documentPictureInPicture' in window"))
        await gesture(A, "Promise.resolve(window.__pipH({action: 'enterpictureinpicture'})).then(() => new Promise(r => setTimeout(r, 1500)))")
        S.check("окно открылось", await A.wait_for(f"!!{PIPDOC}?.querySelector('.pip')", 10))
        f = await A.js(FRAMES)
        S.check("в окне видео собеседника, кадры идут", f.get("video") and f.get("w", 0) > 0 and f.get("grew"), json.dumps(f))
        names = await A.js(f"[...{PIPDOC}.querySelectorAll('.btns button')].map(b => b.getAttribute('aria-label') || b.textContent.trim())")
        S.check("кнопки окна: «Микрофон», «Камера» и «Выйти»", names == ["Микрофон", "Камера", "Выйти"], str(names))
        # стили окна применены (за политикой CSP страницы встроенный <style> блокируется: без файла стилей окно выглядит голым HTML)
        css = await A.js(f"(() => {{ const d = {PIPDOC}; const p = d.querySelector('.pip'); return p ? {{pos: getComputedStyle(p).position, bg: getComputedStyle(d.body).backgroundColor, font: getComputedStyle(d.body).fontFamily.slice(0, 20)}} : null; }})()")
        S.check("стили окна применены (не голый HTML)", bool(css) and css["pos"] == "relative" and css["bg"] != "rgba(0, 0, 0, 0)" and css["bg"] != "rgb(255, 255, 255)", str(css))
        S.check("в окне есть заголовок встречи, время и число участников", bool(await A.js(f"!!({PIPDOC}.querySelector('.top b')?.textContent && {PIPDOC}.querySelector('.meta')?.textContent.match(/\\d\\d:\\d\\d:\\d\\d/))")))
        S.check("в окне — имя собеседника", "Борис" in (await A.js(f"{PIPDOC}.querySelector('.who')?.textContent || ''") or ""))
        await A.shot("main-while-pip")
        mic0 = await A.js("!!document.querySelector('button[aria-pressed=\"true\"][aria-label*=\"икрофон\"]')")
        await A.js(f"{PIPDOC}.querySelector('.btns button').click()"); await asyncio.sleep(2)
        mic1 = await A.js("!!document.querySelector('button[aria-pressed=\"true\"][aria-label*=\"икрофон\"]')")
        S.check("«Микрофон» в окне переключает микрофон встречи", mic0 != mic1, f"{mic0}→{mic1}")
        S.check("кнопка в окне показывает новое состояние", await A.js(f"{PIPDOC}.querySelector('.btns button').getAttribute('aria-pressed')") == ("true" if mic1 else "false"))
        await A.js(f"{PIPDOC}.querySelector('.btns button').click()"); await asyncio.sleep(1.5)
        # возврат на вкладку: окно закрывается само (браузер присылает visibilitychange с visible)
        await A.js("document.dispatchEvent(new Event('visibilitychange'))")
        S.check("возврат на вкладку закрывает окно", await A.wait_for("!documentPictureInPicture.window", 5))
        S.check("встреча продолжается", await A.js("document.querySelectorAll('.tile').length >= 2"))
        # снова в окно и «Выйти» из окна
        await gesture(A, "Promise.resolve(window.__pipH({action: 'enterpictureinpicture'})).then(() => new Promise(r => setTimeout(r, 1500)))")
        S.check("окно открывается повторно", await A.wait_for(f"!!{PIPDOC}?.querySelector('.pip')", 10))
        # собеседник показывает экран — окно само переключается на демонстрацию (целиком, без обрезки)
        await B.click_btn("Показать экран")
        S.check("демонстрация собеседника — в окне", await A.wait_for(f"{PIPDOC}?.querySelector('.pip video.fit')?.videoWidth > 0", 20))
        f = await A.js(FRAMES)
        S.check("кадры демонстрации в окне идут", f.get("grew") and f.get("w", 0) >= 1280, json.dumps(f))
        S.check("снимок окна", await pip_shot(A, "pip-window-screen"))
        await B.click_btn("Остановить показ")
        S.check("показ закончился — в окне снова камера собеседника", await A.wait_for(f"!!{PIPDOC}?.querySelector('.pip video.fill')", 15))
        S.check("снимок окна (камера)", await pip_shot(A, "pip-window-camera"))
        await A.js(f"{PIPDOC}.querySelector('.btns button.leave').click()")
        S.check("«Выйти» в окне — участник вышел из встречи", await A.wait_for("!location.pathname.startsWith('/rooms/')", 15), await A.js("location.pathname"))
        S.check("после выхода окно закрыто и обработчик снят", await A.wait_for("!documentPictureInPicture.window", 5) and await A.js("window.__pipH === null"))
        S.check("у Бориса Алисы больше нет", await B.wait_for("document.querySelectorAll('.tile').length === 1", 20))

        # запасной путь: браузер без Document PiP — системное окно для видео
        C = S.Client("pip-carol", "alice", "alice-pass", 9653)
        S.INIT = S.INIT + NO_DOC_PIP
        try:
            await C.start()
            S.check("без Document PiP: вошла", await C.join())
            await C.wait_for("document.querySelectorAll('.tile').length>=2", 40); await asyncio.sleep(3)
            await gesture(C, "Promise.resolve(window.__pipH({action: 'enterpictureinpicture'})).then(() => new Promise(r => setTimeout(r, 1500)))")
            el = await C.js("(() => { const v = document.pictureInPictureElement; return v ? {tag: v.tagName, w: v.videoWidth, tile: !!v.closest('.tile, .screen-stage')} : null })()")
            S.check("без Document PiP: в системное окно ушло видео собеседника со страницы", bool(el) and el["tag"] == "VIDEO" and el["w"] > 0 and el["tile"], json.dumps(el))
            await C.js("document.dispatchEvent(new Event('visibilitychange'))")
            S.check("без Document PiP: возврат на вкладку закрывает окно", await C.wait_for("!document.pictureInPictureElement", 5))
        finally:
            await C.close()
        print("errs:", [((await c.state()) or {}).get("errs", [])[:3] for c in (A, B)])
    finally:
        for c in (A, B): await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
