"""Сквозной сценарий приложения Peregovorka для Windows на живом стенде (настоящий Electron под X-сервером, настоящий сервер и LiveKit).

Запуск (Linux): Xvfb на :99, стенд с ALLOWED_ORIGINS="http://127.0.0.1:5180,http://localhost:5180", Vite на 127.0.0.1:5180, `cd desktop && node build.mjs`, затем
    DISPLAY=:99 ~/pg-work/venv/bin/python tests/live/desktop_app.py
Две «организации» — один стенд под двумя адресами (разные origin → разные cookie и разделы Electron). Снимки — tests/live/out/desktop/."""
import asyncio, http.cookiejar, json, os, ssl, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, OUT, check, results  # noqa: E402

A_URL, B_URL = "http://127.0.0.1:5180", "http://localhost:5180"
RUN = str(int(time.time()))        # текст сообщения уникален: непрочитанное прошлых прогонов на стенде не даёт ложного результата
LINK = lambda u: "peregovorka://open?url=" + urllib.parse.quote(u, safe="")  # noqa: E731
STUB = """const E = process.mainModule.require('electron');
globalThis.__opened = []; E.shell.openExternal = async (u) => { globalThis.__opened.push(u); };
globalThis.__dialogs = []; E.dialog.showMessageBox = async (_w, o) => { globalThis.__dialogs.push((o || _w).message); return { response: globalThis.__answer ?? 0, checkboxChecked: false }; };
E.dialog.showMessageBoxSync = (_w, o) => { globalThis.__dialogs.push((o || _w).message); return globalThis.__answer ?? 0; };
return true;"""


def tls_server():
    """HTTPS со самоподписанным сертификатом: приложение должно отказать с понятной причиной, а не отключать проверку."""
    d = tempfile.mkdtemp(prefix="pg-tls-")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{d}/k.pem", "-out", f"{d}/c.pem", "-days", "2", "-subj", "/CN=127.0.0.1/O=Test Untrusted CA"],
                   check=True, capture_output=True)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 18443), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"{d}/c.pem", f"{d}/k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def probe_in_shell(shell, url):
    await shell.js(f"(() => {{ const i = document.querySelector('#srv'); i.value = {json.dumps(url)}; document.querySelector('form').requestSubmit(); }})()")
    await shell.wait("!document.querySelector('form button[type=submit]')?.disabled", 30)
    await asyncio.sleep(0.3)
    return await shell.js("document.querySelector('main').innerText")


async def login(page, user, password):
    await page.wait("!!document.querySelector('input[autocomplete=username]')", 30)
    await page.js(f"""(() => {{
      const set = (el, v) => {{ Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v); el.dispatchEvent(new Event('input', {{ bubbles: true }})); }};
      set(document.querySelector('input[autocomplete=username]'), {json.dumps(user)});
      set(document.querySelector('input[type=password]'), {json.dumps(password)});
      document.querySelector('form').requestSubmit();
    }})()""")
    return await page.wait("!!document.querySelector('.topbar .usermenu')", 30)


async def me(page):
    return await page.js("fetch('/api/v1/auth/me').then(r => r.ok ? r.json().then(j => j.user.sam_account_name) : r.status)")


def reset_sessions(users=("alice", "bob")):
    """Повторные прогоны копят сеансы (лимит — 5 на учётную запись): администратор стенда завершает прежние сеансы участников сценария."""
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    hdr = {"Content-Type": "application/json", "Origin": A_URL}
    csrf = json.load(op.open(urllib.request.Request(A_URL + "/api/v1/auth/login", data=json.dumps({"login": "root", "password": "root-pass"}).encode(), headers=hdr)))["csrf_token"]
    for u in json.load(op.open(A_URL + "/api/v1/admin/users")):
        if u["sam_account_name"] in users:
            op.open(urllib.request.Request(f"{A_URL}/api/v1/admin/users/{u['id']}/sessions", method="DELETE", headers={**hdr, "X-CSRF-Token": csrf}))
    op.open(urllib.request.Request(A_URL + "/api/v1/auth/logout", method="POST", headers={**hdr, "X-CSRF-Token": csrf}))


