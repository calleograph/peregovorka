"""Сценарий (см. README.md): список зрителей презентации на СИНТЕТИЧЕСКИХ данных — тысяча «людей» без единого WebRTC-соединения (dev-маршрут /__dev/audience).
Проверяет: в DOM только видимые строки (виртуализация), плитки не создаются для зрителей, поиск, прокрутка, выдача и отзыв слова переносят человека между
сценой и списком, добавление зрителей не вешает страницу. Это НЕ испытание настоящей большой аудитории."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

N = int(os.environ.get("AUDIENCE_N", "1000"))


async def main():
    A = S.Client("aud", "root", "root-pass", 9531)
    try:
        await A.start()
        await A.goto(f"/__dev/audience?n={N}", 2.5)
        S.check("страница списка загрузилась", await A.wait_for("!!document.querySelector('.aud-panel')", 20))
        info = await A.js("document.getElementById('demo-info')?.textContent")
        print(info)
        rows = await A.js("document.querySelectorAll('.aud-row').length")
        total = await A.js("Number(document.querySelector('.aud-list ul')?.dataset.total)")
        S.check(f"в списке {N - 1} зрителей, а в DOM строк: {rows} (виртуализация)", total == N - 1 and 5 <= rows <= 40, f"total={total} rows={rows}")
        S.check("плитка на сцене одна (руководитель), зрители плиток не получили", await A.js("document.querySelectorAll('#demo-stage .tile').length") == 1)
        dom = await A.js("document.getElementsByTagName('*').length")
        S.check(f"всего элементов на странице не больше 600 (при {N} людях)", dom < 600, str(dom))
        head = await A.js("document.querySelector('.aud-head b')?.textContent")
        S.check("заголовок показывает число зрителей", bool(head) and str(N - 1)[-2:] in head.replace(" ", "").replace(" ", ""), head or "")
        first = await A.js("document.querySelector('.aud-row .aud-name')?.textContent")
        S.check("поднятые руки — сверху списка", bool(await A.js("!!document.querySelector('.aud-row .aud-hand')")), first or "")
        # прокрутка в середину
        await A.js("document.querySelector('.aud-list').scrollTop = 40 * 500")
        await asyncio.sleep(0.4)
        rows2 = await A.js("document.querySelectorAll('.aud-row').length")
        mid = await A.js("[...document.querySelectorAll('.aud-row .aud-name')].map(x=>x.textContent)")
        S.check("после прокрутки строки сменились, их по-прежнему немного", 5 <= rows2 <= 40 and mid[0] != first, f"{rows2} {mid[:2]}")
        # поиск
        await A.js("(()=>{const i=document.querySelector('.aud-search');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'Сидорова Вера');i.dispatchEvent(new Event('input',{bubbles:true}))})()")
        await asyncio.sleep(0.6)
        found = await A.js("Number(document.querySelector('.aud-list ul')?.dataset.total)")
        names = await A.js("[...document.querySelectorAll('.aud-row .aud-name')].map(x=>x.textContent).slice(0,3)")
        S.check("поиск по имени сужает список", 0 < found < N // 5 and all("Сидорова" in n and "Вера" in n for n in names), f"{found} {names}")
        await A.js("(()=>{const i=document.querySelector('.aud-search');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'');i.dispatchEvent(new Event('input',{bubbles:true}))})()")
        await asyncio.sleep(0.5)
        # выдача слова: человек уходит из списка на сцену, потом возвращается
        before = await A.js("Number(document.querySelector('.aud-list ul')?.dataset.total)")
        t = await A.js("(async()=>{const t0=performance.now();window.__demo.grant('u-5');await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return performance.now()-t0})()")
        stage = await A.js("document.querySelectorAll('#demo-stage .tile').length")
        after = await A.js("Number(document.querySelector('.aud-list ul')?.dataset.total)")
        S.check("слово: зритель стал выступающим (плитка на сцене, из списка ушёл)", stage == 2 and after == before - 1, f"stage={stage} {before}->{after}, {t:.0f} мс")
        S.check("перерисовка при выдаче слова быстрее 250 мс", t < 250, f"{t:.0f} мс")
        await A.js("window.__demo.revoke('u-5')"); await asyncio.sleep(0.4)
        stage = await A.js("document.querySelectorAll('#demo-stage .tile').length")
        back = await A.js("Number(document.querySelector('.aud-list ul')?.dataset.total)")
        S.check("отзыв: человек вернулся в список зрителей, плитки нет", stage == 1 and back == before, f"stage={stage} total={back}")
        # приход ещё 300 зрителей одним пакетом
        t = await A.js("(async()=>{const t0=performance.now();window.__demo.join(300);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return performance.now()-t0})()")
        total2 = await A.js("Number(document.querySelector('.aud-list ul')?.dataset.total)")
        rows3 = await A.js("document.querySelectorAll('.aud-row').length")
        S.check("+300 зрителей: список обновился, в DOM по-прежнему немного строк, быстрее 400 мс", total2 == before + 300 and rows3 <= 40 and t < 400, f"{total2} rows={rows3} {t:.0f} мс")
        await A.shot("audience-1000")
        errs = (await A.state())["errs"]
        S.check("ошибок страницы нет", not errs, str(errs[:2]))
    finally:
        await A.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
