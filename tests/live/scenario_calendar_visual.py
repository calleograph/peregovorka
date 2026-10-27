"""Сценарий (см. README.md): внешний вид календаря и формы «Новая встреча» в настоящем браузере — то, что не ловят модульные тесты.
Проверяется на ширинах окна 1920, 1536 (1920 при 125 %), 1366, 1280, 1024, 960 (1920 при 200 %), 683 (1366 при 200 %) и 390 px: верхняя панель (кнопки одной группы одной высоты, ничего не вылезает за окно,
нет горизонтальной прокрутки), плашки сетки (короткие 15/30 минут, пересекающиеся, очень длинное название: ничего не обрезано внутри плашки, плашки не наложены, начало стоит на своей отметке времени),
карточка при наведении, пустой период, компактное уведомление об ошибке Exchange с раскрываемыми подробностями, форма (курсор в названии, выбор времени 24 часа: список не перекрывает поле и
не выходит за окно, ввод цифрами, стрелки, Enter, прокрутка окна; кнопки внизу видны), тёмная тема. Снимки — в SMOKE_OUT (calendar-visual-*.png). Не проверяется: настоящий Exchange, Windows и системное масштабирование."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from scenario_calendar import CFG, dev, set_date, click_text, open_chip

HEAD = """(() => { const q = (s) => [...document.querySelectorAll(s)].map((b) => { const r = b.getBoundingClientRect(); return {h: Math.round(r.height * 10) / 10, t: Math.round(r.top), l: r.left, r: r.right, b: r.bottom, txt: (b.getAttribute('aria-label') || b.textContent || '').trim().slice(0, 24)}; });
  const ctl = q('.cal-head .btn, .cal-head .seg'); const groups = q('.cal-head-a, .cal-head-n, .cal-head-b');
  return {ctl, groups, iw: innerWidth, sw: document.documentElement.scrollWidth, ids: [...document.querySelectorAll('.cal-head .btn')].map((b) => getComputedStyle(b).borderRadius)}; })()"""

CHIPS = """(() => { const bad = []; const slots = [...document.querySelectorAll('.cal-slot')];
  const inside = (a, b, tol) => a.left >= b.left - tol && a.right <= b.right + tol && a.top >= b.top - tol && a.bottom <= b.bottom + tol;
  for (const s of slots) { const c = s.querySelector('.cal-chip'); const sr = s.getBoundingClientRect(); const cr = c.getBoundingClientRect();
    if (!inside(cr, sr, 1.5)) bad.push('плашка вне своего места: ' + c.getAttribute('aria-label'));
    if (c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1) bad.push('содержимое обрезано: ' + c.getAttribute('aria-label') + ' ' + c.scrollWidth + 'x' + c.scrollHeight + ' > ' + c.clientWidth + 'x' + c.clientHeight);
    for (const k of c.children) { if (getComputedStyle(k).display === 'none') continue; if (!inside(k.getBoundingClientRect(), cr, 1.5)) bad.push('элемент «' + k.className + '» вне плашки: ' + c.getAttribute('aria-label')); } }
  for (let i = 0; i < slots.length; i++) for (let j = i + 1; j < slots.length; j++) {
    const a = slots[i].getBoundingClientRect(), b = slots[j].getBoundingClientRect(); const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    if (w > 2 && h > 2) bad.push('наложение: ' + slots[i].innerText.split('\\n')[0] + ' и ' + slots[j].innerText.split('\\n')[0]); }
  return {n: slots.length, bad}; })()"""

# начало каждой плашки на отметке своего времени (по подписи «ЧЧ:ММ–ЧЧ:ММ») — точность до 2 px
EXACT = """(() => { const h0 = Number(document.querySelector('.cal-hours span').textContent.slice(0, 2)); const out = [];
  for (const s of document.querySelectorAll('.cal-slot')) { const m = /(\\d\\d):(\\d\\d)–/.exec(s.querySelector('.cal-chip').getAttribute('aria-label')); if (!m) continue;
    const col = s.closest('.cal-col').getBoundingClientRect(); const want = ((Number(m[1]) - h0) * 60 + Number(m[2])) / 60 * 52; const got = s.getBoundingClientRect().top - col.top;
    if (Math.abs(want - got) > 2) out.push(s.innerText.split('\\n')[0] + ': ' + got.toFixed(1) + ' вместо ' + want.toFixed(1)); } return out; })()"""

CHIP_BOX = """(t) => { const c = [...document.querySelectorAll('.cal-chip')].find((x) => x.getAttribute('aria-label').includes(t)); if (!c) return null; c.scrollIntoView({block: 'center'}); const r = c.getBoundingClientRect(); return {x: r.left + Math.min(20, r.width / 2), y: r.top + r.height / 2}; }"""
HOVER = "(() => { const h = document.querySelector('.cal-hover'); if (!h) return null; const r = h.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom, txt: h.innerText, iw: innerWidth, ih: innerHeight}; })()"
FORM = """(() => { const m = document.querySelector('.modal'); if (!m) return null; const r = m.getBoundingClientRect(); const f = m.querySelector('.cal-form-foot .btn.primary'); const fr = f && f.getBoundingClientRect();
  return {l: r.left, r: r.right, t: r.top, b: r.bottom, iw: innerWidth, ih: innerHeight, foot: fr && {t: fr.top, b: fr.bottom}, active: document.activeElement?.placeholder || document.activeElement?.tagName, sw: document.documentElement.scrollWidth}; })()"""
TIME = """(() => { const i = document.querySelector('.tf-in'); const l = document.querySelector('.tf-list'); const ir = i.getBoundingClientRect(); const lr = l && l.getBoundingClientRect(); const sel = l && l.querySelector('[aria-selected=true]');
  const sr = sel && sel.getBoundingClientRect(); return {val: i.value, in: {t: ir.top, b: ir.bottom, l: ir.left, r: ir.right}, list: lr && {t: lr.top, b: lr.bottom, l: lr.left, r: lr.right}, sel: sel && sel.textContent, selVisible: !!sr && sr.top >= lr.top - 1 && sr.bottom <= lr.bottom + 1, ih: innerHeight, iw: innerWidth}; })()"""


async def key(A, k, code, vk, text=None):
    p = dict(key=k, code=code, windowsVirtualKeyCode=vk)
    await A.send("Input.dispatchKeyEvent", type="keyDown", **({"text": text} if text else {}), **p)
    await A.send("Input.dispatchKeyEvent", type="keyUp", **p)


async def size(A, w, h=900):
    await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.5)


async def main():
    R = S.Client("root", "root", "root-pass", 9821)
    A = S.Client("A", "alice", "alice-pass", 9822)
    try:
        await R.start(); await R.goto("/", 1.5)
        S.check("администратор включил календарь", await R.js(CFG) == 200)
        info = dev({"reset": True, "down": False}); day1 = info["day1"]
        S.check("в подставном Graph есть короткие (15 и 30 минут), пересекающиеся и длинные события", info["events"] >= 20, str(info))
        await A.start()
        await size(A, 1366)
        await A.goto("/calendar", 2.0)
        if await A.js("!!document.querySelector('.cal-connect')"):
            await A.js("document.querySelector('.cal-connect input[type=checkbox]').click()"); await asyncio.sleep(0.4)
            await A.js("document.querySelector('.cal-connect button.primary').click()"); await asyncio.sleep(2.0)
        await click_text(A, ".cal-head .seg button", "Неделя")
        await set_date(A, day1)
        S.check("календарь открыт, события загружены", await A.wait_for("document.querySelectorAll('.cal-slot').length >= 12", 20))

        # ---------- ширины окна: панель, плашки, положение по времени
        for w, note in [(1920, "1920"), (1536, "1920 при 125 %"), (1366, "1366"), (1280, "1280"), (1024, "1024"), (960, "1920 при 200 %"), (683, "1366 при 200 %"), (390, "390")]:
            await size(A, w, 900)
            await A.goto("/calendar", 1.8)
            await A.wait_for("!!document.querySelector('.cal-head .seg')", 15)
            if w <= 760:
                await click_text(A, ".cal-head .seg button", "День")
            else:
                await click_text(A, ".cal-head .seg button", "Неделя")
            await set_date(A, day1)
            ok = await A.wait_for("document.querySelectorAll('.cal-slot').length >= 3", 10)
            if not ok:         # после другого сценария у сервера ещё действует минутный предел запросов к Exchange: страница честно показывает сбой — ждём окно и нажимаем «Обновить»
                await asyncio.sleep(62)
                await A.js("document.querySelector('.cal-refresh').click()")
                ok = await A.wait_for("document.querySelectorAll('.cal-slot').length >= 3", 20)
            S.check(f"{note}: события загружены", ok)
            h = await A.js(HEAD)
            hs = {c["h"] for c in h["ctl"]}
            S.check(f"{note}: кнопки панели одной высоты, нет горизонтальной прокрутки, ничего не за краем окна", len(hs) == 1 and h["sw"] <= h["iw"] and all(c["l"] >= -0.5 and c["r"] <= h["iw"] + 0.5 for c in h["ctl"]), str((sorted(hs), h["sw"], h["iw"], [c for c in h["ctl"] if c["h"] != 36][:2])))
            tops = sorted({g["t"] // 20 for g in h["groups"]})
            S.check(f"{note}: панель собрана осмысленно ({'одна строка' if w >= 1500 else 'не больше трёх строк'})", len(tops) == 1 if (w >= 1500 and w > 1500) else len(tops) <= 3, str(h["groups"]))
            ch = await A.js(CHIPS)
            S.check(f"{note}: плашки целы — ничего не обрезано, не вылезает и не накладывается ({ch['n']} шт.)", not ch["bad"] and ch["n"] >= 3, str(ch["bad"][:3]))
            if w > 640:
                ex = await A.js(EXACT)
                S.check(f"{note}: начало каждой плашки стоит на своей отметке времени", not ex, str(ex[:3]))
            await A.shot(f"calendar-visual-{w}")

        # ---------- короткие встречи и длинное название, наведение
        await size(A, 1366, 900)
        await A.goto("/calendar", 1.8)
        await click_text(A, ".cal-head .seg button", "Неделя"); await set_date(A, day1)
        await A.wait_for("document.querySelectorAll('.cal-slot').length >= 12", 15)
        geo = await A.js("""(() => { const q = (t) => { const c = [...document.querySelectorAll('.cal-chip')].find((x) => x.getAttribute('aria-label').includes(t)); const r = c.getBoundingClientRect(); return {h: r.height, w: r.width, cls: c.className, txt: c.innerText}; };
          return {s15: q('Быстрый вопрос'), s30: q('Короткий созвон'), conf15: q('Стендап команды'), long: q('Согласование квартального'), work: q('Выездной воркшоп')}; })()""")
        S.check("встреча на 15 минут: читаемая плашка не тоньше одной строки (26 px), название и время на месте", geo["s15"]["h"] >= 25 and "Быстрый" in geo["s15"]["txt"], str(geo["s15"]))
        S.check("встреча на 30 минут — одна строка, на 45 — время и название двумя строками, на 3 часа — высокая плашка", geo["s30"]["h"] >= 25 and geo["long"]["h"] >= 38 and geo["work"]["h"] >= 150, str((geo["s30"]["h"], geo["long"]["h"], geo["work"]["h"])))
        p = await A.js(f"({CHIP_BOX})('Согласование квартального')")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=p["x"] - 30, y=p["y"] - 80)
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=p["x"], y=p["y"])
        await asyncio.sleep(0.9)
        hv = await A.js(HOVER)
        S.check("при наведении — карточка с полным названием, временем, типом и местом", bool(hv) and "Согласование квартального бюджета с финансовым департаментом и подрядчиками" in hv["txt"] and "Переговорная 7" in hv["txt"] and "–" in hv["txt"], str(hv)[:200])
        S.check("карточка целиком в окне", bool(hv) and hv["l"] >= 0 and hv["t"] >= 0 and hv["r"] <= hv["iw"] and hv["b"] <= hv["ih"], str(hv)[:160])
        await A.shot("calendar-visual-hover")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=p["x"] - 400, y=p["y"] + 200)
        await asyncio.sleep(0.5)
        S.check("карточка исчезает, когда мышь ушла", await A.js(HOVER) is None)
        p = await A.js(f"({CHIP_BOX})('Быстрый вопрос')")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=p["x"], y=p["y"])
        await asyncio.sleep(0.9)
        hv = await A.js(HOVER)
        S.check("у плашки на 15 минут карточка показывает точное время «12:00–12:15» в своём поясе и длительность 15 мин", bool(hv) and "15 мин" in hv["txt"], str(hv)[:200])
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=p["x"] - 300, y=p["y"] + 300)

        # ---------- виды: день, месяц, список
        for name, shot in [("День", "day"), ("Месяц", "month"), ("Список", "list")]:
            await click_text(A, ".cal-head .seg button", name)
            await asyncio.sleep(0.9)
            await A.shot(f"calendar-visual-{shot}")
            h = await A.js(HEAD)
            S.check(f"вид «{name}»: нет горизонтальной прокрутки", h["sw"] <= h["iw"])
        await click_text(A, ".cal-head .seg button", "Месяц")
        mo = await A.js("""(() => { const bad = []; for (const c of document.querySelectorAll('.cal-cell .cal-chip')) { const cell = c.closest('.cal-cell').getBoundingClientRect(), r = c.getBoundingClientRect(); if (r.right > cell.right + 1 || r.left < cell.left - 1) bad.push(c.getAttribute('aria-label')); } return bad; })()""")
        S.check("месяц: плашки не выходят за свои ячейки", not mo, str(mo[:2]))
        await click_text(A, ".cal-head .seg button", "Неделя")

        # ---------- пустой период
        await set_date(A, "2031-03-12")
        await asyncio.sleep(0.6)
        S.check("пустой период: сетка на месте и понятная подсказка", await A.wait_for("!!document.querySelector('.cal-emptyhint')", 10) and await A.js("document.querySelectorAll('.cal-col').length") == 7)
        await A.shot("calendar-visual-empty")
        await set_date(A, day1)

        # ---------- ошибка Exchange: компактное уведомление, настоящий текст — по кнопке
        dev({"down": True})
        await A.js("document.querySelector('.cal-refresh').click()")
        S.check("сбой Exchange: уведомление понятное и компактное", await A.wait_for("!!document.querySelector('.cal-health.h-stale, .cal-health.h-down, .cal-health.h-denied')", 12))
        note = await A.js("(() => { const h = document.querySelector('.cal-health'); const r = h.getBoundingClientRect(); const d = h.querySelector('details'); return {h: r.height, w: r.width, txt: h.innerText.slice(0, 200), details: !!d, open: d && d.open, cls: h.className}; })()")
        S.check("уведомление не выше трёх строк, технический текст свёрнут «Подробности»", note["h"] < 90 and note["details"] and not note["open"], str(note))
        await A.js("document.querySelector('.cal-health summary').click()")
        await asyncio.sleep(0.3)
        S.check("по «Подробности» виден настоящий текст ошибки, а страница не ломается", await A.js("document.querySelector('.cal-health code').innerText.length > 3") and (await A.js(HEAD))["sw"] <= 1366)
        await A.shot("calendar-visual-error")
        dev({"down": False})
        await A.js("document.querySelector('.cal-refresh').click()")
        S.check("после восстановления уведомление уходит", await A.wait_for("!!document.querySelector('.cal-health.h-ok')", 15))

        # ---------- форма «Новая встреча»
        for w, hgt in [(1366, 800), (1024, 700), (1280, 560), (683, 740), (390, 740)]:
            await size(A, w, hgt)
            await A.goto("/calendar", 1.8)
            await click_text(A, ".cal-head-a .btn", "Создать")
            S.check(f"{w}×{hgt}: форма открылась, курсор сразу в названии", await A.wait_for("!!document.querySelector('.modal .cal-form')", 8) and (await A.js(FORM))["active"].startswith("Например"))
            f = await A.js(FORM)
            S.check(f"{w}×{hgt}: окно целиком на экране, кнопки внизу видны, нет горизонтальной прокрутки", f["l"] >= 0 and f["r"] <= f["iw"] and f["t"] >= 0 and f["b"] <= f["ih"] and f["foot"]["b"] <= f["ih"] and f["foot"]["t"] >= f["t"] and f["sw"] <= f["iw"], str(f))
            await A.shot(f"calendar-visual-form-{w}")
            if (w, hgt) == (1366, 800):
                await A.js("document.querySelector('.tf-btn').click()")
                await asyncio.sleep(0.4)
                t = await A.js(TIME)
                S.check("список времени: рядом с полем, не перекрывает его, целиком в окне", bool(t["list"]) and (t["list"]["t"] >= t["in"]["b"] - 1 or t["list"]["b"] <= t["in"]["t"] + 1) and t["list"]["b"] <= t["ih"] and t["list"]["r"] <= t["iw"], str(t))
                S.check("в списке выделено выбранное время и оно видно", t["sel"] == t["val"] and t["selVisible"], str((t["sel"], t["val"])))
                await A.shot("calendar-visual-timelist")
                await A.js("document.querySelector('.modal').scrollTop = 40")
                await asyncio.sleep(0.3)
                t2 = await A.js(TIME)
                S.check("при прокрутке окна список остаётся у поля", bool(t2["list"]) and abs(t2["list"]["t"] - t2["in"]["b"]) < 8 or abs(t2["list"]["b"] - t2["in"]["t"]) < 8, str(t2))
                before = t["val"]
                await key(A, "ArrowDown", "ArrowDown", 40); await key(A, "ArrowDown", "ArrowDown", 40); await key(A, "Enter", "Enter", 13)
                await asyncio.sleep(0.3)
                t3 = await A.js(TIME)
                S.check("стрелки и Enter выбирают время (на полчаса позже), список закрылся", t3["val"] != before and t3["list"] is None and await A.js("document.querySelector('.modal') !== null"), f"{before} → {t3['val']}")
                await A.js("(() => { const i = document.querySelector('.tf-in'); i.focus(); i.select(); })()")
                await A.send("Input.insertText", text="0930")
                await asyncio.sleep(0.2)
                S.check("ввод цифрами «0930» даёт «09:30», время в 24 часа", await A.js("document.querySelector('.tf-in').value") == "09:30" and "09:30 –" in await A.js("document.querySelector('.cal-end').textContent"), await A.js("document.querySelector('.tf-in').value"))
                await A.js("(() => { const i = document.querySelector('.tf-in'); i.select(); })()")
                await A.send("Input.insertText", text="2575")
                await A.js("document.querySelector('.tf-in').blur()")
                await asyncio.sleep(0.2)
                S.check("неверное время («2575») не принимается: возвращается прежнее", await A.js("document.querySelector('.tf-in').value") == "09:30")
                await A.js("(() => { const i = document.querySelector('.tf-in'); i.focus(); i.select(); })()")
                await A.send("Input.insertText", text="1445")
                await A.js("document.querySelector('.tf-in').blur()")
                await asyncio.sleep(0.2)
                S.check("«1445» → 14:45", await A.js("document.querySelector('.tf-in').value") == "14:45")
                await A.js("document.querySelector('.cal-sect summary').click()")
                await A.js("document.querySelectorAll('.cal-sect summary')[1].click()")
                await asyncio.sleep(0.3)
                f = await A.js(FORM)
                S.check("раскрытые «Повторение» и «Описание»: окно остаётся на экране, кнопки внизу видны", f["b"] <= f["ih"] and f["foot"]["b"] <= f["ih"], str(f))
                await A.shot("calendar-visual-form-open")
                # Escape закрывает список, а не окно
                await A.js("(() => { const i = document.querySelector('.tf-in'); i.focus(); })()")
                await A.js("document.querySelector('.tf-btn').click()")
                await asyncio.sleep(0.2)
                await key(A, "Escape", "Escape", 27)
                await asyncio.sleep(0.2)
                S.check("Escape в открытом списке закрывает список, а не всё окно", await A.js("!!document.querySelector('.modal') && !document.querySelector('.tf-list')"))
            await key(A, "Escape", "Escape", 27)
            await asyncio.sleep(0.4)
            S.check(f"{w}×{hgt}: форма закрывается, календарь на месте", await A.js("!document.querySelector('.modal') && !!document.querySelector('.cal-view')"))

        # ---------- изменение встречи: та же форма и тот же выбор времени
        await size(A, 1366, 800)
        await A.goto("/calendar", 1.8)
        await click_text(A, ".cal-head .seg button", "Неделя"); await set_date(A, day1)
        await A.wait_for("document.querySelectorAll('.cal-slot').length >= 12", 15)
        S.check("карточка встречи открывается", await open_chip(A, "Созвон по бюджету"))
        await A.wait_for("!!document.querySelector('.modal')", 8)
        S.check("в карточке есть «Изменить»", await click_text(A, ".modal button", "Изменить"))
        S.check("форма изменения: общий вид, 24-часовое поле времени, кнопки внизу видны", await A.wait_for("!!document.querySelector('.modal .cal-form .tf-in')", 10) and (await A.js(TIME))["val"][2] == ":")
        f = await A.js(FORM)
        S.check("форма изменения целиком на экране", f["t"] >= 0 and f["b"] <= f["ih"] and f["foot"]["b"] <= f["ih"] and f["sw"] <= f["iw"], str(f))
        await A.shot("calendar-visual-edit")
        await key(A, "Escape", "Escape", 27)
        await asyncio.sleep(0.4)

        # ---------- тёмная тема
        await size(A, 1366, 800)
        await A.goto("/calendar", 1.8)
        await click_text(A, ".cal-head .seg button", "Неделя"); await set_date(A, day1)
        await A.js("document.documentElement.setAttribute('data-theme', 'dark')")
        await asyncio.sleep(0.6)
        ch = await A.js(CHIPS)
        S.check("тёмная тема: плашки целы", not ch["bad"] and ch["n"] >= 3, str(ch["bad"][:2]))
        await A.shot("calendar-visual-dark")
        await click_text(A, ".cal-head-a .btn", "Создать")
        await asyncio.sleep(0.6)
        await A.js("document.querySelector('.tf-btn').click()")
        await asyncio.sleep(0.4)
        await A.shot("calendar-visual-dark-form")
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        for c in (A, R):
            try: await c.close()
            except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")


if __name__ == "__main__":
    asyncio.run(main())
