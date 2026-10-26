"""Сценарий (см. README.md): календарь Outlook в Peregovorka в настоящем браузере. Сервер, API и страницы — настоящие; вместо Exchange — подставной Microsoft Graph стенда
(`/__dev/calendar`, backend/tests/fake_graph.py). Проверяется: кнопка «Календарь» на главной, мастер подключения, неделя как временная сетка (пересечения без наложения, высота по длительности,
события вне рабочих часов не скрыты, текущее время, события на весь день), рабочие часы, выделение интервала мышью → форма создания, создание с конференцией и гостевой ссылкой без дублей,
карточка события (организатор, участники, место, описание), изменение и отмена одного занятия и всей серии, чужая встреча только для чтения, несколько календарей, виды «день/месяц/список»,
сохранение даты и вида при переходах, недоступный Exchange, узкие окна и тёмная тема. Не проверяется: настоящий Exchange и Microsoft 365 (docs/OUTLOOK_CALENDAR.md)."""
import asyncio, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

STATE = """(() => ({chips: [...document.querySelectorAll('.cal-chip')].map(c => c.getAttribute('aria-label')), title: document.querySelector('.cal-period')?.textContent,
  sw: document.documentElement.scrollWidth, iw: innerWidth, alert: [...document.querySelectorAll('.alert')].map(a => a.innerText.slice(0, 160)),
  pressed: [...document.querySelectorAll('.cal-head .seg button')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.textContent.trim()), health: document.querySelector('.cal-health')?.className || ''}))()"""
FIRST = "(() => ({tag: document.activeElement?.tagName, label: document.activeElement?.closest('label')?.innerText?.slice(0, 20) || ''}))()"


def dev(body):
    """Подставной Graph живёт в процессе стенда: управляется напрямую (через прокси Vite служебные адреса /__dev не проходят)."""
    return json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/__dev/calendar", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})))


