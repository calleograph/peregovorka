"""Сценарий (см. README.md): защита входа в приложении Windows (Electron из desktop/, под Xvfb на Linux). После порога неудач CAPTCHA показывается в окне приложения так же, как в браузере;
неверные символы дают ошибку, верные пускают; очистка хранилища окна порог не снимает. Стенд — `restart_dev_sec.sh` (LIVE_FIXED_CAPTCHA=1, ответ AAAAA).
Не проверяется: настоящий Windows и системное масштабирование; настоящий reverse proxy/WAF."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, check, results  # noqa: E402
from desktop_app import A_URL, STUB, probe_in_shell, reset_sessions  # noqa: E402
import urllib.request, http.cookiejar  # noqa: E402

FILL = """(sel, v) => { const el = document.querySelector(sel); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true })); }"""


def admin_put(group, body):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    hdr = {"Content-Type": "application/json", "Origin": A_URL}
    ch = json.load(op.open(urllib.request.Request(A_URL + "/api/v1/captcha/challenge", headers=hdr)))        # на стенде CAPTCHA может быть уже включена: ответ известен (AAAAA)
    cap = {"X-Captcha-Id": ch["id"], "X-Captcha-Answer": "AAAAA"} if ch.get("kind") == "image" else {}
    csrf = json.load(op.open(urllib.request.Request(A_URL + "/api/v1/auth/login", data=json.dumps({"login": "root", "password": "root-pass"}).encode(), headers={**hdr, **cap})))["csrf_token"]
    op.open(urllib.request.Request(f"{A_URL}/api/v1/admin/settings/{group}", method="PUT", data=json.dumps(body).encode(), headers={**hdr, "X-CSRF-Token": csrf}))
    op.open(urllib.request.Request(A_URL + "/api/v1/auth/logout", method="POST", headers={**hdr, "X-CSRF-Token": csrf}))


async def attempt(page, user, pw):
    await page.js(f"""(() => {{ const f = {FILL}; f('input[autocomplete=username]', {json.dumps(user)}); f('input[type=password]', {json.dumps(pw)}); document.querySelector('form').requestSubmit(); }})()""")
    await asyncio.sleep(1.5)


async def main():
    reset_sessions()
    admin_put("captcha", {"mode": "after_failures", "kind": "image", "failures": 3, "window_minutes": 15})
    admin_put("login_protection", {"slowdown_after": 0, "ip_block_failures": 100, "spray_accounts": 50, "account_guard_failures": 500, "rate_ip_per_minute": 500})
    app = App(scale=1.0, port=9360, inspect=9361)
    try:
        await app.start()
        await app.m(STUB)
        shell = await app.page("app://shell/index.html")
        await shell.wait("[...document.querySelectorAll('button')].some((b) => b.textContent.includes('Добавить организацию'))", 30)
        await shell.js("(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')); b.click(); })()")
        await shell.wait("!!document.querySelector('#srv')", 10)
        await probe_in_shell(shell, A_URL)
        page = await app.page(A_URL)
        await page.wait("!!document.querySelector('input[autocomplete=username]')", 30)
        for i in range(3):
            await attempt(page, "alice", "неверно")
            check(f"приложение: неверный пароль №{i + 1} — окна проверки нет", not await page.js("!!document.querySelector('.captcha-form')"))
        await attempt(page, "alice", "alice-pass")
        check("приложение: после трёх неудач показано окно «Подтвердите, что вы не робот»", await page.wait("!!document.querySelector('.captcha-form img.captcha-img')", 15))
        check("приложение: картинка загружена, курсор в поле", await page.js("(() => { const i = document.querySelector('.captcha-img'); return i.complete && i.naturalWidth > 100 && document.activeElement.tagName === 'INPUT'; })()"))
        await app.shot_window("login-captcha-desktop")
        await page.js(f"({FILL})('.captcha-form input', 'ZZZZZ')")
        await page.js("document.querySelector('.captcha-form button[type=submit]').click()")
        await asyncio.sleep(1.5)
        check("приложение: неверные символы — ошибка, окно осталось", await page.js("!!document.querySelector('.captcha-form .alert')"))
        await page.js("localStorage.clear(); sessionStorage.clear()")
        await page.js(f"({FILL})('.captcha-form input', 'aaaaa')")
        await page.js("document.querySelector('.captcha-form button[type=submit]').click()")
        check("приложение: верные символы — вход выполнен", await page.wait("!!document.querySelector('.topbar .usermenu')", 20))
    finally:
        try: await app.main.close()
        except Exception: pass
        try: app.proc.terminate()
        except Exception: pass
        await asyncio.sleep(1)
        admin_put("captcha", {"failures": 5})
    bad = [r[0] for r in results if not r[1]]
    print(f"ИТОГО: {len(results) - len(bad)} из {len(results)}; не прошли: {bad}")

if __name__ == "__main__":
    asyncio.run(main())
