"""Плавающее окно встречи за настоящей политикой CSP сервера (nginx образа web): применены ли стили окна и что в нём показано.

Встроенный <style> в окне Document Picture-in-Picture закрыт политикой страницы (style-src 'self'), поэтому окно подключает файл стилей. Сценарий scenario_auto_pip.py
идёт на сервер разработки без CSP и этого не видит — эта проверка идёт на собранный стенд. Один участник (сам администратор), данные синтетические.
  ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox PEREGOVORKA_URL=http://localhost:28480 python pip_window_check.py"""
import asyncio
import json
import os
import time

import httpx

import smoke_room as S

BASE = os.environ.get("PEREGOVORKA_URL", "http://localhost:28480")
HOOK = """
window.__pipH = null;
if (navigator.mediaSession) { const orig = navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
  navigator.mediaSession.setActionHandler = (a, h) => { if (a === 'enterpictureinpicture') window.__pipH = h; try { orig(a, h); } catch (e) {} }; }
"""
S.INIT += HOOK
PIPDOC = "documentPictureInPicture.window?.document"


def admin() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=90)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": sorted(d["sessions"], key=lambda s: s["created_at"])[0]["id"]})
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


class Viewer(S.Client):
    """Браузер входит по уже полученной сессии администратора (куки передаются в браузер)."""
    cookie = ""

    async def start(self):
        try:
            await S.Client.start(self)
        except AssertionError:
            pass
        await self.send("Network.setCookie", name="vm_session", value=self.cookie, url=BASE, httpOnly=True)


async def shot(c, name):
    """Снимок окна: это отдельная вкладка браузера (target) со своим адресом отладки."""
    import base64
    import urllib.request

    import websockets
    t = [x for x in json.load(urllib.request.urlopen(f"http://127.0.0.1:{c.port}/json")) if x.get("type") == "page" and "/rooms/" not in x.get("url", "") and x.get("webSocketDebuggerUrl")]
    for x in t:
        async with websockets.connect(x["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "!!document.querySelector('.pip')", "returnByValue": True}}))
            if not json.loads(await ws.recv()).get("result", {}).get("result", {}).get("value"):
                continue
            await ws.send(json.dumps({"id": 3, "method": "Emulation.setDeviceMetricsOverride", "params": {"width": 400, "height": 250, "deviceScaleFactor": 2, "mobile": False}}))   # настоящий размер окна (в headless оно другое)
            await ws.recv()
            await asyncio.sleep(0.6)
            await ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == 2:
                    os.makedirs(S.OUT, exist_ok=True)
                    path = os.path.join(S.OUT, f"{name}.png")
                    open(path, "wb").write(base64.b64decode(r["result"]["data"]))
                    return path
    return None


async def main():
    api = admin()
    slug = f"pip-{time.strftime('%H%M%S')}"
    rid = api.post("/api/v1/admin/rooms", json={"name": "Совещание отдела", "slug": slug, "room_type": "regular", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    S.ROOM = f"/rooms/{slug}"
    v = Viewer("pipcsp", "admin", "-", 9861)
    v.cookie = api.headers["Cookie"].split("=", 1)[1]
    try:
        await v.start()
        S.check("вошёл во встречу", await v.join())
        await v.wait_for("document.querySelectorAll('.tile').length>=1", 40)
        S.check("страница зарегистрировала автопереход", await v.wait_for("typeof window.__pipH === 'function'", 10))
        csp = await v.js("fetch('/', {method: 'HEAD'}).then(r => r.headers.get('content-security-policy') || '')")
        S.check("страница отдана с боевой CSP (style-src без unsafe-inline)", "style-src 'self'" in (csp or "") and "unsafe-inline" not in (csp or "").split("style-src")[-1].split(";")[0], (csp or "")[:60])
        await v.send("Runtime.evaluate", expression="Promise.resolve(window.__pipH({action: 'enterpictureinpicture'}))", awaitPromise=True, userGesture=True)
        S.check("окно открылось", await v.wait_for(f"!!{PIPDOC}?.querySelector('.pip')", 10))
        await asyncio.sleep(1.5)
        css = await v.js(f"(() => {{ const d = {PIPDOC}; const p = d.querySelector('.pip'); return p ? {{pos: getComputedStyle(p).position, bg: getComputedStyle(d.body).backgroundColor, font: getComputedStyle(d.body).fontFamily.slice(0, 12), btn: getComputedStyle(d.querySelector('.btns button') || d.body).borderRadius}} : null; }})()")
        S.check("стили окна применены (не голый HTML)", bool(css) and css["pos"] == "relative" and css["bg"] not in ("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)") and css["btn"] != "0px", str(css))
        S.check("заголовок, время и участники на месте", bool(await v.js(f"!!({PIPDOC}.querySelector('.top b')?.textContent && /\\d\\d:\\d\\d:\\d\\d/.test({PIPDOC}.querySelector('.meta')?.textContent))")))
        names = await v.js(f"[...{PIPDOC}.querySelectorAll('.btns button')].map(b => b.getAttribute('aria-label') || b.textContent.trim())")
        S.check("кнопки: «Микрофон», «Камера», «Выйти»", names == ["Микрофон", "Камера", "Выйти"], str(names))
        print("снимок окна:", await shot(v, "pip-window"))
    finally:
        await v.close()
        api.delete(f"/api/v1/admin/rooms/{rid}")
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
