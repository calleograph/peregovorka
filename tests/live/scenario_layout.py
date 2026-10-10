"""Сценарий (см. README.md): компактная комната 0.12.0 — площадь, панель управления в один ряд, правая панель, «Развернуть доску», телефон без прокрутки.
Измеряется геометрия на 1366×768, 1920×1080 и 390×844: ширина сцены, высота страницы (не должна прокручиваться), число рядов кнопок, доступность кнопок."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/arch"
GEO = """(()=>{const r=e=>{if(!e)return null;const x=e.getBoundingClientRect();return {x:Math.round(x.left),y:Math.round(x.top),w:Math.round(x.width),h:Math.round(x.height)}};
 const btns=[...document.querySelectorAll('.rbar .rbtn')].map(b=>r(b));const rows=new Set(btns.map(b=>b.y)).size;
 return {iw:innerWidth,ih:innerHeight,sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight,view:r(document.querySelector('.st-view')),bar:r(document.querySelector('.rbar')),
  panel:r(document.querySelector('.transcript:not(.collapsed)')),collapsed:!!document.querySelector('.transcript.collapsed'),rows,btnSize:btns[0]&&btns[0].w,nbtn:btns.length,
  over:btns.filter(b=>b.x+b.w>innerWidth||b.x<0).length,diag:[...document.querySelectorAll('button')].some(b=>/Диагностика/.test(b.textContent)),devdetails:!!document.querySelector('details.devices')}})()"""


async def geo(c):
    return await c.js(GEO)


async def vp(c, w, h, mobile=False):
    await c.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=mobile)
    await asyncio.sleep(1.2)


async def main():
    A = S.Client("A", "root", "root-pass", 9621)
    B = S.Client("B", "alice", "alice-pass", 9622)
    try:
        for c in (A, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.st-cell.type-camera').length>=2", 40) for c in (A, B)])
        for w, h in ((1366, 768), (1920, 1080)):
            await vp(A, w, h)
            g = await geo(A)
            S.check(f"{w}×{h}: сцена занимает почти всю ширину (≥ {w - 400}px) при открытой панели 240–340px", g["view"]["w"] >= w - 400 and 240 <= g["panel"]["w"] <= 340, str(g["view"]) + str(g["panel"]))
            S.check(f"{w}×{h}: страница не прокручивается", g["sh"] <= g["ih"] + 1 and g["sw"] <= g["iw"] + 1, f"{g['sh']}/{g['ih']}")
            S.check(f"{w}×{h}: кнопки управления в один ряд, 40px", g["rows"] == 1 and g["btnSize"] == 40, f"рядов {g['rows']}, размер {g['btnSize']}, кнопок {g['nbtn']}")
            S.check(f"{w}×{h}: диагностики в комнате нет, устройства не занимают места", not g["diag"] and not g["devdetails"])
            await A.shot(f"layout-{w}")
        # правая панель сворачивается без пустой полосы
        await vp(A, 1366, 768)
        before = (await geo(A))["view"]["w"]
        await A.js("document.querySelector('button[aria-label=\"Свернуть панель\"]').click()")
        await asyncio.sleep(0.8)
        g = await geo(A)
        S.check("панель свёрнута: сцена получила всю ширину (разница ≥ 250px, справа нет пустой полосы)", g["view"]["w"] - before >= 250 and g["iw"] - (g["view"]["x"] + g["view"]["w"]) <= 24, f"{before}→{g['view']['w']}")
        await A.shot("layout-collapsed")
        await A.js("document.querySelector('.transcript.collapsed .rail-btn').click()"); await asyncio.sleep(0.6)
        # устройства — всплывающим окном рядом с микрофоном
        await A.js("document.querySelector('.rbar .rbtn-more').click()"); await asyncio.sleep(0.8)
        S.check("устройства: компактное окно рядом с кнопкой микрофона, а не отдельная область", await A.js("!!document.querySelector('.dev-pop select')"))
        await A.shot("layout-devices")
        await A.js("document.querySelector('.dev-pop .icon-btn').click()")
        # меню «Ещё» и «Вид»
        await A.js("document.querySelector('button[aria-label=\"Ещё\"]').click()"); await asyncio.sleep(0.4)
        items = await A.js("[...document.querySelectorAll('.ctx-menu .ctx-item')].map(x=>x.textContent.trim())")
        S.check("«Ещё»: шумоподавление, транскрибация, выключить у всех", any("Шумоподавление" in x for x in items) and any("транскрибац" in x.lower() for x in items) and any("Выключить у всех" in x for x in items), str(items))
        await A.js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        # доска: открыть → «Развернуть»
        S.check("A: «Доска» нажата", await A.click_btn("Доска"))
        await A.wait_for("!!document.querySelector('.st-board.on iframe')", 30)
        await B.wait_for("document.querySelectorAll('.st-cell.type-camera').length>=2", 10)
        await A.js("document.querySelector('.st-board .board-head .btn.primary').click()"); await asyncio.sleep(1.2)
        g = await A.js("(()=>{const b=document.querySelector('.st-board').getBoundingClientRect();const f=document.querySelector('.st-board iframe').getBoundingClientRect();const v=document.querySelector('.st-view').getBoundingClientRect();return {bw:Math.round(b.width),bh:Math.round(b.height),fh:Math.round(f.height),vw:Math.round(v.width),vh:Math.round(v.height),panel:getComputedStyle(document.querySelector('.transcript')).display,focus:document.querySelector('.room-wrap').classList.contains('board-focus'),iw:innerWidth,ih:innerHeight}})()")
        S.check("«Развернуть»: доска ≥ 95% ширины и ≥ 75% высоты окна, правая панель скрыта", g["focus"] and g["bw"] >= g["iw"] * 0.95 and g["bh"] >= g["ih"] * 0.75 and g["panel"] == "none", str(g))
        S.check("«Развернуть»: сам редактор получает ≥ 60% высоты окна", g["fh"] >= g["ih"] * 0.6, str(g))
        await A.shot("layout-board-focus")
        okB = await B.js("!document.querySelector('.room-wrap').classList.contains('board-focus') && document.querySelectorAll('.st-cell.type-camera').length>=2")
        S.check("у B раскладка не изменилась (развёрнутая доска — личное действие)", okB)
        st = await A.state()
        S.check("в режиме доски звук не прерывался (аудиоэлементы живы)", sum(1 for a in st["audio"] if a["live"]) >= 1, str(st["audio"]))
        await A.js("[...document.querySelectorAll('.st-board .board-head .btn')].find(b=>/Вернуться к встрече/.test(b.textContent)).click()"); await asyncio.sleep(1)
        g = await geo(A)
        S.check("«Вернуться к встрече»: панель и шапка снова на месте", not await A.js("document.querySelector('.room-wrap').classList.contains('board-focus')") and g["panel"] is not None)
        # телефон
        # телефон: свежий вход (как у нового пользователя — панель по умолчанию свёрнута)
        await A.js("localStorage.removeItem('room.tcollapsed')")
        await vp(A, 390, 844, mobile=True)
        await A.send("Page.reload"); await asyncio.sleep(3)
        await A.js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        S.check("телефон: после перезагрузки снова в комнате", await A.join(navigate=False) and await A.wait_for("document.querySelectorAll('.st-cell').length>=2", 30))
        await asyncio.sleep(2)
        g = await geo(A)
        S.check("телефон: правая панель по умолчанию свёрнута, сцена видна", g["collapsed"] and g["panel"] is None, str(g["panel"]))
        S.check("телефон 390×844: всё в одном окне — страница не прокручивается", g["sh"] <= g["ih"] + 1 and g["sw"] <= g["iw"] + 1, f"{g['sh']}/{g['ih']} {g['sw']}/{g['iw']}")
        S.check("телефон: кнопки в один ряд и все на экране", g["rows"] == 1 and g["over"] == 0, f"рядов {g['rows']}, за краем {g['over']}, кнопок {g['nbtn']}")
        S.check("телефон: сцена занимает не менее 55% высоты окна", g["view"]["h"] >= g["ih"] * 0.55, str(g["view"]))
        await A.shot("layout-mobile")
        await A.click_btn("Чат"); await asyncio.sleep(0.8)
        g = await geo(A)
        S.check("телефон: «Чат» открывает панель поверх сцены на весь экран", g["panel"] and g["panel"]["h"] >= g["ih"] * 0.8, str(g["panel"]))
        await A.shot("layout-mobile-chat")
        # диагностика встречи — у администратора в списке встреч: кнопка «Д», полноэкранное окно, Esc
        D = S.Client("D", "root", "root-pass", 9623)
        try:
            await D.start(); await D.goto("/admin?tab=meetings", 3.5)
            S.check("администрирование → «Встречи»: у идущей встречи есть круглая кнопка «Д»", await D.wait_for("!!document.querySelector('.diag-btn')", 20))
            await D.js("document.querySelector('.diag-btn').click()")
            S.check("«Д» открывает диагностику полноэкранным окном поверх страницы (без новой вкладки)", await D.wait_for("(()=>{const m=document.querySelector('.diag-full');if(!m)return false;const r=m.getBoundingClientRect();return r.width>=innerWidth-20&&r.height>=innerHeight-2&&m.innerText.includes('Качество связи')})()", 15))
            await D.shot("diag-modal")
            await D.send("Input.dispatchKeyEvent", type="rawKeyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
            await D.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
            S.check("Esc закрывает диагностику", await D.wait_for("!document.querySelector('.diag-full')", 5))
        finally:
            await D.close()
        errs = {c.label: [e for e in (await c.state())["errs"] if "favicon" not in e] for c in (A, B)}
        S.check("ошибок страницы нет", not any(errs.values()), json.dumps(errs, ensure_ascii=False)[:300])
    finally:
        for c in (A, B): await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
