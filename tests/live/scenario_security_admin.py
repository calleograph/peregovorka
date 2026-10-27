"""Сценарий (см. README.md): разделы «Безопасность» в настоящем браузере — события и блокировки, заблокированные адреса, доверенные подсети (включение с пробным сроком и подтверждением,
защита от самоизоляции администратора), лёгкий WAF (наблюдение → защита), сеть и IP-адреса. Адрес клиента на стенде подставляется заголовком X-PG-Peer (запрос идёт через прокси Vite
с частного адреса — так web-контейнер сообщает backend адрес подключившегося). Стенд — `restart_dev_sec.sh`. Не проверяется: настоящий reverse proxy/WAF и сеть."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

FILL = """((sel, val) => { const i = document.querySelector(sel); const proto = i.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, 'value').set.call(i, val); i.dispatchEvent(new Event('input', {bubbles: true})); })"""


async def api(A, method, path, body=None, headers=None, anon=False):
    h = {"content-type": "application/json", **(headers or {})}
    return await A.js(f"""(async () => {{ const me = {'null' if anon else "await (await fetch('/api/v1/auth/me')).json().catch(() => null)"};
      const hh = {json.dumps(h)}; if (me && me.csrf_token) hh['X-CSRF-Token'] = me.csrf_token;
      const r = await fetch('/api/v1{path}', {{method: {method!r}, headers: hh, credentials: {'"omit"' if anon else '"same-origin"'}, body: {('JSON.stringify(' + json.dumps(body) + ')') if body is not None else 'undefined'}}});
      let d = null; try {{ d = await r.json(); }} catch (e) {{}} return {{status: r.status, data: d}}; }})()""")


async def click_text(A, text, sel="button, a"):
    return await A.js(f"(() => {{ const b = [...document.querySelectorAll({sel!r})].find((x) => x.textContent.trim().includes({text!r})); if (b) b.click(); return !!b; }})()")


async def main():
    A = S.Client("A", "root", "root-pass", 9821)
    try:
        await A.start()
        for w, h, tag in ((1366, 900, "1366"), (390, 844, "390")):
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=(w < 500))
            for tab, key, title in (("security_events", ".sec-tiles", "События и блокировки"), ("security_blocks", ".sec-tiles", "Заблокированные адреса"), ("ip_access", ".card h2", "Доверенные подсети"),
                                    ("waf", ".sec-modes", "WAF"), ("login_protection", ".card h2, .card h3", "Защита входа"), ("net_trust", ".card h2", "Сеть и IP-адреса")):
                await A.goto(f"/admin?tab={tab}", 1.6)
                S.check(f"{tag}: раздел «{title}» открывается", await A.wait_for(f"!!document.querySelector({key!r})", 15))
                S.check(f"{tag}: «{title}» без горизонтальной прокрутки", await A.js("document.documentElement.scrollWidth <= innerWidth + 1"))
                if tag == "1366" or tab in ("security_events", "ip_access"):
                    await A.shot(f"sec-{tab}-{tag}")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)

        # ---- сеть и IP: диагностика
        await A.goto("/admin?tab=net_trust", 1.5)
        S.check("«Что сервер видит сейчас» показывает адрес и источник", await A.wait_for("document.body.innerText.includes('Ваш адрес для сервера')", 10))

        # ---- доверенные подсети: защита от самоизоляции
        await A.goto("/admin?tab=ip_access", 1.5)
        S.check("список пуст: включить нельзя", (await api(A, "PUT", "/admin/security/ip-access/settings", {"enabled": True}))["status"] == 422)
        add = await api(A, "POST", "/admin/security/ip-access/entries", {"cidr": "203.0.113.0/24", "comment": "Офис (проверка)"})
        S.check("запись добавлена", add["status"] == 201)
        await A.goto("/admin?tab=ip_access", 1.5)
        await A.js("document.querySelector('.card label.check input[type=checkbox]').click()")
        S.check("адрес администратора не в списке: окно предупреждения о потере доступа", await A.wait_for("!!document.querySelector('.modal') && document.querySelector('.modal').textContent.includes('потеряете доступ')", 10))
        await A.shot("sec-ip-access-risk")
        await click_text(A, "Отмена", ".modal button")
        S.check("после отмены ограничение осталось выключенным", (await api(A, "GET", "/admin/security/ip-access"))["data"]["enabled"] is False)
        await api(A, "POST", "/admin/security/ip-access/entries", {"cidr": "127.0.0.1", "comment": "Адрес администратора"})
        await A.goto("/admin?tab=ip_access", 1.5)
        await A.js("document.querySelector('.card label.check input[type=checkbox]').click()")
        S.check("включено: пробный срок и кнопка «Подтвердить»", await A.wait_for("document.body.innerText.includes('Подтвердите настройку')", 10), (await A.js("document.querySelector('.card').innerText.slice(0, 500)")) or "")
        await A.shot("sec-ip-access-pending")
        far = await api(A, "GET", "/captcha/challenge?scope=login", None, {"X-Dev-Peer": "198.51.100.200"})
        S.check("чужой адрес получает нейтральный 403 без подробностей", far["status"] == 403 and far["data"] and far["data"]["detail"]["code"] == "access_restricted" and far["status"] == 403 and far["data"]["detail"]["message"].startswith("Доступ к сервису ограничен") and "203.0.113" not in json.dumps(far["data"]), json.dumps(far)[:200])
        near = await api(A, "GET", "/captcha/challenge?scope=login", None, {"X-Dev-Peer": "203.0.113.20"})
        S.check("адрес из разрешённой подсети проходит", near["status"] == 200)
        await click_text(A, "Подтвердить", ".card button")
        S.check("настройка подтверждена, плашка ожидания исчезла", await A.wait_for("!document.body.innerText.includes('Подтвердите настройку')", 10))
        S.check("интерфейс администратора работает при включённом ограничении", await A.wait_for("document.body.innerText.includes('ограничение действует')", 10))
        await A.shot("sec-ip-access-active")
        # отказ в приложении: страница-заглушка
        await A.send("Emulation.setUserAgentOverride", userAgent="Mozilla/5.0 Chrome/130")
        await api(A, "PUT", "/admin/security/ip-access/settings", {"enabled": False})
        S.check("ограничение выключено из интерфейса", (await api(A, "GET", "/admin/security/ip-access"))["data"]["active"] is False)

        # ---- WAF
        await A.goto("/admin?tab=waf", 1.5)
        await A.js("[...document.querySelectorAll('.sec-mode input')][2].click()")
        S.check("режим «Защита» сохранён", await A.wait_for("document.querySelectorAll('.sec-mode.on').length === 1 && document.querySelector('.sec-mode.on').textContent.includes('Защита')", 10))
        await asyncio.sleep(4)                                              # снимок политики в процессе обновляется раз в 3 секунды
        trav = await api(A, "GET", "/files/..%2f..%2fetc%2fpasswd", None, None, anon=True)
        S.check("анонимный запрос со «..» блокируется правилом WAF", trav["status"] == 403 and trav["data"]["detail"]["code"] == "request_blocked", json.dumps(trav)[:160])
        ok = await api(A, "GET", "/rooms?q=" + "Иван+Петров", None)
        S.check("обычный запрос вошедшего пользователя проходит", ok["status"] == 200)
        await A.goto("/admin?tab=security_events", 2.0)
        S.check("срабатывание WAF видно в «События и блокировки»", await A.wait_for("document.body.innerText.includes('Срабатывание правила WAF')", 10))
        await A.shot("sec-events-1366")
        await api(A, "PUT", "/admin/security/waf", {"mode": "observe"})

        # ---- ручная блокировка и снятие
        await A.goto("/admin?tab=security_blocks", 1.5)
        await A.js(f"({FILL})('form input[placeholder=\"203.0.113.50\"]', '203.0.113.5')")
        await A.js(f"({FILL})('form input[placeholder^=\"Подбор\"]', 'Проверка из сценария')")
        await A.js("[...document.querySelectorAll('form button')].find((b) => b.textContent.includes('Заблокировать')).click()")
        S.check("адрес заблокирован вручную и виден в таблице", await A.wait_for("document.body.innerText.includes('203.0.113.5')", 10))
        await A.shot("sec-blocks-1366")
        await click_text(A, "Снять блокировку", "table button")
        S.check("окно подтверждения снятия блокировки", await A.wait_for("!!document.querySelector('.modal') && document.querySelector('.modal').textContent.includes('история повторных блокировок')", 10))
        await click_text(A, "Снять блокировку", ".modal button")
        S.check("блокировка снята, таблица пуста", await A.wait_for("document.body.innerText.includes('заблокированных адресов нет')", 10))
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        await A.close()
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

asyncio.run(main())
