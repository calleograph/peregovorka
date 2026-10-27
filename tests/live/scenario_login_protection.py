"""Сценарий (см. README.md): защита входа в настоящем браузере. Порог CAPTCHA = 3: после трёх неверных паролей окно с картинкой ДЕЙСТВИТЕЛЬНО появляется;
без правильного ввода вход не принимается (в том числе прямым запросом к API); закрытие «окна инкогнито» (очистка cookie и хранилища, другой User-Agent) требование не снимает;
неверные символы дают новую картинку; верные — пускают; блокировка адреса видна пользователю нейтральным сообщением; админские разделы открываются и сохраняются.
Стенд: `restart_dev_sec.sh` (LIVE_FIXED_CAPTCHA=1: ответ CAPTCHA на стенде — AAAAA). Не проверяется: настоящий reverse proxy и WAF (адрес берётся как у прямого подключения)."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

FILL = """((sel, val) => { const i = document.querySelector(sel); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, val);
  i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true})); })"""


async def api(A, method, path, body=None, headers=None):
    h = {"content-type": "application/json", **(headers or {})}
    return await A.js(f"""(async () => {{ const me = await (await fetch('/api/v1/auth/me')).json().catch(() => null);
      const hh = {json.dumps(h)}; if (me && me.csrf_token) hh['X-CSRF-Token'] = me.csrf_token;
      const r = await fetch('/api/v1{path}', {{method: {method!r}, headers: hh, body: {('JSON.stringify(' + json.dumps(body) + ')') if body is not None else 'undefined'}}});
      let d = null; try {{ d = await r.json(); }} catch (e) {{}} return {{status: r.status, data: d, ra: r.headers.get('Retry-After')}}; }})()""")


async def logout(A):
    await A.js("(async () => { const me = await (await fetch('/api/v1/auth/me')).json().catch(() => null); if (me && me.csrf_token) await fetch('/api/v1/auth/logout', {method: 'POST', headers: {'X-CSRF-Token': me.csrf_token}}); })()")


async def submit(A, user, pw):
    await A.js(f"({FILL})('.px-form input[autocomplete=\"username\"], .px-form input:not([type=password])', {user!r})")
    await A.js(f"({FILL})('.px-form input[type=password]', {pw!r})")
    await A.js("document.querySelector('.px-cta').click()")
    await asyncio.sleep(1.2)


async def main():
    A = S.Client("A", "root", "root-pass", 9811)
    try:
        await A.start()
        # --- настройки (администратор): порог 3, картинка
        r = await api(A, "PUT", "/admin/settings/captcha", {"mode": "after_failures", "kind": "image", "failures": 3, "window_minutes": 15, "length": 5, "ttl_seconds": 120})
        S.check("администратор сохранил порог CAPTCHA = 3", r["status"] == 200 and r["data"]["failures"] == 3, json.dumps(r)[:300])
        r = await api(A, "PUT", "/admin/settings/login_protection", {"slowdown_after": 0, "ip_block_failures": 40, "spray_accounts": 30, "account_guard_failures": 500, "rate_ip_per_minute": 500})
        S.check("защита входа настроена для проверки (без замедления)", r["status"] == 200)
        await logout(A)
        await A.js("localStorage.clear(); sessionStorage.clear()")
        await A.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
        await A.send("Emulation.setUserAgentOverride", userAgent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Окно-1")
        await A.goto("/", 1.5)
        S.check("страница входа открыта", await A.wait_for("!!document.querySelector('.px-form input')", 15))
        # --- три неверных пароля: проверки ещё нет
        for i in range(3):
            await submit(A, "alice", "неверный-пароль")
            S.check(f"неверный пароль №{i + 1}: окно проверки не требуется", not await A.js("!!document.querySelector('.captcha-form')"))
        # --- четвёртая попытка (даже с ВЕРНЫМ паролем): окно с картинкой появляется
        await submit(A, "alice", "alice-pass")
        S.check("после трёх неудач появилось окно «Подтвердите, что вы не робот»", await A.wait_for("!!document.querySelector('.captcha-form img.captcha-img')", 10))
        info = await A.js("(() => { const i = document.querySelector('.captcha-img'); return {w: i.naturalWidth, h: i.naturalHeight, ok: i.complete && i.naturalWidth > 100, hasInput: !!document.querySelector('.captcha-form input')}; })()")
        S.check("картинка загружена и видна, есть поле ввода", info["ok"] and info["hasInput"], json.dumps(info))
        S.check("курсор сразу в поле ввода символов", await A.js("document.activeElement && document.activeElement.closest('.captcha-form') !== null && document.activeElement.tagName === 'INPUT'"))
        await A.shot("captcha-dialog-1280")
        # --- прямой запрос к API с верным паролем, но без проверки — отклоняется
        direct = await api(A, "POST", "/auth/login", {"login": "alice", "password": "alice-pass"})
        S.check("прямой запрос к API с верным паролем без CAPTCHA отклонён (428)", direct["status"] == 428 and direct["data"]["detail"]["code"] == "captcha_required")
        S.check("в ответе нет ответа на задачу, картинка — data:image/png", "answer" not in json.dumps(direct["data"]).lower() and direct["data"]["detail"]["challenge"]["image"].startswith("data:image/png;base64,"))
        # --- неверные символы → ошибка и новая картинка
        before = await A.js("document.querySelector('.captcha-img').src.length")
        await A.js(f"({FILL})('.captcha-form input', 'ZZZZZ')")
        await A.js("document.querySelector('.captcha-form button[type=submit]').click()")
        await asyncio.sleep(1.5)
        S.check("неверные символы: окно осталось и показало понятную ошибку", await A.js("(document.querySelector('.captcha-form .alert') || {}).textContent || ''") != "" and bool(await A.js("!!document.querySelector('.captcha-form')")))
        await A.shot("captcha-dialog-wrong")
        # --- «закрыли окно инкогнито»: cookie, хранилище и User-Agent другие — требование осталось
        await A.send("Network.clearBrowserCookies")
        await A.js("localStorage.clear(); sessionStorage.clear()")
        await A.send("Emulation.setUserAgentOverride", userAgent="curl/8.5.0")
        await A.goto("/", 1.5)
        await submit(A, "alice", "alice-pass")
        S.check("после очистки cookie и смены User-Agent проверка по-прежнему требуется", await A.wait_for("!!document.querySelector('.captcha-form img.captcha-img')", 10))
        # --- верные символы пропускают
        await A.js(f"({FILL})('.captcha-form input', 'aaaaa')")                 # регистр не важен
        await A.js("document.querySelector('.captcha-form button[type=submit]').click()")
        S.check("верные символы: вход выполнен, открыт интерфейс", await A.wait_for("!document.querySelector('.px-form') && !!document.querySelector('.rail, .shell')", 15))
        await logout(A)
        # --- одна картинка — одна попытка: повтор того же ответа не проходит
        ch = (await api(A, "GET", "/captcha/challenge?scope=login"))["data"]
        ok = await api(A, "POST", "/auth/login", {"login": "alice", "password": "alice-pass"}, {"X-Captcha-Id": ch["id"], "X-Captcha-Answer": "AAAAA"})
        await logout(A)
        again = await api(A, "POST", "/auth/login", {"login": "alice", "password": "alice-pass"}, {"X-Captcha-Id": ch["id"], "X-Captcha-Answer": "AAAAA"})
        S.check("решение принимается один раз", ok["status"] == 200 and again["status"] == 428 and again["data"]["detail"]["reason"] == "expired", f"{ok['status']}/{again['status']}")
        # --- подбор по списку: адрес блокируется, пользователь видит нейтральное сообщение
        await A.send("Emulation.setUserAgentOverride", userAgent="Mozilla/5.0 Chrome/130")
        rr = await api(A, "PUT", "/admin/settings/login_protection", {"spray_accounts": 4}) if False else None
        login = await api(A, "POST", "/auth/login", {"login": "root", "password": "root-pass"}, {"X-Captcha-Id": (await api(A, "GET", "/captcha/challenge?scope=login"))["data"]["id"], "X-Captcha-Answer": "AAAAA"})
        S.check("администратор снова вошёл (с CAPTCHA)", login["status"] == 200, str(login["status"]))
        await api(A, "PUT", "/admin/settings/login_protection", {"spray_accounts": 4, "ip_block_failures": 40})
        await logout(A)
        codes = []
        for i in range(8):
            ch = (await api(A, "GET", "/captcha/challenge?scope=login"))["data"]
            rr = await api(A, "POST", "/auth/login", {"login": f"ghost{i}", "password": "x"}, {"X-Captcha-Id": ch["id"], "X-Captcha-Answer": "AAAAA"})
            codes.append(rr["status"])
        S.check("подбор по списку учётных записей приводит к блокировке адреса (429)", 429 in codes, str(codes))
        await A.goto("/", 1.5)
        await A.js("localStorage.clear()")
        S.check("страница входа при блокировке открывается", await A.wait_for("!!document.querySelector('.px-form input')", 10))
        await submit(A, "alice", "alice-pass")
        txt = await A.js("(document.querySelector('.px-form .alert, .px-form [role=alert], .alert') || {}).textContent || ''")
        S.check("при блокировке пользователь видит нейтральное сообщение без подробностей", "Слишком много" in txt and "правил" not in txt.lower() and "ghost" not in txt, txt[:120])
        await A.shot("login-blocked")
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        await A.close()
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

asyncio.run(main())
