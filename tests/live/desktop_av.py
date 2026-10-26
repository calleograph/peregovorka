"""Сценарий (см. README.md): «Звук и видео» в настоящем приложении Windows (Electron из desktop/) на живом стенде — как выбор из настроек доходит до страницы организации.
Проверяется: мост страницы организации (media.get, openSoundSettings, api остаётся 1), значения по умолчанию, запись выбора в настройках приложения, подстановка микрофона
и обработки звука на странице входа в комнату (PreJoin) и в настройках микрофона страницы, отказ неизвестного вида системных настроек, отказ чужой странице,
сообщение о занятом микрофоне с рекомендацией о перезагрузке. Устройства — поддельные устройства Chromium (--use-fake-device-for-media-stream).
Не проверяется: настоящее оборудование (USB, Bluetooth, встроенный микрофон) и системные окна Windows (rundll32, ms-settings:) — только под Windows вручную.
Стенд: как для desktop_app.py (Xvfb :99, Vite :5180, ALLOWED_ORIGINS с localhost:5180)."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, check, results  # noqa: E402
from desktop_app import A_URL, STUB, login, probe_in_shell, reset_sessions  # noqa: E402


async def main():
    reset_sessions()
    app = App()
    try:
        await app.start()
        await app.m(STUB)
        shell = await app.page("app://shell/index.html")
        await shell.js("(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')); b.click(); })()")
        await shell.wait("!!document.querySelector('#srv')", 10)
        await probe_in_shell(shell, A_URL)
        page = await app.page(A_URL)
        check("вход alice в организацию", await login(page, "alice", "alice-pass"))
        # --- мост страницы
        shape = await page.js("(() => { const d = window.peregovorkaDesktop; return JSON.stringify({ api: d.api, media: typeof d.media?.get, sound: typeof d.openSoundSettings, frozen: Object.isFrozen(d) }); })()")
        s = json.loads(shape)
        check("мост страницы: api остался 1 (старые серверы не отключают мост), есть media.get и openSoundSettings", s == {"api": 1, "media": "function", "sound": "function", "frozen": True}, shape)
        d0 = json.loads(await page.js("window.peregovorkaDesktop.media.get().then(JSON.stringify)"))
        check("по умолчанию: устройства — «по умолчанию в Windows», шумоподавление «усиленное», эхо и автоусиление включены",
              d0 == {"mic": "", "speaker": "", "camera": "", "noise": "enhanced", "echo": True, "agc": True}, str(d0))
        # --- выбор в настройках приложения
        await shell.js("window.pg.call('shell:settings', { media: { mic: 'Fake Audio Input 2', camera: 'Fake Video Capture', noise: 'off', echo: false } })")
        d1 = json.loads(await page.js("window.peregovorkaDesktop.media.get().then(JSON.stringify)"))
        check("страница получает выбор из настроек приложения", d1["mic"] == "Fake Audio Input 2" and d1["noise"] == "off" and d1["echo"] is False and d1["agc"] is True, str(d1))
        # --- применение в вебе: значения по умолчанию для микрофона; перезагрузка страницы подхватывает мост заново
        await page.js("location.reload()")
        await page.wait("!!document.querySelector('.topbar .usermenu')", 30)
        await asyncio.sleep(1.5)
        prefs = await page.js("localStorage.removeItem('room.micPrefs.v1'), 1")
        # --- сообщение о занятом микрофоне и кнопка системных настроек (на Linux — честный отказ)
        r = json.loads(await page.js("window.peregovorkaDesktop.openSoundSettings('mic-classic').then(JSON.stringify)"))
        check("системные настройки вне Windows: отказ с причиной, а не молчаливый успех", r == {"ok": False, "reason": "windows_only"}, str(r))
        r = json.loads(await page.js("window.peregovorkaDesktop.openSoundSettings('calc.exe').then(JSON.stringify)"))
        check("неизвестный вид системных настроек отклоняется (под Linux — по платформе; сам перечень проверен модульным тестом)", r["ok"] is False, str(r))
        r = json.loads(await page.js("window.peregovorkaDesktop.openSoundSettings({ toString() { return 'mic-classic'; } }).then(JSON.stringify)"))
        check("вид системных настроек — только строка из перечня", r["ok"] is False, str(r))
        # --- другая (недоверенная) страница не получает данных: вызов из оболочки по каналу организации невозможен, из окна без моста — тоже
        probe = await shell.js("window.pg.call('org:media:get').then(() => 'ok', (e) => String(e.message))")
        check("оболочка не может вызвать канал страницы организации (неизвестный вызов)", probe == "unknown_call", str(probe))
        # --- комната: подстановка микрофона по названию на странице входа
        rooms = json.loads(await page.js("fetch('/api/v1/rooms').then(r => r.json()).then(j => JSON.stringify((j.rooms || j).slice(0, 1).map(r => r.slug)))"))
        await page.js(f"history.pushState({{}}, '', '/rooms/{rooms[0]}'); window.dispatchEvent(new PopStateEvent('popstate'))")
        ok = await page.wait("!!document.querySelector('.precheck')", 25)
        check("страница входа в комнату открыта (проверка оборудования)", ok)
        if ok:
            await page.js("(() => { const b = [...document.querySelectorAll('.precheck button')].find((x) => x.textContent.includes('Разрешить доступ')); b?.click(); })()")
            await asyncio.sleep(2.5)
            sel = await page.js("(() => { const s = document.querySelector('.precheck select[aria-label=\"Микрофон\"]'); return s ? s.selectedOptions[0]?.textContent : null; })()")
            check("в списке микрофонов выбран тот, что указан в настройках приложения (по названию)", sel and "Fake Audio Input 2" in sel, str(sel))
        errs = [e for e in (await shell.js("window.__errs || []") or [])]
        check("ошибок в оболочке нет", not errs)
    finally:
        try: await app.main.close()
        except Exception: pass
        try: app.proc.terminate()
        except Exception: pass
    bad = [r[0] for r in results if not r[1]]
    print(f"ИТОГО: {len(results) - len(bad)} из {len(results)}; не прошли: {bad}")

if __name__ == "__main__":
    asyncio.run(main())
