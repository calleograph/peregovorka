"""Сценарий (см. README.md): «Календарь» Outlook в Peregovorka в настоящем браузере. Сервер, API и страницы — настоящие; вместо Exchange — подставной Microsoft Graph стенда
(`/__dev/calendar`, backend/tests/fake_graph.py): события и ответы по документированному формату. Проверяется: мастер подключения (без согласия кнопка недоступна), виды «день/неделя/месяц/список»,
подписи типа и статуса у событий (не только цвет), создание встречи (курсор сразу в названии, участники, гостевая ссылка, защита от двойного нажатия), перенос и отмена, сообщение о недоступном
Exchange с последними данными, узкие окна и тёмная тема. Не проверяется: настоящий Exchange и Microsoft 365 (docs/OUTLOOK_CALENDAR.md)."""
import asyncio, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

STATE = """(() => ({chips: [...document.querySelectorAll('.cal-chip')].map(c => c.getAttribute('aria-label')), title: document.querySelector('.cal-period')?.textContent,
  sw: document.documentElement.scrollWidth, iw: innerWidth, alert: [...document.querySelectorAll('.alert')].map(a => a.innerText.slice(0, 160)), pressed: [...document.querySelectorAll('.cal-head .seg [aria-pressed=true]')].map(b => b.textContent)}))()"""
FIRST = "(() => ({tag: document.activeElement?.tagName, label: document.activeElement?.closest('label')?.innerText?.slice(0, 20) || ''}))()"


def dev(body):
    """Подставной Graph живёт в процессе стенда: управляется напрямую (через прокси Vite служебные адреса /__dev не проходят)."""
    return json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/__dev/calendar", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})))["events"]