async def main():
    reset_sessions()
    srv = tls_server()
    app = App()
    try:
        await app.start()
        await app.m(STUB)
        shell = await app.page("app://shell/index.html")
        check("первый запуск: мастер подключения", await shell.wait("!!document.querySelector('#srv')", 15))
        await app.shot_window("01-first-run")

        t = await probe_in_shell(shell, "http://meet.example.org")
        check("HTTP отклоняется понятным сообщением", "защищённый адрес https" in t, t[:120])
        t = await probe_in_shell(shell, "https://127.0.0.1:18999")
        check("недоступный сервер: «Сервер недоступен»", "Сервер недоступен" in t, t[-200:])
        t = await probe_in_shell(shell, "https://127.0.0.1:18443")
        check("недоверенный сертификат: отказ, причина и кем выдан", "Сертификат не доверен" in t and "Test Untrusted CA" in t, t[:300])
        await app.shot_window("02-tls-untrusted")
        t = await probe_in_shell(shell, "https://192.0.2.1")
        check("сервер не отвечает (таймаут) — мастер не зависает", "Сервер не отвечает" in t or "Сервер недоступен" in t, t[-200:])

        # ---------- организация А
        await shell.js(f"document.querySelector('#nm').value = 'Стенд А'")
        await probe_in_shell(shell, A_URL)
        orgs = await app.m("return pg.orgs.list.map(o => ({ id: o.rec.id, name: o.rec.name, origin: o.rec.origin }))")
        check("организация А подключена", len(orgs) == 1 and orgs[0]["name"] == "Стенд А", str(orgs))
        a_id = orgs[0]["id"]
        pa = await app.page(A_URL)
        check("вход alice на странице организации А", await login(pa, "alice", "alice-pass"))
        check("после входа паролем устройство получило сохранённый вход (токен, не пароль)", await app.main.wait(f"!!globalThis.__pg.ctx.trust.get('{a_id}')", 15))
        await pa.send("Page.addScriptToEvaluateOnNewDocument", source="window.__tracks = []; const G = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia = async (c) => { const s = await G(c); window.__tracks.push(...s.getTracks()); return s; };")
        await pa.send("Page.addScriptToEvaluateOnNewDocument", source="window.__ws = []; const W = window.WebSocket; window.WebSocket = function (...a) { const s = new W(...a); window.__ws.push(s); return s; }; window.WebSocket.prototype = W.prototype; Object.assign(window.WebSocket, W);")
        await pa.js("location.reload()")
        await pa.wait("!!document.querySelector('.topbar .usermenu')", 30)
        await asyncio.sleep(1.5)
        check("один сокет событий на страницу (без двойного запуска чатов)", await pa.js("window.__ws.filter(s => s.url.endsWith('/api/v1/ws') && s.readyState === 1).length") == 1)
        await app.shot_window("03-org-a")

        # ---------- безопасность страницы организации
        keys = await pa.js("Object.keys(window.peregovorkaDesktop).sort().join(',')")
        check("мост страницы — только api/version/platform/notify/setState/onCommand", keys == "api,notify,onCommand,platform,setState,version", keys)
        check("на странице нет Node.js (require/process)", await pa.js("typeof require === 'undefined' && typeof process === 'undefined' && typeof module === 'undefined'"))
        await pa.js("window.open('https://example.org/x', '_blank')")
        await asyncio.sleep(0.8)
        opened = await app.m("return globalThis.__opened")
        check("внешняя ссылка — в системный браузер, не в окне приложения", opened == ["https://example.org/x"], str(opened))
        await pa.js("window.open('file:///C:/Windows/System32/calc.exe')")
        await pa.js("window.open('javascript:alert(1)')")
        await asyncio.sleep(0.5)
        check("file: и javascript: не открываются вовсе", await app.m("return globalThis.__opened.length") == 1)
        await pa.js("setTimeout(() => { location.href = 'https://example.org/phish'; }, 0)")
        await asyncio.sleep(1.2)
        check("переход страницы на чужой адрес заблокирован", (await pa.js("location.origin")) == A_URL)
        check("веб-уведомления страницы запрещены (только через приложение)", await pa.js("Notification.requestPermission()") == "denied")
        check("микрофон разрешён своему серверу", await pa.js("navigator.mediaDevices.getUserMedia({audio:true}).then(s => { s.getTracks().forEach(t => t.stop()); return true; }).catch(e => e.name)") is True)
        check("геолокация не выдаётся", await pa.js("new Promise(r => navigator.geolocation.getCurrentPosition(() => r('granted'), e => r(e.code)))") == 1)
        check("оболочка: неизвестная команда отклоняется мостом", await shell.js("window.pg.call('shell:evil').then(() => 'ok', e => 'rejected')") == "rejected")
        check("оболочка не получает камеру/микрофон", await shell.js("navigator.mediaDevices.getUserMedia({audio:true}).then(() => 'granted', e => e.name)") != "granted")

        # ---------- организация Б (другой адрес → свой раздел, cookie, сокеты)
        await shell.js("document.querySelector('.side .tool[aria-label=\"Подключить организацию\"]').click()")
        await shell.wait("!!document.querySelector('#srv')", 10)
        await shell.js("document.querySelector('#nm').value = 'Стенд Б'")
        await probe_in_shell(shell, B_URL)
        pb = await app.page(B_URL)
        check("вход bob на странице организации Б", await login(pb, "bob", "bob-pass"))
        check("изоляция: А — alice, Б — bob одновременно", (await me(pa), await me(pb)) == ("alice", "bob"))
        ck = await app.m("""const [a, b] = pg.orgs.list; const ca = await a.ses.cookies.get({}); const cb = await b.ses.cookies.get({});
          return { a: ca.map(c => c.domain), b: cb.map(c => c.domain) };""")
        check("cookie организаций в разных разделах", all("127.0.0.1" in d for d in ck["a"]) and all("localhost" in d for d in ck["b"]) and ck["a"] and ck["b"], str(ck))
        vis = await app.m("return pg.orgs.list.map(o => o.view.getVisible())")
        check("видна только активная организация (Б), А работает в фоне", vis == [False, True], str(vis))

        # ---------- уведомление из неактивной организации
        before = app.log().count("notify.accepted")
        await pb.js("""(async () => { const csrf = (await (await fetch('/api/v1/auth/me')).json()).csrf_token;
          const h = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf };
          const u = (await (await fetch('/api/v1/chats/people?q=alice')).json()).users.find(x => x.login === 'alice');
          const c = await (await fetch('/api/v1/chats/direct', { method: 'POST', headers: h, body: JSON.stringify({ id: u.id, ref: u.ref, login: u.login }) })).json();
          await fetch(`/api/v1/chats/${c.id}/messages`, { method: 'POST', headers: h, body: JSON.stringify({ text: 'Привет из Б ' + '""" + RUN + """', client_id: 'e2e-' + Date.now() }) }); })()""")
        ok = False
        for _ in range(40):
            if app.log().count("notify.accepted") > before:
                ok = True
                break
            await asyncio.sleep(0.25)
        check("сообщение в неактивной организации → уведомление Windows", ok and f'"org":"{a_id}"' in app.log())
        check("непрочитанное видно на плитке организации А", await shell.wait("/^[1-9]\\d*\\+?$/.test(document.querySelector('.orgs .org .badge')?.textContent || '')", 10))
        check("одно сообщение — одно всплывающее уведомление на странице", await pa.wait(f"[...document.querySelectorAll('.chat-toast')].filter(t => t.innerText.includes('{RUN}')).length === 1", 8)
              and await pa.js(f"[...document.querySelectorAll('.chat-toast')].filter(t => t.innerText.includes('{RUN}')).length") == 1)
        await app.shot_window("04-two-orgs-unread")
        n0 = app.log().count("notify.accepted")
        await pa.js("peregovorkaDesktop.notify({ tag: 'dup-1', title: 'x', kind: 'info' }); peregovorkaDesktop.notify({ tag: 'dup-1', title: 'x', kind: 'info' });")
        await asyncio.sleep(0.6)
        check("одно событие — одно уведомление (повтор после переподключения не показывается)", app.log().count("notify.accepted") - n0 <= 1)
        await app.m(f"const o = pg.orgs.byId('{a_id}'); const cid = await o.view.webContents.executeJavaScript(\"fetch('/api/v1/chats').then(r => r.json()).then(j => j.items[0].id)\"); pg.notifier.onClick(o, '/?chat=' + cid); return true;")
        check("нажатие на уведомление открывает нужную организацию и переписку", await pa.wait("!!document.querySelector('.chat-win')", 10)
              and await app.m("return pg.orgs.activeId") == a_id)
        await app.shot_window("05-chat-from-notification")

        # ---------- ссылки peregovorka://
        await app.m(f"await pg.openLink({json.dumps(LINK(B_URL + '/history'))}); return true;")
        check("ссылка приложения переключает организацию и открывает раздел", await pb.wait("location.pathname === '/history'", 10) and await app.m("return pg.orgs.activeId") != a_id)
        await app.m("await pg.openLink('peregovorka://run?cmd=calc'); return true;")
        check("опасная ссылка не выполняется", await shell.wait("document.querySelector('.toast')?.textContent.includes('не открыта')", 5))
        env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
        env["PEREGOVORKA_USER_DATA"] = app.user_data
        p2 = subprocess.run([os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "desktop", "node_modules", "electron", "dist", "electron"), "--no-sandbox", "--e2e",
                             "--allow-http-loopback", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "desktop"), LINK(A_URL + "/history")], env=env, timeout=30, capture_output=True)
        check("второй запуск не создаёт второй экземпляр и передаёт ссылку", p2.returncode == 0 and await pa.wait("location.pathname === '/history'", 10), f"rc={p2.returncode}")

        # ---------- встреча, показ экрана, индикатор, закрытие окна
        await pa.js("location.assign('/rooms/sales')")
        await pa.wait("!!document.querySelector('.pj-actions .btn.primary')", 30)
        await pa.js("[...document.querySelectorAll('.precheck button')].filter(b=>/Разрешить доступ|Проверить микрофон/.test(b.textContent)).forEach(b=>b.click())")
        await asyncio.sleep(0.8)
        for _ in range(3):
            await pa.js("document.querySelector('.pj-actions .btn.primary')?.click()")
            if await pa.wait("document.querySelectorAll('.tile').length > 0", 10):
                break
        check("вход во встречу в приложении (LiveKit)", await app.main.wait(f"globalThis.__pg.orgs.byId('{a_id}').state.inCall === true", 20))
        await asyncio.sleep(2)
        live_audio = await pa.js("window.__tracks.filter(t => t.kind === 'audio' && t.readyState === 'live').length")
        check("после проверки устройств во встрече один микрофон (без дублей захвата)", live_audio <= 1, f"живых аудиодорожек: {live_audio}")
        r = await pa.send("Runtime.evaluate", expression="window.__ds = navigator.mediaDevices.getDisplayMedia({ video: true, audio: true }).then(s => (window.__share = s, s.getVideoTracks()[0].readyState), e => e.name)",
                          userGesture=True, awaitPromise=False)
        picker = await app.page("app://shell/picker.html", 15, fresh=True)
        check("окно выбора экрана открылось", await picker.wait("document.querySelectorAll('.src').length > 0", 15))
        await app.shot_page(picker, "06-screen-picker")
        await picker.js("setTimeout(() => document.querySelector('footer .btn.primary').click(), 0)")
        check("выбранный экран передаётся странице", await pa.js("window.__ds") == "live")
        await pa.js("window.__share.getTracks().forEach(t => t.stop())")
        await pa.send("Runtime.evaluate", expression="window.__ds2 = navigator.mediaDevices.getDisplayMedia({ video: true }).then(() => 'granted', e => e.name)", userGesture=True)
        picker = await app.page("app://shell/picker.html", 15, fresh=True)
        await picker.wait("document.querySelectorAll('.src').length > 0", 15)
        await picker.js("setTimeout(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })), 0)")
        await pa.wait("window.__ds2 !== undefined", 5)
        res2 = await pa.js("window.__ds2")
        check("отмена выбора — страница получает отказ", res2 in ("NotAllowedError", "AbortError", "NotReadableError"), str(res2))
        # показ экрана через кнопку встречи → индикатор → «Остановить» одним нажатием
        await pa.js("[...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || '').includes('Показать экран'))?.click()")
        picker = await app.page("app://shell/picker.html", 15, fresh=True)
        await picker.wait("document.querySelectorAll('.src').length > 0", 15)
        await picker.js("setTimeout(() => document.querySelector('footer .btn.primary').click(), 0)")
        check("показ экрана из встречи идёт", await app.main.wait(f"globalThis.__pg.orgs.byId('{a_id}').state.sharing === true", 20))
        ind = await app.page("app://shell/indicator.html", 15, fresh=True)
        check("индикатор «Идёт показ экрана» поверх окон", await ind.wait("document.body.innerText.includes('Идёт показ экрана')", 10))
        await app.shot_page(ind, "07-share-indicator")
        await ind.js("setTimeout(() => document.querySelector('.btn.stop').click(), 0)")
        check("«Остановить» на индикаторе прекращает показ", await app.main.wait(f"globalThis.__pg.orgs.byId('{a_id}').state.sharing === false", 15))
        await app.m("globalThis.__answer = 0; pg.main.win.close(); return true;")
        await asyncio.sleep(1)
        check("закрытие окна во время встречи спрашивает и сворачивает в трей", await app.m("return !pg.main.win.isVisible() && globalThis.__dialogs.some(m => m.includes('встрече'))"))
        check("встреча продолжается в фоне", await app.m(f"return pg.orgs.byId('{a_id}').state.inCall") is True)
        ind = await app.page("app://shell/indicator.html", 15, fresh=True)
        check("при свёрнутом окне — индикатор встречи", await ind.wait("document.body.innerText.includes('Встреча') || document.body.innerText.includes('встреча')", 10))
        await ind.js("setTimeout(() => [...document.querySelectorAll('.btn')].find(b => b.textContent === 'Открыть').click(), 0)")
        check("«Открыть» на индикаторе возвращает окно", await app.main.wait("globalThis.__pg.main.win.isVisible()", 10))
        await app.m(f"pg.orgs.command(pg.orgs.byId('{a_id}'), {{ type: 'leave-meeting' }}); return true;")
        check("«Покинуть встречу» из приложения", await app.main.wait(f"globalThis.__pg.orgs.byId('{a_id}').state.inCall === false", 15))
        check("после выхода из встречи камера и микрофон освобождены", await pa.wait("window.__tracks.every(t => t.readyState === 'ended')", 10),
              str(await pa.js("window.__tracks.filter(t => t.readyState === 'live').map(t => t.kind)")))

        # ---------- окно: минимальный размер, положение запоминается
        await app.m("pg.main.win.setBounds({ x: 50, y: 40, width: 400, height: 300 }); return true;")
        b = await app.m("return pg.main.win.getBounds()")
        check("окно не меньше минимального размера", b["width"] >= 800 and b["height"] >= 520, str(b))
        await app.m("pg.main.win.setBounds({ x: 120, y: 80, width: 1280, height: 720 }); return true;")
        await asyncio.sleep(0.8)
        for (w, h) in [(1920, 1080), (1600, 900), (1366, 768), (1280, 720), (1024, 768), (800, 520)]:
            await app.m(f"pg.main.win.setBounds({{ x: 0, y: 0, width: {w}, height: {h} }}); return true;")
            await asyncio.sleep(0.6)
            await app.shot_window(f"10-size-{w}x{h}")
        await app.m("pg.main.win.setBounds({ x: 120, y: 80, width: 1280, height: 720 }); return true;")
        await asyncio.sleep(0.8)
        diag = await shell.js("window.pg.call('shell:diag', { devices: null })")
        blob = json.dumps(diag, ensure_ascii=False)
        tokens = await app.m("return pg.orgs.list.map(o => pg.ctx.trust.get(o.rec.id)).filter(Boolean)")
        cookies = await app.m("const all = []; for (const o of pg.orgs.list) for (const c of await o.ses.cookies.get({})) all.push(c.value); return all;")
        check("диагностика без токенов, cookie и текста сообщений", tokens and not any(t in blob for t in tokens) and not any(c in blob for c in cookies if len(c) > 8) and "Привет из Б" not in blob)
        check("диагностика: версии, организации, разрешения", diag["client"]["electron"] and len(diag["organizations"]) == 2 and "camera" in diag["permissions"])
        await shell.js("document.querySelector('.side .tool[aria-label=\"Настройки\"]').click()")
        await shell.wait("!!document.querySelector('.tabs')", 5)
        for tab in ["general", "notify", "orgs", "network", "updates", "diag", "about"]:
            await shell.js(f"document.querySelector('#tab-{tab}').click()")
            await asyncio.sleep(0.7 if tab == "diag" else 0.3)
            await app.shot_window(f"08-settings-{tab}")
        await shell.js("document.querySelector('.settings [aria-label=\"Закрыть настройки\"]').click()")

        # ---------- пороги производительности (базовые замеры и пороги — docs/DESKTOP.md «Производительность»)
        M = "const m = process.mainModule.require('electron').app.getAppMetrics(); return { mb: Math.round(m.reduce((n, x) => n + x.memory.workingSetSize, 0) / 1024), cpu: m.reduce((n, x) => n + x.cpu.percentCPUUsage, 0) };"
        ready = int(app.log().split('app.ready {"ms":')[1].split("}")[0])
        check("запуск до показа окна ≤ 3 с", ready <= 3000, f"{ready} мс")
        sw = await app.m("const t = []; for (let i = 0; i < 10; i++) { const o = pg.orgs.list[i % 2]; const t0 = performance.now(); pg.orgs.activate(o.rec.id); t.push(performance.now() - t0); await new Promise(r => setTimeout(r, 100)); } return Math.max(...t);")
        check("переключение организации ≤ 50 мс (без перезагрузки страниц)", sw <= 50, f"{sw:.1f} мс")
        mem = (await app.m(M))["mb"]
        check("память: две организации в простое ≤ 1500 МБ", mem <= 1500, f"{mem} МБ")
        await app.m("pg.main.win.hide(); return true;")
        await app.m(M)
        await asyncio.sleep(10)
        cpu = (await app.m(M))["cpu"]
        check("процессор в фоне (окно скрыто, две организации) ≤ 5 %", cpu <= 5, f"{cpu:.1f} %")
        await app.m("pg.main.reveal(); return true;")
        heap0 = await shell.js("performance.memory.usedJSHeapSize")
        for _ in range(20):
            await shell.js("document.querySelector('.side .tool[aria-label=\"Настройки\"]').click()")
            for t in ("general", "orgs", "updates", "about"):
                await shell.js(f"document.querySelector('#tab-{t}').click()")
            await shell.js("document.querySelector('.side .org').click()")
        await shell.send("HeapProfiler.collectGarbage")
        heap1 = await shell.js("performance.memory.usedJSHeapSize")
        check("оболочка без утечек: 20 открытий настроек", heap1 - heap0 < 5 * 1048576, f"{(heap1 - heap0) / 1048576:+.1f} МБ")
    finally:
        await app.stop()

    # ---------- перезапуск: положение окна и вход сохраняются
    app2 = App(user_data=app.user_data, port=9342, inspect=9343)
    try:
        await app2.start()
        await app2.m(STUB)
        b = await app2.m("return pg.main.win.getBounds()")
        check("после перезапуска окно на прежнем месте", abs(b["x"] - 120) <= 2 and abs(b["width"] - 1280) <= 2, str(b))
        pa = await app2.page(A_URL)
        pb = await app2.page(B_URL)
        check("после перезапуска обе организации без повторного ввода пароля", await pa.wait("!!document.querySelector('.topbar .usermenu')", 30) and await pb.wait("!!document.querySelector('.topbar .usermenu')", 30))
        a_id = await app2.m("return pg.orgs.list[0].rec.id")
        await app2.m("const o = pg.orgs.list[0]; await o.ses.clearStorageData({ storages: ['cookies'] }); await pg.orgs.start(o); return true;")
        check("сеанс истёк — вход восстановлен токеном устройства без пароля", await pa.wait("!!document.querySelector('.topbar .usermenu')", 30) and app2.log().count("org.resumed") >= 1)
        check("токен повернулся (новый после возобновления)", await app2.m(f"return !!pg.ctx.trust.get('{a_id}')"))
        out = await pa.js("fetch('/api/v1/auth/me').then(r => r.json()).then(j => fetch('/api/v1/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': j.csrf_token } })).then(r => r.status)")
        check("выход на странице организации", out == 204, str(out))
        await app2.m("const o = pg.orgs.list[0]; await o.ses.clearStorageData({ storages: ['cookies'] }); await pg.orgs.start(o); return true;")
        form = await pa.wait("!!document.querySelector('input[autocomplete=username]')", 30)
        gone = await app2.m(f"return !pg.ctx.trust.get('{a_id}')")
        other = await me(pb)
        check("после выхода сохранённый вход отозван: только форма входа этой организации", form and gone and other == "bob", f"form={form} token_gone={gone} b={other}")
        await app2.m("globalThis.__answer = 0; return true;")
        b_id = await app2.m("return pg.orgs.list[1].rec.id")
        await app2.m(f"await pg.orgs.remove('{b_id}'); return true;")
        left = await app2.m("const s = process.mainModule.require('electron').session.fromPartition('persist:org-" + b_id + "'); return (await s.cookies.get({})).length;")
        check("удаление организации стирает её cookie и сохранённый вход", left == 0 and await app2.m(f"return !pg.ctx.trust.get('{b_id}') && pg.orgs.list.length === 1"))
    finally:
        await app2.stop(keep_data=False)

    # ---------- корпоративная политика: преднастроенный сервер, добавление запрещено
    pol = os.path.join(OUT, "policy.json")
    with open(pol, "w", encoding="utf-8") as f:
        json.dump({"Servers": "Корпорация|https://meet.example.invalid", "AllowAddServers": 0, "HideNotificationContent": 1, "Autostart": "tray"}, f, ensure_ascii=False)
    app3 = App(env={"PEREGOVORKA_POLICY_FILE": pol}, port=9344, inspect=9345)
    try:
        await app3.start()
        st = await app3.m("return pg.shellState()")
        check("политика: организация задана администратором, добавление запрещено", len(st["orgs"]) == 1 and st["orgs"][0]["managed"] and not st["canAdd"]
              and "addServers" in st["locked"] and st["settings"]["notifications"]["hideContent"] and st["settings"]["autostart"] == "tray")
        shell = await app3.page("app://shell/index.html")
        check("недоступный сервер: понятное сообщение и повтор", await shell.wait("document.body.innerText.includes('Сервер не найден') && document.body.innerText.includes('Повторная попытка')", 30))
        check("кнопки «Подключить организацию» нет", await shell.js("!document.querySelector('.side .tool[aria-label=\"Подключить организацию\"]')"))
        await app3.shot_window("09-policy-offline")
    finally:
        await app3.stop(keep_data=False)
        srv.shutdown()

    failed = [n for n, ok, _ in results if not ok]
    print(f"ИТОГО: {len(results) - len(failed)} из {len(results)}; не прошли: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
