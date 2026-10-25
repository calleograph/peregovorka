"""Сценарий (см. README.md): встроенная мини-игра в приложении Windows (настоящий Electron на стенде) — Alt+M в окне организации,
один экземпляр, переходы между разделами без потери окна, настройки хранятся отдельно у каждой организации (свой раздел данных), закрытие освобождает окно.
Стенд как для desktop_fixes.py."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, check, results  # noqa: E402
from desktop_app import A_URL, B_URL, STUB, login, reset_sessions  # noqa: E402
from desktop_fixes import add_org  # noqa: E402

SR = "document.querySelector('[data-crystals]').shadowRoot"


async def altm(page):
    await page.send("Input.dispatchKeyEvent", type="rawKeyDown", modifiers=1, code="KeyM", key="m", windowsVirtualKeyCode=77)
    await page.send("Input.dispatchKeyEvent", type="keyUp", modifiers=1, code="KeyM", key="m", windowsVirtualKeyCode=77)
    await page.wait("document.querySelectorAll('[data-crystals]').length >= 1", 8)
    await asyncio.sleep(0.5)


async def main():
    reset_sessions()
    app = App()
    try:
        await app.start()
        await app.m(STUB)
        shell = await app.page("app://shell/index.html")
        pa = await add_org(app, shell, A_URL, "Орг А")
        check("вход alice в организацию А", await login(pa, "alice", "alice-pass"))
        pb = await add_org(app, shell, B_URL, "Орг Б")
        check("вход alice в организацию Б", await login(pb, "alice", "alice-pass"))
        await app.m(f"pg.orgs.activate(pg.orgs.list.find(o => o.rec.origin === '{A_URL}').rec.id); return 1")
        await asyncio.sleep(1.5)
        check("до нажатия окна игры нет", await pa.js("document.querySelectorAll('[data-crystals]').length") == 0)
        await altm(pa)
        check("Alt+M в окне организации: игра открылась", await pa.js("document.querySelectorAll('[data-crystals]').length") == 1)
        await altm(pa)
        check("повторный Alt+M — тот же экземпляр", await pa.js("document.querySelectorAll('[data-crystals]').length") == 1)
        check("окно открылось отдельным слоем страницы, а не окном системы", await pa.js(f"getComputedStyle({SR}.querySelector('.win')).position") == "fixed")
        await pa.js(f"{SR}.querySelector('[data-go=settings]').click()"); await asyncio.sleep(0.4)
        await pa.js(f"{SR}.querySelector('[data-q=mid]').click()"); await asyncio.sleep(0.3)
        await pa.js(f"{SR}.querySelector('.settings .back').click()"); await asyncio.sleep(0.3)
        await pa.js(f"{SR}.querySelector('.play').click()"); await asyncio.sleep(2.0)
        await pa.js("[...document.querySelectorAll('.rail-nav a')].find(a => a.getAttribute('href') === '/history')?.click()"); await asyncio.sleep(1.2)
        check("переход между разделами: окно и партия на месте", await pa.js(f"document.querySelectorAll('[data-crystals]').length") == 1 and await pa.js(f"!{SR}.querySelector('.game').classList.contains('off')"))
        check("у организации А настройки игры записаны", await pa.js("localStorage.getItem('crystals.settings.v2')") is not None)
        check("у организации Б данных игры нет (разделы данных не смешиваются)", await pb.js("Object.keys(localStorage).filter(k => k.startsWith('crystals.')).length") == 0)
        await pa.js(f"{SR}.querySelector('.cls').click()"); await asyncio.sleep(0.4)
        check("закрытие убирает окно", await pa.js("document.querySelectorAll('[data-crystals]').length") == 0)
        await altm(pa)
        check("повторное открытие работает, настройки на месте", await pa.js(f"(async()=>{{ {SR}.querySelector('[data-go=settings]').click(); await new Promise(r=>setTimeout(r,400)); return {SR}.querySelector('[data-q=mid]').getAttribute('aria-pressed') }})()") == "true")
    finally:
        try: await app.stop(False)
        except Exception: pass
    bad = [n for n, ok, _ in results if not ok]
    print(f"\nитог: {len(results) - len(bad)} из {len(results)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
