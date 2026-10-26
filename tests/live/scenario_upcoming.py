"""Сценарий (см. README.md): главная страница — «Предстоящие встречи» над постоянными переговорками. Перед запуском: стенд и `python tests/live/seed_upcoming.py`.
Проверяется: встречи из календаря и временные комнаты — отдельным блоком, в каталоге постоянных их нет; статусы (идёт, скоро, запланирована; отменённые и завершённые на главной не показываются);
отсчёт обновляется сам, без перезагрузки и частых запросов (встреча через 50 с — секунды идут); полный список приглашённых по нажатию — у организатора
с адресами, у приглашённого без адресов; фильтры «Предстоящие»/«Постоянные»; компактный вид; 390 px и 1366×650 без горизонтальной прокрутки; тёмная тема;
нет ошибок страницы. Снимки — в $SMOKE_OUT/upc-*.png. Не проверяется: настоящий Outlook/Exchange (встречи созданы через API надстройки)."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

SOON = """(async (RUN) => {                                         // встреча через 50 с — тем же путём, что надстройка (токен надстройки + /outlook/meetings)
  const me = await (await fetch('/api/v1/auth/me')).json();
  const tok = (await (await fetch('/api/v1/outlook/token', {method: 'POST', headers: {'X-CSRF-Token': me.csrf_token}})).json()).token;
  const s = new Date(Date.now() + 50000), e = new Date(Date.now() + 1850000);
  const r = await fetch('/api/v1/outlook/meetings', {method: 'POST', headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok},
    body: JSON.stringify({key: 'live' + Math.random().toString(36).slice(2) + 'xxxxxxxxxxxxxxxx', subject: 'Встреча через минуту ' + RUN, start: s.toISOString(), end: e.toISOString(),
                          attendees: [{email: 'bob@corp.test', name: 'Борис Мартынов'}]})});
  return r.status; })"""
COUNT_OF = "(n) => { const c = [...document.querySelectorAll('.upc-card')].find(x => x.querySelector('.upc-title')?.textContent === n); return c ? c.querySelector('.upc-count').textContent : null; }"


async def main():
    A = S.Client("upc-a", "alice", "alice-pass", 9811)
    B = S.Client("upc-b", "bob", "bob-pass", 9812)
    try:
        await A.start(); await B.start()
        S.check("встреча через 50 с создана через API надстройки", await A.js(f"({SOON})('{S.RUN_ID}')") == 200)
        await A.goto("/", 3)
        await A.js("localStorage.setItem('pg:roomsView', 'tiles')"); await A.goto("/", 3)
        await A.js("document.querySelector('.upc-more')?.click()"); await asyncio.sleep(0.5)          # «Показать все»: проверки не зависят от числа встреч
        g = await A.js("""(() => { const up = document.querySelector('.upc-section'), perm = document.querySelector('.rooms-grid');
          const names = [...document.querySelectorAll('.upc-card .upc-title')].map(e => e.textContent), permNames = [...document.querySelectorAll('.rc-cell h2')].map(e => e.textContent);
          return {up: !!up, above: !!(up && perm && (up.compareDocumentPosition(perm) & Node.DOCUMENT_POSITION_FOLLOWING)), names, permNames,
                  states: [...document.querySelectorAll('.upc-card .upc-status')].map(e => e.textContent)}; })()""")
        S.check("блок «Предстоящие встречи» над постоянными переговорками", g["up"] and g["above"], str(g)[:300])
        S.check("временные комнаты и встречи из календаря не попадают в каталог постоянных", not set(g["names"]) & set(g["permNames"]) and "Разбор инцидента" not in g["permNames"], str(g)[:300])
        S.check("статусы различаются: идёт, скоро начнётся, запланирована; отменённых и завершённых на главной нет", {"Идёт", "Скоро начнётся", "Запланирована"} <= set(g["states"]) and not ({"Отменена", "Завершена"} & set(g["states"])), str(g["states"]))
        S.check("отменённая встреча («Встреча с поставщиком») не показана ни карточкой, ни в тексте страницы", "Встреча с поставщиком" not in g["names"] and await A.js("!document.body.innerText.includes('Встреча с поставщиком')"))
        ser = await A.js("(() => { const c = [...document.querySelectorAll('.upc-card')].find(x => x.querySelector('.upc-title').textContent === 'Еженедельный обзор проектов'); return c ? [c.querySelector('.upc-when').textContent, c.querySelector('.upc-count').textContent] : null; })()")
        S.check("серия: ближайшее занятие с датой и отсчётом («Серия · …», «Через …»)", bool(ser) and "Серия ·" in ser[0] and "Через" in ser[1], str(ser))
        t1 = await A.js(f"({COUNT_OF})('Встреча через минуту {S.RUN_ID}')"); await asyncio.sleep(3.2); t2 = await A.js(f"({COUNT_OF})('Встреча через минуту {S.RUN_ID}')")
        S.check("отсчёт идёт сам, без перезагрузки: «Начало через N сек» меняется", bool(t1 and t2 and "сек" in t1 and t1 != t2), f"{t1} → {t2}")
        # полный список приглашённых у организатора — с адресами
        await A.js("[...document.querySelectorAll('.upc-card')].find(c => c.querySelector('.upc-title').textContent === 'Планёрка отдела продаж').querySelector('.upc-people.link').click()")
        await asyncio.sleep(1.0)
        pop = await A.js("(() => { const p = document.querySelector('.upc-pop'); return p ? {inBody: p.parentElement === document.body, text: p.innerText} : null; })()")
        S.check("подсказка с полным списком приглашённых (организатор видит адреса)", bool(pop) and pop["inBody"] and "Юрист Консультант" in pop["text"] and "@" in pop["text"], str(pop)[:200])
        await A.shot("tiles-popover")
        await A.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))"); await asyncio.sleep(0.3)
        S.check("подсказка закрывается по Esc", not await A.js("!!document.querySelector('.upc-pop')"))
        # фильтры
        await A.js("[...document.querySelectorAll('.chips .chip')].find(b => b.textContent.startsWith('Постоянные')).click()"); await asyncio.sleep(0.4)
        S.check("фильтр «Постоянные»: только каталог переговорок", await A.js("!document.querySelector('.upc-section') && document.querySelectorAll('.rc-cell').length > 0"))
        await A.js("[...document.querySelectorAll('.chips .chip')].find(b => b.textContent.startsWith('Предстоящие')).click()"); await asyncio.sleep(0.4)
        S.check("фильтр «Предстоящие»: только встречи", await A.js("!!document.querySelector('.upc-section') && document.querySelectorAll('.rc-cell').length === 0"))
        await A.js("[...document.querySelectorAll('.chips .chip')].find(b => b.textContent.startsWith('Все')).click()")
        await A.js("[...document.querySelectorAll('.viewseg button')].find(b => /Список/.test(b.textContent)).click()"); await asyncio.sleep(0.6)
        S.check("компактный вид: встречи строками", await A.js("document.querySelectorAll('.upc-row').length > 0 && document.querySelectorAll('.upc-card').length === 0"))
        for w, h in ((1366, 650), (390, 844)):
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600); await asyncio.sleep(1.0)
            sw = await A.js("document.documentElement.scrollWidth")
            S.check(f"{w}×{h}: без горизонтальной прокрутки", sw <= w, f"scrollWidth={sw}")
            await A.shot(f"compact-{w}x{h}")
        await A.js("document.documentElement.setAttribute('data-theme', 'dark')"); await asyncio.sleep(0.4)
        bg = await A.js("[getComputedStyle(document.querySelector('.upc-row')).backgroundColor, getComputedStyle(document.body).backgroundColor]")
        S.check("тёмная тема: карточки встреч в цветах темы", bool(bg) and bg[0] != "rgb(255, 255, 255)", str(bg))
        await A.shot("compact-dark")
        await A.js("document.documentElement.removeAttribute('data-theme'); localStorage.setItem('pg:roomsView', 'tiles')")
        # приглашённый: видит встречу и имена, но не адреса
        await B.goto("/", 3)
        await B.js("document.querySelector('.upc-more')?.click()"); await asyncio.sleep(0.5)
        b = await B.js("(() => ({names: [...document.querySelectorAll('.upc-title, .upc-row-name')].map(e => e.textContent)}))()")
        S.check("приглашённый видит встречи, куда его позвали, но не чужие", "Планёрка отдела продаж" in b["names"] and "Встреча с поставщиком" not in b["names"], str(b)[:200])
        await B.js("[...document.querySelectorAll('.upc-card, .upc-row')].find(c => (c.querySelector('.upc-title, .upc-row-name')).textContent === 'Планёрка отдела продаж').querySelector('.upc-people.link').scrollIntoView({block: 'end', behavior: 'instant'})")
        await asyncio.sleep(0.5)          # человек нажимает на то, что видит: кнопку сначала докручивают до видимого места, у нижнего края окна
        await B.js("[...document.querySelectorAll('.upc-card, .upc-row')].find(c => (c.querySelector('.upc-title, .upc-row-name')).textContent === 'Планёрка отдела продаж').querySelector('.upc-people.link').click()")
        await asyncio.sleep(1.0)
        pt = await B.js("document.querySelector('.upc-pop')?.innerText || ''")
        S.check("у приглашённого в списке имена без адресов почты", "Дмитрий Соколов" in pt and "@" not in pt, pt[:160])
        box = await B.js("(() => { const b = document.querySelector('.upc-pop').getBoundingClientRect(); return {top: Math.round(b.top), bottom: Math.round(b.bottom), ih: innerHeight}; })()")
        S.check("подсказка целиком в окне (у нижнего края раскрывается вверх)", box["top"] >= 0 and box["bottom"] <= box["ih"], str(box))
        await B.shot("invitee-popover")
        for c in (A, B):
            errs = await c.js("window.__errs")
            S.check(f"{c.label}: ошибок страницы нет", not errs, str(errs)[:300])
    finally:
        await A.close(); await B.close()
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")


asyncio.run(main())
