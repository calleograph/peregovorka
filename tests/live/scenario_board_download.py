"""Сценарий (см. README.md): меню «Скачать» на общей доске — открывается поверх редактора и реально выдаёт файлы (.drawio, PNG, SVG, PDF).
Нужен draw.io (DEV_DRAWIO_URL / :8771, см. README), как для остальных проверок доски. Регрессия: меню было обрезано полосой инструментов `overflow-x: auto` на сцене."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

HOOK = """window.__dl = []; const _c = HTMLAnchorElement.prototype.click;
HTMLAnchorElement.prototype.click = function () { if (this.download) { const n = this.download; fetch(this.href).then(r => r.blob()).then(b => window.__dl.push({name: n, size: b.size, type: b.type})); } return _c.apply(this, arguments); };"""
OPEN = """(() => { const b = [...document.querySelectorAll('.board-stage .menu > button')][0]; if (!b) return false; b.click(); return true; })()"""
POP = """(() => { const p = document.querySelector('.board-stage .menu-pop'); if (!p) return null; const r = p.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  return {visible: r.width > 0 && r.height > 0, topmost: !!top && p.contains(top), items: [...p.querySelectorAll('button')].map(x => x.textContent.trim()), inView: r.bottom <= innerHeight && r.right <= innerWidth}; })()"""


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9495)
    try:
        await A.start()
        S.check("Алиса вошла в комнату", await A.join())
        await A.js(HOOK)
        S.check('кнопка «Доска» нажата', await A.click_btn('Доска'))
        S.check("доска открыта", await A.wait_for("document.querySelector('.board-stage:not(.off) iframe.drawio-frame') !== null", 30))
        await asyncio.sleep(8)
        S.check("кнопка «Скачать» на месте", await A.js("!![...document.querySelectorAll('.board-stage .menu > button')].length"))
        await A.js(OPEN)
        await asyncio.sleep(0.6)
        pop = await A.js(POP)
        S.check("меню открылось: видно, лежит поверх редактора и не обрезано", bool(pop) and pop["visible"] and pop["topmost"] and pop["inView"], str(pop))
        S.check("в меню четыре формата", bool(pop) and len(pop["items"]) == 4, str(pop and pop["items"]))
        await A.shot("board-download-menu")
        for label, ext in (("Файл draw.io", ".drawio"), ("Картинка PNG", ".png"), ("Вектор SVG", ".svg"), ("Документ PDF", ".pdf")):
            if not await A.js(f"!!document.querySelector('.board-stage .menu-pop')"):
                await A.js(OPEN); await asyncio.sleep(0.5)
            await A.js(f"[...document.querySelectorAll('.board-stage .menu-pop button')].find(b => b.textContent.includes('{label}'))?.click()")
            ok = await A.wait_for(f"window.__dl.some(d => d.name.endsWith('{ext}') && d.size > 50)", 40)
            S.check(f"«{label}» выдаёт файл {ext}", ok, str(await A.js("window.__dl")))
        S.check("меню закрывается после выбора и по Escape", not await A.js("!!document.querySelector('.board-stage .menu-pop')"))
        await A.js(OPEN); await asyncio.sleep(0.4)
        await A.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        S.check("Escape закрывает меню", await A.wait_for("!document.querySelector('.board-stage .menu-pop')", 5))
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:2]))
    finally:
        await A.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