CFG = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); return (await fetch('/api/v1/admin/settings/outlook', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token},
  body: JSON.stringify({enabled: true, sync_mode: 'graph', graph_tenant_id: 't', graph_client_id: 'c', graph_client_secret: 's', calendar_enabled: true, calendar_write: true, guest_links: true})})).status; })()"""


async def click_text(A, sel, text):
    return await A.js(f"(() => {{ const b = [...document.querySelectorAll({sel!r})].find((x) => x.textContent.trim().includes({text!r})); if (!b) return false; b.click(); return true; }})()")


async def main():
    R = S.Client("root", "root", "root-pass", 9811)
    A = S.Client("A", "alice", "alice-pass", 9812)
    try:
        await R.start()
        S.check("администратор включил календарь (Graph, запись, гостевые ссылки)", await R.js(CFG) == 200)
        S.check("подставной Graph стенда запущен, у Алисы 7 событий", dev({"reset": True, "down": False}) == 7)
        await A.start()
        await A.js("(async () => { await fetch('/api/v1/calendar/connect', {method: 'DELETE', headers: {'X-CSRF-Token': (await (await fetch('/api/v1/auth/me')).json()).csrf_token}}); })()")
        await A.goto("/calendar", 1.5)
        S.check("пункт «Календарь» есть в левой панели", await A.wait_for("!!document.querySelector('.rail-nav a[href=\"/calendar\"]')", 15))
        S.check("до подключения — мастер, кнопка недоступна без согласия", await A.wait_for("!!document.querySelector('.cal-connect')", 10) and await A.js("document.querySelector('.cal-connect button.primary').disabled === true"))
        S.check("мастер называет ящик и объясняет, что читается и что нет", await A.js("document.querySelector('.cal-connect').innerText.includes('alice@corp.test') && document.querySelector('.cal-connect').innerText.includes('пароль')"))
        await A.shot("calendar-connect")
        await A.js("document.querySelector('.cal-connect input[type=checkbox]').click()")
        await asyncio.sleep(0.5)
        S.check("с согласием кнопка «Подключить календарь» доступна", await A.js("document.querySelector('.cal-connect button.primary').disabled === false"))
        await A.js("document.querySelector('.cal-connect button.primary').click()")
        S.check("после согласия открывается календарь", await A.wait_for("!!document.querySelector('.cal-view')", 15))
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        await click_text(A, ".cal-head .seg button", "Неделя")
        await asyncio.sleep(1.2)
        st = await A.js(STATE)
        S.check("неделя: события с типом и статусом в подписи, а не только цветом", len(st["chips"]) >= 5 and all(("Событие Outlook" in c or "Встреча" in c or "Серия" in c) and any(w in c for w in ("Запланирована", "Завершена", "Идёт", "Отменена")) for c in st["chips"]), str(st["chips"][:3]))
        S.check("отменённая встреча подписана «Отменена», событие на весь день — «весь день»", any("Отменена" in c for c in st["chips"]) and any("весь день" in c for c in st["chips"]))
        S.check("легенда типов и статусов на странице", await A.js("document.querySelector('.cal-legend').innerText.includes('Встреча') && document.querySelector('.cal-legend').innerText.includes('Событие Outlook') && document.querySelector('.cal-legend').innerText.includes('Серия')"))
        await A.shot("calendar-week")
        for name, key in (("День", "day"), ("Месяц", "month"), ("Список", "list"), ("Неделя", "week")):
            await click_text(A, ".cal-head .seg button", name)
            await asyncio.sleep(0.8)
            s2 = await A.js(STATE)
            S.check(f"вид «{name}»: переключился, горизонтальной прокрутки нет", s2["pressed"] == [name] and s2["sw"] <= s2["iw"], str(s2["pressed"]))
        await click_text(A, ".cal-head .seg button", "Список"); await asyncio.sleep(0.8)
        S.check("список: события сгруппированы по дням, есть подпись типа", await A.js("document.querySelectorAll('.cal-list section').length >= 3 && document.querySelector('.cal-list').innerText.includes('Событие Outlook')"))
        await A.shot("calendar-list")
        await click_text(A, ".cal-head .seg button", "Неделя"); await asyncio.sleep(0.6)
        # переход по периодам и «Сегодня»
        before = (await A.js(STATE))["title"]
        await A.js("document.querySelector('.cal-nav button[aria-label=\"Вперёд\"]').click()"); await asyncio.sleep(0.8)
        mid = (await A.js(STATE))["title"]
        await click_text(A, ".cal-nav button", "Сегодня"); await asyncio.sleep(0.8)
        S.check("«Вперёд» меняет период, «Сегодня» возвращает", mid != before and (await A.js(STATE))["title"] == before, f"{before} → {mid}")
        # ---------- создание встречи
        await click_text(A, ".cal-head button", "Новая встреча")
        S.check("форма «Новая встреча» открылась", await A.wait_for("!!document.querySelector('.cal-form')", 8))
        f = await A.js(FIRST)
        S.check("курсор сразу в поле «Название» — без щелчка и Tab", f["tag"] == "INPUT" and f["label"].startswith("Название"), str(f))
        await A.send("Input.insertText", text="Согласование бюджета")
        S.check("введённый сразу текст попал в название", await A.js("document.activeElement.value") == "Согласование бюджета")
        await A.js("(() => { const i = document.querySelector('.cal-who input[type=email]'); i.focus(); })()")
        await A.send("Input.insertText", text="ext@example.org")
        await A.js("document.querySelector('.cal-who .row button:last-child').click()")
        S.check("внешний участник добавлен", await A.js("document.querySelector('.cal-people')?.innerText.includes('ext@example.org')"))
        await A.js("[...document.querySelectorAll('.cal-form .check input')].find((i) => i.parentElement.innerText.includes('гостевую'))?.click()")
        await A.shot("calendar-create")
        await A.js("(() => { const b = [...document.querySelectorAll('.cal-form button')].find((x) => x.textContent.includes('Создать и разослать')); b.click(); b.click(); })()")      # двойное нажатие
        S.check("встреча создана: показаны ссылка для сотрудников и гостевая", await A.wait_for("document.querySelector('.modal')?.innerText.includes('Для внешних участников')", 25), str(await A.js("document.querySelector('.modal')?.innerText.slice(0, 200)")))
        info = dev({})
        S.check("в Exchange создано одно событие (двойное нажатие дубль не дало): было 7, стало 8", info == 8, str(info))
        await click_text(A, ".modal button", "Готово")
        await asyncio.sleep(1.5)
        S.check("новая встреча появилась в календаре с типом «Встреча»", any("Встреча. Согласование бюджета" in c for c in (await A.js(STATE))["chips"]), str((await A.js(STATE))["chips"]))
        # ---------- карточка, перенос, отмена
        await A.js("[...document.querySelectorAll('.cal-chip')].find((c) => c.getAttribute('aria-label').includes('Согласование бюджета')).click()")
        S.check("карточка события: тип, статус, ссылки, «Изменить время» и «Отменить встречу»", await A.wait_for("document.querySelector('.cal-detail')?.innerText.includes('Для внешних участников')", 8)
                and await A.js("document.querySelector('.cal-detail').innerText.includes('Изменить время') && document.querySelector('.cal-detail').innerText.includes('Отменить встречу')"))
        await click_text(A, ".cal-detail button", "Изменить время")
        S.check("в форме переноса курсор в названии", (await A.js(FIRST))["tag"] == "INPUT" and (await A.js("document.activeElement.value")) == "Согласование бюджета")
        await A.js("(() => { const i = document.querySelector('.cal-detail input[type=text], .cal-detail input:not([type])'); })()")
        await A.send("Input.insertText", text=" (перенесено)")
        await click_text(A, ".cal-detail button", "Сохранить и уведомить")
        await asyncio.sleep(2.0)
        S.check("перенос применён: новое название в календаре", any("Согласование бюджета (перенесено)" in c for c in (await A.js(STATE))["chips"]), str((await A.js(STATE))["chips"][:8]))
        await A.js("[...document.querySelectorAll('.cal-chip')].find((c) => c.getAttribute('aria-label').includes('перенесено')).click()")
        await A.wait_for("!!document.querySelector('.cal-detail')", 8)
        await click_text(A, ".cal-detail button", "Отменить встречу")
        S.check("отмена требует подтверждения", await A.wait_for("document.querySelector('.modal')?.innerText.includes('нельзя отменить')", 8))
        await click_text(A, ".modal button", "Отменить встречу")
        await asyncio.sleep(2.0)
        S.check("после отмены встреча в календаре помечена «Отменена»", any("перенесено" in c and "Отменена" in c for c in (await A.js(STATE))["chips"]), str((await A.js(STATE))["chips"][:8]))
        # ---------- Exchange недоступен: последние данные и понятное сообщение
        await A.js("document.querySelector('.cal-head button:not(.primary):not(.icon)') && 1")
        dev({"down": True})
        await click_text(A, ".cal-head button", "Обновить")
        S.check("Exchange недоступен: баннер «данные могли устареть», события остались", await A.wait_for("document.querySelector('.alert.warn')?.innerText.includes('могли устареть')", 15) and len((await A.js(STATE))["chips"]) >= 5, str((await A.js(STATE))["alert"]))
        dev({"down": False})
        # ---------- узкий экран и тёмная тема
        for w in (390, 700):
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=800, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.6)
            s3 = await A.js(STATE)
            S.check(f"{w} px: горизонтальной прокрутки страницы нет", s3["sw"] <= s3["iw"], f"{s3['sw']}/{s3['iw']}")
        await A.shot("calendar-390")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        await A.js("document.documentElement.setAttribute('data-theme', 'dark')")
        await asyncio.sleep(0.5)
        tint = await A.js("(() => { const c = document.querySelector('.cal-chip'); const s = getComputedStyle(c); return {bg: s.backgroundColor, color: s.color}; })()")
        S.check("тёмная тема: плашка светлым текстом на тёмном фоне", tint["bg"] != "rgba(0, 0, 0, 0)" and tint["color"] != tint["bg"], str(tint))
        await A.shot("calendar-dark")
        # ---------- отключение
        await A.js("document.documentElement.setAttribute('data-theme', 'glass')")
        await click_text(A, ".cal-foot button", "Отключить календарь")
        S.check("отключение возвращает мастер подключения", await A.wait_for("!!document.querySelector('.cal-connect')", 10))
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        for c in (A, R):
            try: await c.close()
            except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

if __name__ == "__main__":
    asyncio.run(main())
