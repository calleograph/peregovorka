"""Сценарий (см. README.md): исправления приложения Windows, проверяемые в настоящем Electron на живом стенде —
значки комнат с двух организаций (свой сервер у каждой, запасной значок при сбое, журнал сбоя), крестик окна чатов, диктовка в поле сообщения
(смена устройств не обрывает диктовку, «Остановить и отправить», микрофон освобождается), автозапуск по умолчанию у нового профиля.
Стенд: как для desktop_app.py (Xvfb :99, Vite :5180, ALLOWED_ORIGINS с localhost:5180). Распознавание речи — подставная служба стенда (/__dev/fake_asr):
это проверяет цепочку клиента, но НЕ настоящий ASR (GigaAM); с настоящим — вручную."""
import asyncio, glob, http.cookiejar, io, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, check, results  # noqa: E402
from desktop_app import A_URL, B_URL, STUB, login, probe_in_shell, reset_sessions  # noqa: E402

ROOM_IMG = "[...document.images].filter(i => i.className.includes('room-icon'))"


def upload_room_icon(origin):
    from PIL import Image
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    hdr = {"Content-Type": "application/json", "Origin": origin}
    csrf = json.load(op.open(urllib.request.Request(origin + "/api/v1/auth/login", data=json.dumps({"login": "root", "password": "root-pass"}).encode(), headers=hdr)))["csrf_token"]
    rooms = json.load(op.open(origin + "/api/v1/rooms"))
    room = (rooms if isinstance(rooms, list) else rooms["rooms"])[0]
    b = io.BytesIO(); Image.new("RGB", (300, 300), (200, 30, 30)).save(b, "PNG")
    op.open(urllib.request.Request(f"{origin}/api/v1/rooms/{room['id']}/icon", data=b.getvalue(), method="PUT", headers={"Content-Type": "image/png", "Origin": origin, "X-CSRF-Token": csrf}))
    op.open(urllib.request.Request(origin + "/api/v1/auth/logout", method="POST", headers={**hdr, "X-CSRF-Token": csrf}))


async def add_org(app, shell, url, name):
    await shell.js("(document.querySelector('button[title=\"Подключить организацию\"]') && [...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')) || [...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')) || document.querySelector('button[title=\"Подключить организацию\"]')).click()")
    await shell.wait("!!document.querySelector('#srv') && !!document.querySelector('#nm')", 10)
    await shell.js(f"document.querySelector('#nm').value = {json.dumps(name)}")
    await probe_in_shell(shell, url)
    return await app.page(url)


