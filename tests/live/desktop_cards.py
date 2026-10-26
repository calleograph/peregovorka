"""Сценарий (см. README.md): главная страница в настоящем приложении Windows (Electron из desktop/) на живом стенде — кнопки копирования ссылок на карточках и список только актуальных встреч.
Проверяется: у карточки «Предстоящих» кнопки зелёная (сине-зелёная) и красная (коралловая) одного размера на одной линии рядом с «⋯», подсказка при наведении, копирование ссылки
(в буфер или сообщение об отказе — не молчание), отменённая встреча на главной отсутствует. Не проверяется: настоящий Windows (масштабирование экрана, системный буфер).
Стенд: как для desktop_app.py (Xvfb :99, Vite :5180, ALLOWED_ORIGINS с localhost:5180); перед запуском `seed_upcoming.py`."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, check, results  # noqa: E402
from desktop_app import A_URL, STUB, login, probe_in_shell, reset_sessions  # noqa: E402


def allow_guests():
    """Гости во встречах Outlook разрешены на стенде (как у организации, которая их включила)."""
    import httpx
    api = "http://127.0.0.1:8000/api/v1"
    c = httpx.Client(timeout=20)
    c.post(f"{api}/auth/login", json={"login": "root", "password": "root-pass"}).raise_for_status()
    c.headers["X-CSRF-Token"] = c.get(f"{api}/auth/me").json()["csrf_token"]
    c.put(f"{api}/admin/settings/outlook", json={"enabled": True, "guest_links": True}).raise_for_status()
    c.post(f"{api}/auth/logout")                     # сеансов на учётную запись немного: повторные запуски не должны их копить


async def main():
    allow_guests()
    reset_sessions()
    app = App()
    try:
        await app.start()
        await app.m(STUB)
        shell = await app.page("app://shell/index.html")
        await shell.wait("[...document.querySelectorAll('button')].some((b) => b.textContent.includes('Добавить организацию'))", 30)
        await shell.js("(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')); b.click(); })()")
        await shell.wait("!!document.querySelector('#srv')", 10)
        await probe_in_shell(shell, A_URL)
        page = await app.page(A_URL)
        check("вход alice в организацию", await login(page, "alice", "alice-pass"))
        got = await page.wait("document.querySelectorAll('.upc-card').length >= 2", 30)
        check("на главной приложения есть «Предстоящие встречи»", got, str(await page.js("document.body.innerText.slice(0, 300)")))
        g = json.loads(await page.js("""(() => { const c = [...document.querySelectorAll('.upc-card')].find((x) => x.querySelector('.lc-guest')); if (!c) return JSON.stringify(null);
          const r = (s) => { const b = c.querySelector(s).getBoundingClientRect(); return {l: b.left, r: b.right, t: b.top, w: b.width, h: b.height}; };
          const col = (s) => { const x = getComputedStyle(c.querySelector(s)); return [x.color, x.backgroundColor]; };
          return JSON.stringify({staff: r('.lc-staff'), guest: r('.lc-guest'), more: r('.rm-more'), cs: col('.lc-staff'), cg: col('.lc-guest'), names: [...document.querySelectorAll('.upc-title')].map((e) => e.textContent)}); })()"""))
        check("в приложении у карточки есть две кнопки копирования и «⋯»", bool(g and g["staff"] and g["guest"] and g["more"]), str(g)[:200])
        if g:
            same = all(abs(g[k]["w"] - g["staff"]["w"]) < 0.6 and abs(g[k]["h"] - g["staff"]["h"]) < 0.6 and abs(g[k]["t"] - g["staff"]["t"]) < 1 for k in ("guest", "more"))
            check("кнопки одного размера на одной линии", same, str((g["staff"], g["guest"], g["more"])))
            check("цвета кнопок различаются, заливка — не сплошная тёмная (прозрачный оттенок)", g["cs"][0] != g["cg"][0] and "rgba" in g["cs"][1] + g["cg"][1] or "color(" in g["cs"][1] + g["cg"][1], str((g["cs"], g["cg"])))
            check("отменённой встречи («Встреча с поставщиком») на главной нет", "Встреча с поставщиком" not in g["names"], str(g["names"]))
        await page.js("(() => { document.querySelector('.upc-card .lc-staff').scrollIntoView({block: 'center'}); })()")
        await asyncio.sleep(0.5)
        box = json.loads(await page.js("(() => { const b = document.querySelector('.upc-card .lc-staff').getBoundingClientRect(); return JSON.stringify({x: b.left + b.width / 2, y: b.top + b.height / 2}); })()"))
        await page.send("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"], y=box["y"])
        check("подсказка при наведении: «Копировать ссылку для сотрудников»", await page.wait("document.querySelector('.lc-tip')?.innerText === 'Копировать ссылку для сотрудников'", 5))
        await page.send("Input.dispatchMouseEvent", type="mousePressed", x=box["x"], y=box["y"], button="left", buttons=1, clickCount=1)      # настоящее нажатие: буфер обмена требует действия пользователя
        await page.send("Input.dispatchMouseEvent", type="mouseReleased", x=box["x"], y=box["y"], button="left", buttons=0, clickCount=1)
        await asyncio.sleep(1.2)
        msg = await page.js("document.body.innerText.includes('скопирована') ? 'copied' : (document.body.innerText.includes('Не удалось скопировать') ? 'refused' : 'silent')")
        focus = await page.js("document.hasFocus()")
        check("копирование в приложении: ссылка скопирована или показан отказ — но не молчание", msg == "copied", f"{msg}; окно в фокусе: {focus}")
    finally:
        try: await app.main.close()
        except Exception: pass
        try: app.proc.terminate()
        except Exception: pass
    bad = [r[0] for r in results if not r[1]]
    print(f"ИТОГО: {len(results) - len(bad)} из {len(results)}; не прошли: {bad}")

if __name__ == "__main__":
    asyncio.run(main())
