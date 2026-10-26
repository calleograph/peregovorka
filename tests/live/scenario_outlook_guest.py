"""Сценарий (см. README.md): две ссылки в приглашении Outlook и управление гостевой ссылкой в боковой панели — в настоящем браузере. Сервер, API и страницы — настоящие;
вместо Outlook — подставной Office.js из scenario_outlook_panel. Проверяется: приглашение с обеими ссылками, одна гостевая ссылка на событие при повторном нажатии, перевыпуск (прежняя
перестаёт работать, текст приглашения обновлён), отзыв и повторный выпуск, запрет администратора скрывает блок и закрывает ссылку, ссылка не попадает в журнал.
Не проверяется: настоящий Outlook и Exchange."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from scenario_outlook_panel import ENABLE, FAKE, PANEL, STATE

GUEST = """(() => ({shown: !document.getElementById('guest').hidden, link: document.getElementById('glink').href, linkbox: !document.getElementById('guest-linkbox').hidden,
  issue: !document.getElementById('g-issue').hidden, rotate: document.getElementById('g-rotate').textContent, revoke: document.getElementById('g-revoke').textContent,
  body: window.__m.get('body', ''), sw: document.documentElement.scrollWidth, iw: innerWidth}))()"""


async def main():
    R = S.Client("root", "root", "root-pass", 9791)
    A = S.Client("A", "alice", "alice-pass", 9792)
    try:
        await R.start()
        S.check("интеграция включена, гости разрешены", await R.js(f"{ENABLE}({{guest_links: true}})") == 200)
        await A.start()
        await A.js("(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); await fetch('/api/v1/auth/logout', {method: 'POST', headers: {'X-CSRF-Token': me.csrf_token}}); })()")
        await A.send("Network.setBlockedURLs", urls=["*appsforoffice.microsoft.com*", "*cdn.office.net*"])
        await A.send("Page.addScriptToEvaluateOnNewDocument", source=FAKE)
        await A.send("Emulation.setDeviceMetricsOverride", width=340, height=700, deviceScaleFactor=1, mobile=False)

        async def open_panel(wait="document.body.dataset.state === 'done' || document.body.dataset.state === 'error'"):
            await A.goto(PANEL, 1.0)
            await A.wait_for(wait, 20)
            await asyncio.sleep(0.5)

        async def type_(sel, text):
            await A.js(f"(() => {{ const i = document.querySelector({sel!r}); i.focus(); i.value = {text!r}; i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")

        async def press(sel):
            await A.js(f"document.querySelector({sel!r}).click()")
            await asyncio.sleep(1.0)

        async def code(path):
            return await A.js(f"fetch({path!r}).then(r => r.status)")

        await A.goto(PANEL, 0.3); await A.js("localStorage.clear(); sessionStorage.clear()")
        await open_panel("document.body.dataset.state !== 'loading'")
        await type_("#lg-user", "alice"); await type_("#lg-pass", "alice-pass"); await A.js("document.getElementById('lg-go').click()")
        S.check("конференция создана", await A.wait_for("document.body.dataset.state === 'done'", 20))
        st, g = await A.js(STATE), await A.js(GUEST)
        gl = g["link"]
        S.check("в панели: блок «Для внешних участников» с гостевой ссылкой /guest/…", g["shown"] and g["linkbox"] and "/guest/" in gl and not g["issue"], str(g)[:300])
        S.check("в тексте приглашения обе ссылки: для сотрудников и гостевая, подписи и пояснение",
                st["link"] in g["body"] and gl in g["body"] and "Для сотрудников организации" in g["body"] and "Для внешних участников" in g["body"] and "передайте ему гостевую ссылку" in g["body"] and g["body"].count("<!--pg-conf-->") == 1, g["body"][:500])
        S.check("320–340 px: без горизонтальной прокрутки", g["sw"] <= g["iw"], f"{g['sw']}/{g['iw']}")
        await A.shot("outlook-panel-guest-340")
        old = gl.rsplit("/", 1)[1]
        S.check("гостевая ссылка рабочая (страница гостя видит комнату)", await code(f"/api/v1/guest/room/{old}") == 200)
        # повторное открытие и «Обновить конференцию» не меняют ссылку и не добавляют вторую
        await open_panel()
        g2 = await A.js(GUEST)
        await press("#go")
        await A.wait_for("document.body.dataset.state === 'done'", 15)
        g3 = await A.js(GUEST)
        S.check("повторное открытие и обновление: та же гостевая ссылка, один блок в тексте", g2["link"] == gl == g3["link"] and g3["body"].count("<!--pg-conf-->") == 1 and g3["body"].count("/guest/") == 2, str(g3["body"].count("/guest/")))
        # перевыпуск: нужно подтверждение вторым нажатием
        await press("#g-rotate")
        g = await A.js(GUEST)
        S.check("перевыпуск требует подтверждения вторым нажатием", g["link"] == gl and "Подтвердить" in g["rotate"], g["rotate"])
        await press("#g-rotate")
        await A.wait_for("document.body.dataset.state === 'done'", 15)
        g = await A.js(GUEST)
        new = g["link"].rsplit("/", 1)[1]
        S.check("перевыпуск: новая ссылка в панели и в тексте приглашения, прежней в тексте нет", new != old and g["link"] in g["body"] and old not in g["body"], g["body"][:300])
        S.check("прежняя гостевая ссылка перестала работать, новая работает", await code(f"/api/v1/guest/room/{old}") == 404 and await code(f"/api/v1/guest/room/{new}") == 200)
        # отзыв
        await press("#g-revoke"); await press("#g-revoke")
        await A.wait_for("document.body.dataset.state === 'done'", 15)
        g = await A.js(GUEST)
        S.check("отзыв: ссылки в панели и в тексте нет, предложено выпустить заново", g["shown"] and not g["linkbox"] and g["issue"] and "/guest/" not in g["body"] and "Для внешних участников" not in g["body"], g["body"][:300])
        S.check("отозванная ссылка не работает", await code(f"/api/v1/guest/room/{new}") == 404)
        await press("#g-issue")
        await A.wait_for("document.body.dataset.state === 'done'", 15)
        g = await A.js(GUEST)
        S.check("повторный выпуск после отзыва: новая рабочая ссылка в тексте", g["linkbox"] and g["link"] in g["body"] and g["link"].rsplit("/", 1)[1] not in (old, new), g["link"][-6:])
        issued = g["link"].rsplit("/", 1)[1]
        # запрет администратора
        S.check("администратор запретил гостей во встречах Outlook", await R.js(f"{ENABLE}({{guest_links: false}})") == 200)
        S.check("выпущенная ссылка перестала работать", await code(f"/api/v1/guest/room/{issued}") == 404)
        await open_panel()
        S.check("блок гостевой ссылки в панели скрыт", not (await A.js(GUEST))["shown"])
        log = await R.js("(async () => JSON.stringify([await (await fetch('/api/v1/admin/journal?limit=300')).json(), await (await fetch('/api/v1/admin/audit?limit=300')).json()]))()")
        S.check("в журнале и аудите нет гостевых ссылок", all(t not in log for t in (old, new, issued)), log[:200])
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        for c in (A, R):
            try: await c.close()
            except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

if __name__ == "__main__":
    asyncio.run(main())