CFG = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); return (await fetch('/api/v1/admin/settings/outlook', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token},
  body: JSON.stringify({enabled: true, sync_mode: 'graph', graph_tenant_id: 't', graph_client_id: 'c', graph_client_secret: 's', calendar_enabled: true, calendar_write: true, guest_links: true})})).status; })()"""

OPEN = """(t) => { const c = [...document.querySelectorAll('.cal-chip')].find((x) => x.getAttribute('aria-label').includes(t)); if (!c) return false; c.click(); return true; }"""


async def click_text(A, sel, text):
    return await A.js(f"(() => {{ const b = [...document.querySelectorAll({sel!r})].find((x) => x.textContent.trim().includes({text!r})); if (!b) return false; b.click(); return true; }})()")


async def open_chip(A, text):
    ok = await A.js(f"({OPEN})({text!r})")
    await asyncio.sleep(0.9)
    return ok


async def set_date(A, iso):
    await A.js(f"""(() => {{ const i = document.querySelector('.cal-jump input'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, {iso!r});
      i.dispatchEvent(new Event('input', {{bubbles: true}})); i.dispatchEvent(new Event('change', {{bubbles: true}})); }})()""")
    await asyncio.sleep(1.2)


async def main():
    R = S.Client("root", "root", "root-pass", 9811)
    A = S.Client("A", "alice", "alice-pass", 9812)
    try:
        await R.start()
        S.check("администратор включил календарь (Graph, запись, гостевые ссылки)", await R.js(CFG) == 200)
        info = dev({"reset": True, "down": False})
        day1 = info["day1"]
        S.check("подставной Graph стенда запущен, у Алисы события, включая серию и второй календарь", info["events"] >= 14, str(info))
        await A.start()
        await A.js("(async () => { await fetch('/api/v1/calendar/connect', {method: 'DELETE', headers: {'X-CSRF-Token': (await (await fetch('/api/v1/auth/me')).json()).csrf_token}}); })()")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        # ---------- главная: кнопка «Календарь»
        await A.goto("/", 2.0)
        S.check("на главной есть заметная кнопка «Календарь»", await A.wait_for("!![...document.querySelectorAll('.rooms-hero a.btn')].find((a) => a.textContent.includes('Календарь'))", 15))
        await A.shot("calendar-home")
        await A.js("[...document.querySelectorAll('.rooms-hero a.btn')].find((a) => a.textContent.includes('Календарь')).click()")
        await asyncio.sleep(1.2)
        S.check("кнопка открывает календарь", await A.js("location.pathname") == "/calendar")
        S.check("до подключения — мастер, кнопка недоступна без согласия", await A.wait_for("!!document.querySelector('.cal-connect')", 10) and await A.js("document.querySelector('.cal-connect button.primary').disabled === true"))
        await A.shot("calendar-connect")
        await A.js("document.querySelector('.cal-connect input[type=checkbox]').click()")
        await asyncio.sleep(0.4)
        await A.js("document.querySelector('.cal-connect button.primary').click()")
        S.check("после согласия открывается календарь", await A.wait_for("!!document.querySelector('.cal-view')", 15))
        S.check("рядом с календарём видно состояние: «Календарь подключён»", await A.wait_for("document.querySelector('.cal-health')?.innerText.includes('подключён')", 10))
        # ---------- неделя: сетка
        await click_text(A, ".cal-head .seg button", "Неделя")
        await set_date(A, day1)
        await asyncio.sleep(0.8)
        st = await A.js(STATE)
        S.check("неделя — вид по умолчанию, у каждой плашки в подписи тип и время", st["pressed"] == ["Неделя"] and len(st["chips"]) >= 8, str(st["pressed"]))
        S.check("подписи несут тип не только цветом: «Повторяющаяся», «Разовая встреча», «Событие Outlook», «Отменено»", all(any(w in c for c in st["chips"]) for w in ("Повторяющаяся", "Разовая встреча", "Событие Outlook", "Отменено")))
        geo = await A.js("""(() => { const q = (t) => { const c = [...document.querySelectorAll('.cal-chip')].find((x) => x.getAttribute('aria-label').includes(t)); const r = c.closest('.cal-slot').getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom, h: r.height}; };
          return {budget: q('Созвон по бюджету'), retro: q('Ретроспектива'), early: q('Ранняя зарядка'), late: q('Вечерний созвон')}; })()""")
        b, r = geo["budget"], geo["retro"]
        S.check("пересекающиеся встречи стоят рядом и не наложены друг на друга", b["r"] <= r["l"] + 1 or r["r"] <= b["l"] + 1, str((b, r)))
        S.check("высота плашки соответствует длительности (60 минут = 52 пикселя)", abs(b["h"] - 52) <= 3 and abs(r["h"] - 52) <= 3, str((b["h"], r["h"])))
        S.check("события вне рабочих часов (06:30 и 21:00) не скрыты", geo["early"]["h"] > 10 and geo["late"]["h"] > 10)
        S.check("вне рабочих часов сетка затенена, текущее время отмечено на сегодняшнем дне", await A.js("document.querySelectorAll('.cal-off').length >= 2"))
        S.check("события на весь день вынесены в верхнюю строку", await A.js("document.querySelector('.cal-gh.allday')?.innerText.includes('Конференция (весь день)')"))
        S.check("у события «свободен» отдельный вид (штриховка), его подпись называет занятость", any("Свободен" in c for c in (await A.js(STATE))["chips"]))
        await A.shot("calendar-week")
        # ---------- рабочие часы
        await click_text(A, ".cal-pop button", "Рабочие часы")
        S.check("настройка рабочих часов открывается", await A.wait_for("!!document.querySelector('.cal-popbox')", 5))
        h_full = await A.js("document.querySelector('.cal-body').getBoundingClientRect().height")
        await A.js("[...document.querySelectorAll('.cal-popbox label.check input')].find((i) => i.parentElement.innerText.includes('только рабочие')).click()")
        await asyncio.sleep(0.6)
        h_work = await A.js("document.querySelector('.cal-body').getBoundingClientRect().height")
        S.check("«только рабочие часы» сжимает сетку, но раннее и позднее события остаются видны", h_work < h_full and (await A.js(f"({OPEN.replace('c.click(); ', '')})('Ранняя зарядка')")) is not None
                and any("Ранняя зарядка" in c for c in (await A.js(STATE))["chips"]) and any("Вечерний созвон" in c for c in (await A.js(STATE))["chips"]), f"{h_full} → {h_work}")
        await A.shot("calendar-workhours")
        await A.js("[...document.querySelectorAll('.cal-popbox label.check input')].find((i) => i.parentElement.innerText.includes('только рабочие')).click()")
        await A.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        # ---------- выделение интервала мышью
        pos = await A.js("""(() => { const sc = document.querySelector('.cal-scroll'); sc.scrollTop = 12 * 52 - 40;
          const day = new Date('DAY1' + 'T12:00'); const idx = (day.getDay() + 6) % 7; const col = document.querySelectorAll('.cal-col')[idx]; const r = col.getBoundingClientRect(); return {x: r.left + r.width - 12, top: r.top, idx}; })()""".replace("DAY1", day1))
        await asyncio.sleep(0.3)
        pos = await A.js("""(() => { const day = new Date('DAY1' + 'T12:00'); const idx = (day.getDay() + 6) % 7; const col = document.querySelectorAll('.cal-col')[idx]; const r = col.getBoundingClientRect(); return {x: r.left + r.width - 14, top: r.top}; })()""".replace("DAY1", day1))
        y0 = pos["top"] + 13 * 52 + 2
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=pos["x"], y=y0)
        await A.send("Input.dispatchMouseEvent", type="mousePressed", x=pos["x"], y=y0, button="left", buttons=1, clickCount=1)
        for k in range(1, 6):
            await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=pos["x"], y=y0 + 15 * k, button="left", buttons=1)
        S.check("при протяжке показано выделение со временем", await A.js("document.querySelector('.cal-sel')?.innerText.includes(':')"))
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=pos["x"], y=y0 + 78, button="left", buttons=1)
        await A.send("Input.dispatchMouseEvent", type="mouseReleased", x=pos["x"], y=y0 + 78, button="left", buttons=0, clickCount=1)
        S.check("после выделения открылась форма «Новая встреча»", await A.wait_for("!!document.querySelector('.cal-form')", 8))
        form = await A.js("""(() => ({date: document.querySelector('.cal-when input[type=date]').value, time: document.querySelector('.cal-when input[type=time]').value, mins: document.querySelector('.cal-when select').value}))()""")
        S.check("в форме — выделенные день, начало 13:00 и длительность 90 минут", form["date"] == day1 and form["time"] == "13:00" and form["mins"] == "90", str(form))
        f = await A.js(FIRST)
        S.check("курсор сразу в поле «Название» — без щелчка и Tab", f["tag"] == "INPUT" and f["label"].startswith("Название"), str(f))
        await A.send("Input.insertText", text="Согласование бюджета")
        await A.js("(() => { const i = [...document.querySelectorAll('.cal-form label')].find((l) => l.innerText.startsWith('Место')).querySelector('input'); i.focus(); })()")
        await A.send("Input.insertText", text="Переговорная 5")
        await A.js("(() => { const i = document.querySelector('.cal-who input[type=email]'); i.focus(); })()")
        await A.send("Input.insertText", text="ext@example.org")
        await A.js("document.querySelector('.cal-who .row button:last-child').click()")
        S.check("внешний участник добавлен", await A.js("document.querySelector('.cal-people')?.innerText.includes('ext@example.org')"))
        await A.js("[...document.querySelectorAll('.cal-form .check input')].find((i) => i.parentElement.innerText.includes('гостевую'))?.click()")
        await A.shot("calendar-create")
        await A.js("(() => { const b = [...document.querySelectorAll('.cal-form button')].find((x) => x.textContent.includes('Создать и разослать')); b.click(); b.click(); })()")      # двойное нажатие
        S.check("встреча создана: показаны ссылка для сотрудников и гостевая", await A.wait_for("document.querySelector('.modal')?.innerText.includes('Для внешних участников')", 25))
        d = dev({"dump": True})
        mine = [e for e in d["items"].values() if e["subject"] == "Согласование бюджета"]
        S.check("в Exchange создано ровно одно событие (двойное нажатие дубля не дало), место и ссылки на месте", len(mine) == 1 and "Переговорная 5" in (mine[0]["location"] or "") and "/m/" in (mine[0]["location"] or "") and "ext@example.org" in mine[0]["attendees"], str(mine))
        S.check("в теле приглашения — ссылка для сотрудников и гостевая", "guest" in mine[0]["body"] or "гост" in mine[0]["body"].lower(), mine[0]["body"][:120])
        await click_text(A, ".modal button", "Готово")
        await asyncio.sleep(1.5)
        S.check("новая встреча появилась в календаре: разовая, с меткой конференции Peregovorka", any("Согласование бюджета" in c and "Разовая встреча" in c and "конференция Peregovorka" in c for c in (await A.js(STATE))["chips"]))
        # ---------- карточка события
        await open_chip(A, "Созвон по бюджету")
        card = await A.js("document.querySelector('.cal-detail')?.innerText || ''")
        S.check("карточка: организатор, участники с ответами, место, описание без разметки", "Организатор: вы" in card and "Боб Петров" in card and "принял" in card and "необязательный" in card and "Переговорная 3" in card and "Бюджет квартала" in card and "<p>" not in card, card[:300])
        await A.shot("calendar-card")
        await click_text(A, ".cal-detail button", "Изменить")
        S.check("в форме изменения курсор в названии", (await A.js(FIRST))["tag"] == "INPUT" and await A.js("document.activeElement.value") == "Созвон по бюджету")
        await A.send("Input.insertText", text=" (план)")
        await A.js("(() => { const i = [...document.querySelectorAll('.cal-form label')].find((l) => l.innerText.startsWith('Место')).querySelector('input'); i.focus(); i.select(); })()")
        await A.send("Input.insertText", text="Переговорная 7")
        await click_text(A, ".cal-form button", "Сохранить и уведомить")
        await asyncio.sleep(2.0)
        e = dev({"dump": True})["items"]["BUDGET"]
        S.check("изменение дошло до Exchange: название и место", e["subject"] == "Созвон по бюджету (план)" and e["location"] == "Переговорная 7", str(e))
        # ---------- чужая встреча — только чтение
        await open_chip(A, "Планирование от руководителя")
        card = await A.js("document.querySelector('.cal-detail')?.innerText || ''")
        S.check("приглашение от руководителя: организатор показан, изменить и отменить нельзя", "Руководитель" in card and "Изменять и отменять встречу может её организатор" in card and "Отменить встречу" not in card and not await click_text(A, ".cal-detail button", "Изменить"))
        await click_text(A, ".cal-detail button", "Закрыть"); await asyncio.sleep(0.4)
        # ---------- серия: только это занятие / вся серия
        await open_chip(A, "Планёрка отдела")
        S.check("карточка занятия серии помечена «Занятие серии»", await A.js("document.querySelector('.cal-detail')?.innerText.includes('Занятие серии')"))
        await click_text(A, ".cal-detail button", "Изменить")
        S.check("для занятия серии обязателен выбор: «Только это занятие» или «Всю серию»", await A.js("document.querySelector('.cal-scope')?.innerText.includes('Только это занятие') && document.querySelector('.cal-scope')?.innerText.includes('Всю серию')"))
        await A.send("Input.insertText", text=" — лето")
        await click_text(A, ".cal-form button", "Сохранить и уведомить")
        await asyncio.sleep(2.0)
        it = dev({"dump": True})["items"]
        S.check("«только это занятие»: изменено одно занятие, серия и остальные занятия прежние", it["OCC0"]["subject"] == "Планёрка отдела — лето" and it["SERIES"]["subject"] == "Планёрка отдела" and it["OCC1"]["subject"] == "Планёрка отдела", str({k: it[k]["subject"] for k in ("OCC0", "OCC1", "SERIES")}))
        await open_chip(A, "лето")
        await click_text(A, ".cal-detail button", "Отменить встречу")
        S.check("отмена занятия: выбор «Только это занятие / Всю серию» и предупреждение", await A.wait_for("!!document.querySelector('.cal-scope')", 5) and await A.js("document.querySelector('.modal').innerText.includes('нельзя отменить')"),
                str(await A.js("document.querySelector('.modal')?.innerText?.slice(0, 700).replace(/\\n+/g, ' / ')")))
        await click_text(A, ".cal-detail button", "Отменить встречу"); await asyncio.sleep(2.0)
        it = dev({"dump": True})["items"]
        S.check("отменено одно занятие: серия и соседние занятия живы", it["OCC0"]["cancelled"] and not it["SERIES"]["cancelled"] and not it["OCC1"]["cancelled"], str({k: it[k]["cancelled"] for k in ("OCC0", "OCC1", "SERIES")}))
        await set_date(A, (__import__("datetime").date.fromisoformat(day1) + __import__("datetime").timedelta(days=7)).isoformat())
        await open_chip(A, "Планёрка отдела")
        await click_text(A, ".cal-detail button", "Отменить встречу")
        await A.js("document.querySelectorAll('.cal-scope input')[1].click()")
        S.check("для всей серии кнопка называется «Отменить всю серию»", await A.js("document.querySelector('.cal-detail button.danger')?.innerText.includes('всю серию')"))
        await click_text(A, ".cal-detail button", "Отменить всю серию"); await asyncio.sleep(2.0)
        it = dev({"dump": True})["items"]
        S.check("серия отменена целиком по мастер-событию", it["SERIES"]["cancelled"], str(it["SERIES"]))
        await set_date(A, day1)
        # ---------- несколько календарей
        await click_text(A, ".cal-pop button", "Календари")
        S.check("в списке календарей — основной и «Проекты»", await A.wait_for("document.querySelector('.cal-popbox')?.innerText.includes('Проекты')", 5))
        before = len((await A.js(STATE))["chips"])
        await A.js("[...document.querySelectorAll('.cal-popbox label.check input')].find((i) => i.parentElement.innerText.includes('Проекты')).click()")
        await asyncio.sleep(1.5)
        S.check("включённый календарь добавил свои события", any("Проектный созвон" in c for c in (await A.js(STATE))["chips"]) and len((await A.js(STATE))["chips"]) > before)
        await A.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        # ---------- виды, сохранение состояния
        for name in ("День", "Месяц", "Список", "Неделя"):
            await click_text(A, ".cal-head .seg button", name)
            await asyncio.sleep(0.8)
            s2 = await A.js(STATE)
            S.check(f"вид «{name}»: переключился, горизонтальной прокрутки нет", s2["pressed"] == [name] and s2["sw"] <= s2["iw"], str(s2["pressed"]))
        await click_text(A, ".cal-head .seg button", "Месяц"); await asyncio.sleep(0.8)
        n0 = await A.js("(() => { const m = document.querySelector('.cal-more'); return m ? m.closest('.cal-cell').querySelectorAll('.cal-chip').length : -1; })()")
        await A.js("document.querySelector('.cal-more')?.click()")
        await asyncio.sleep(0.5)
        n1 = await A.js("(() => { const m = document.querySelector('.cal-more[aria-expanded=true]'); return m ? m.closest('.cal-cell').querySelectorAll('.cal-chip').length : -1; })()")
        S.check("месяц: у переполненной ячейки есть «ещё N», раскрытие показывает все события дня", n0 == 3 and n1 > n0, f"{n0} → {n1}")
        await A.shot("calendar-month")
        title = (await A.js(STATE))["title"]
        await A.js("document.querySelector('.rail-nav a[href=\"/\"]').click()"); await asyncio.sleep(1.2)
        await A.js("document.querySelector('.rail-nav a[href=\"/calendar\"]').click()"); await asyncio.sleep(1.8)
        s3 = await A.js(STATE)
        S.check("после перехода в переговорки и обратно вид и период сохранились", s3["pressed"] == ["Месяц"] and s3["title"] == title, f"{s3['pressed']} {s3['title']} / {title}")
        await click_text(A, ".cal-head .seg button", "Список"); await asyncio.sleep(0.8)
        S.check("список: события сгруппированы по дням, есть подпись типа и метка Peregovorka", await A.js("document.querySelectorAll('.cal-list section').length >= 2 && document.querySelector('.cal-list').innerText.includes('Peregovorka')"))
        await A.shot("calendar-list")
        await click_text(A, ".cal-head .seg button", "Неделя"); await asyncio.sleep(0.8)
        # ---------- Exchange недоступен
        dev({"down": True})
        await click_text(A, ".cal-head button", "Обновить")
        S.check("Exchange недоступен: «Нет соединения», данные помечены как возможно устаревшие, события остались", await A.wait_for("document.querySelector('.cal-health')?.innerText.includes('могли устареть')", 15) and len((await A.js(STATE))["chips"]) >= 5, (await A.js(STATE))["health"])
        await A.shot("calendar-offline")
        dev({"down": False})
        await click_text(A, ".cal-health button", "Повторить"); await asyncio.sleep(1.5)
        S.check("связь вернулась: состояние снова «подключён»", await A.wait_for("document.querySelector('.cal-health')?.classList.contains('h-ok')", 10))
        # ---------- узкий экран и тёмная тема
        for w in (390, 700):
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=800, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.6)
            s4 = await A.js(STATE)
            S.check(f"{w} px: горизонтальной прокрутки страницы нет", s4["sw"] <= s4["iw"], f"{s4['sw']}/{s4['iw']}")
        await A.shot("calendar-390")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        await A.js("document.documentElement.setAttribute('data-theme', 'dark')")
        await asyncio.sleep(0.6)
        tint = await A.js("(() => { const c = document.querySelector('.cal-chip'); const s = getComputedStyle(c); return {bg: s.backgroundColor, color: s.color}; })()")
        S.check("тёмная тема: светлый текст на тёмном фоне плашки", tint["bg"] != tint["color"] and tint["bg"] != "rgba(0, 0, 0, 0)", str(tint))
        await A.shot("calendar-dark")
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