async def main():
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/__dev/fake_asr", method="POST"))
    reset_sessions()
    upload_room_icon(A_URL)
    app = App()
    try:
        await app.start()
        await app.m(STUB)
        check("новый профиль: автозапуск по умолчанию «свёрнутым в трей»", await app.m("return pg.ctx.eff().autostart") == "tray")
        shell = await app.page("app://shell/index.html")
        pa = await add_org(app, shell, A_URL, "Орг А")
        check("вход alice в организацию А", await login(pa, "alice", "alice-pass"))
        pb = await add_org(app, shell, B_URL, "Орг Б")
        check("вход alice в организацию Б", await login(pb, "alice", "alice-pass"))
        await asyncio.sleep(3)
        # ---------- значки комнат
        for name, page, origin in (("А", pa, A_URL), ("Б", pb, B_URL)):
            r = json.loads(await page.js(f"JSON.stringify({ROOM_IMG}.map(i => ({{ok: i.complete && i.naturalWidth > 0, own: i.src.startsWith('{origin}/api/')}})))"))
            check(f"организация {name}: загруженные значки комнат показаны, адреса — её собственного сервера", bool(r) and all(x["ok"] and x["own"] for x in r), str(r))
            check(f"организация {name}: нет «сломанных» картинок и адресов app://", await page.js("[...document.images].every(i => !i.complete || i.naturalWidth > 0) && ![...document.images].some(i => i.src.startsWith('app:'))"))
        await app.m(f"pg.orgs.activate(pg.orgs.list.find(o => o.rec.origin === '{A_URL}').rec.id); return 1")       # скрытая страница картинки не грузит (ленивая загрузка) — как и в браузере
        # сбой: файл значка пропал на сервере → запасной значок вместо сломанной картинки, сбой записан в журнал приложения
        files = glob.glob(os.path.expanduser("~/pg-work/livedata/room-icons/*.webp"))
        for f in files: os.rename(f, f + ".bak")
        try:
            await pa.send("Network.enable")
            await pa.send("Network.clearBrowserCache")       # значок «навечно» в кэше (immutable): без очистки перезагрузка показала бы его из кэша
            await pa.js("location.reload()")
            await pa.wait("!!document.querySelector('.room-card, .rc-mark')", 20)
            await pa.js("document.querySelectorAll('img.room-icon').forEach(i => i.scrollIntoView({block: 'center'}))")       # картинки грузятся лениво: пока не в зоне видимости, запроса нет
            await pa.wait("![...document.images].some(i => i.className.includes('room-icon'))", 20)
            st = await pa.js("JSON.stringify({ih: innerHeight, sy: scrollY, sh: document.documentElement.scrollHeight, imgs: [...document.images].filter(i => i.className.includes('room-icon')).map(i => i.src.slice(-34) + ' complete=' + i.complete + ' w=' + i.naturalWidth + ' top=' + Math.round(i.getBoundingClientRect().top)), std: document.querySelectorAll('.room-icon.std').length, scrollers: [...document.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 50 && getComputedStyle(e).overflowY !== 'visible').map(e => e.className).slice(0, 3)})")
            check("значок недоступен: вместо сломанной картинки — стандартный значок", await pa.js("[...document.images].filter(i => i.className.includes('room-icon')).length === 0 && document.querySelectorAll('.room-icon.std').length > 0"), st)
            check("сбой загрузки записан в журнал приложения (без параметров адреса)", "page.image_failed" in app.log() and "?v=" not in "".join(l for l in app.log().splitlines() if "image_failed" in l))
        finally:
            for f in files: os.rename(f + ".bak", f)
        # ---------- окно чатов: крестик в углу окна
        await pa.js("location.reload()")
        await pa.wait("!!document.querySelector('.chat-nav')", 20)
        await pa.js("document.querySelector('.chat-nav').click()")
        check("окно чатов открыто", await pa.wait("!!document.querySelector('.chat-win')", 10))
        g = json.loads(await pa.js("(() => { const w = document.querySelector('.chat-win').getBoundingClientRect(), c = document.querySelector('.chat-win > .fw-close').getBoundingClientRect(); return JSON.stringify({top: Math.round(c.top - w.top), right: Math.round(w.right - c.right)}); })()"))
        check("крестик чатов — в правом верхнем углу окна, отступы равны", g["top"] == g["right"] and 6 <= g["top"] <= 12, str(g))
        await app.shot_window("fixes-chats")
        # ---------- диктовка
        await pa.js("document.querySelector('[aria-label=\"Новый чат\"]').click()")
        await pa.wait("!!document.querySelector('[aria-label=\"Поиск сотрудников\"]')", 5)
        await pa.js("document.querySelector('[aria-label=\"Поиск сотрудников\"]').focus()")
        await pa.send("Input.insertText", text="Борис")
        await pa.wait("[...document.querySelectorAll('.people-row')].some(r => r.innerText.includes('Борис Мартынов'))", 8)
        await pa.js("[...document.querySelectorAll('.people-row')].find(r => r.innerText.includes('Борис Мартынов')).click()")
        await pa.wait("!!document.querySelector('.composer textarea')", 8)
        await pa.js("window.__tr = []; const G = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia = async c => { const s = await G(c); window.__tr.push(...s.getTracks()); return s; }")
        await pa.js("document.querySelector('.composer textarea').focus()")
        await pa.send("Input.insertText", text="Вручную: ")
        await pa.js("document.querySelector('[aria-label=\"Диктовка\"]').click()")
        check("диктовка началась", await pa.wait("document.querySelector('.dictation')?.innerText.includes('Идёт диктовка')", 10))
        await asyncio.sleep(1.5)
        await pa.js("navigator.mediaDevices.dispatchEvent(new Event('devicechange'))")
        check("событие смены устройств не обрывает диктовку", await pa.wait("/фраза 1/i.test(document.querySelector('.composer textarea').value) && !!document.querySelector('.dictation')", 12), await pa.js("document.querySelector('.composer textarea').value"))
        check("распознанный текст — в поле ввода, ручной текст сохранён", await pa.js("document.querySelector('.composer textarea').value.startsWith('Вручную:')"))
        await pa.js("document.querySelector('.dictation [aria-label^=Остановить]').click()")
        check("«Остановить и отправить»: сообщение с распознанным текстом ушло", await pa.wait("[...document.querySelectorAll('.msg-text')].some(t => t.innerText.startsWith('Вручную:') && /фраза/i.test(t.innerText))", 15))
        check("микрофон освобождён", await pa.wait("window.__tr.length > 0 && window.__tr.every(t => t.readyState === 'ended')", 5))
    finally:
        await app.stop(False)
    bad = [r for r in results if not r[1]]
    print(f"ИТОГО: {len(results) - len(bad)} из {len(results)}; не прошли: {[b[0] for b in bad]}")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    asyncio.run(main())
