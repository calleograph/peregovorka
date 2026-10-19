"""Сценарий (см. README.md): вложенные меню «Ещё» → «Качество видео ▸» / «Шумоподавление ▸» (как в контекстных меню Windows).

Проверяется настоящими событиями ввода: подменю открывается наведением справа от строки; выбор применяется сразу и закрывает меню; ✓ у текущего значения; стрелки →/←/↑/↓; Escape закрывает сначала
подменю, потом меню; клик вне меню закрывает; на узком экране (390 px, касание) подменю раскрывается внутри меню и не выходит за экран.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/sales"
CENTER = "(n => { const b = [...document.querySelectorAll('.ctx-menu [role=menuitem]')].find(x => x.textContent.includes(n)); const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })"


async def key(c, k, code, vk):
    for t in ("keyDown", "keyUp"):
        await c.send("Input.dispatchKeyEvent", type=t, key=k, code=code, windowsVirtualKeyCode=vk)
    await asyncio.sleep(0.25)


async def sub_open(c):
    return bool(await c.js("!!document.querySelector('.ctx-sub')"))


async def menu_open(c):
    return bool(await c.js("!!document.querySelector('.ctx-menu')"))


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9497)
    try:
        await A.start(); S.check("alice: вошла", await A.join())
        await A.wait_for("document.querySelectorAll('.tile').length>=1", 30); await asyncio.sleep(3)

        # --- мышь: наведение открывает подменю справа
        await A.click_btn("Ещё"); await asyncio.sleep(0.4)
        x, y = await A.js(f"({CENTER})('Качество видео')")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y); await asyncio.sleep(0.4)
        S.check("наведение на «Качество видео ▸» открывает подменю", await sub_open(A))
        geo = await A.js("(() => { const m = document.querySelector('.ctx-menu').getBoundingClientRect(), s = document.querySelector('.ctx-sub').getBoundingClientRect(); return {mr: m.right, sl: s.left, sr: s.right, w: innerWidth}; })()")
        S.check("подменю справа от меню и в пределах экрана", geo["sl"] >= geo["mr"] - 6 and geo["sr"] <= geo["w"], str(geo))
        items = await A.js("[...document.querySelectorAll('.ctx-sub [role=menuitemradio]')].map(x => x.textContent.trim())")
        S.check("в подменю «Автоматически» с ✓ и уровни", items[:1] == ["✓Автоматически"] and "Высокое" in items and "Среднее" in items and "Низкое" in items, str(items))
        await A.shot("submenu-desktop")
        # переход на «Шумоподавление» меняет открытое подменю
        x, y = await A.js(f"({CENTER})('Шумоподавление')")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y); await asyncio.sleep(0.4)
        S.check("наведение на «Шумоподавление ▸» показывает его подменю вместо прежнего", await A.js("[...document.querySelectorAll('.ctx-sub [role=menuitemradio]')].some(x => x.textContent.includes('Усиленное'))"))
        # --- Escape: сначала подменю, потом меню
        await key(A, "Escape", "Escape", 27)
        S.check("Escape закрывает подменю, меню остаётся", (not await sub_open(A)) and await menu_open(A))
        await key(A, "Escape", "Escape", 27)
        S.check("второй Escape закрывает меню", not await menu_open(A))

        # --- клавиатура: → открывает, ↓ выбирает, Enter применяет
        await A.click_btn("Ещё"); await asyncio.sleep(0.4)
        for _ in range(3): await key(A, "ArrowDown", "ArrowDown", 40)
        focused = await A.js("document.activeElement?.textContent || ''")
        await key(A, "ArrowRight", "ArrowRight", 39)
        S.check(f"→ на строке со стрелкой открывает подменю и переводит в него фокус ({focused.strip()[:20]})", await sub_open(A) and await A.js("!!document.activeElement.closest('.ctx-sub')"))
        await key(A, "ArrowLeft", "ArrowLeft", 37)
        S.check("← возвращает в меню", (not await sub_open(A)) and await menu_open(A))
        await key(A, "Escape", "Escape", 27)

        # --- выбор применяется сразу и закрывает меню; ✓ переезжает
        await A.click_btn("Ещё"); await asyncio.sleep(0.4)
        x, y = await A.js(f"({CENTER})('Качество видео')")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y); await asyncio.sleep(0.4)
        x, y = await A.js("(() => { const b = [...document.querySelectorAll('.ctx-sub [role=menuitemradio]')].find(x => x.textContent.includes('Среднее')); const r = b.getBoundingClientRect(); return [r.left + 20, r.top + r.height / 2]; })()")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for t in ("mousePressed", "mouseReleased"):
            await A.send("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
        await asyncio.sleep(0.8)
        S.check("клик по «Среднее» закрыл меню", not await menu_open(A))
        badge = await A.js("[...document.querySelectorAll('.room-head .badge')].map(b => b.textContent.trim())")
        S.check("настройка применена сразу (индикатор «Видео: среднее»)", any("Видео: среднее" in b for b in badge), str(badge))
        await A.click_btn("Ещё"); await asyncio.sleep(0.4)
        x, y = await A.js(f"({CENTER})('Качество видео')")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y); await asyncio.sleep(0.4)
        S.check("✓ стоит у «Среднее»", await A.js("document.querySelector('.ctx-sub [aria-checked=true]')?.textContent.includes('Среднее')"))
        # --- клик вне меню
        await A.send("Input.dispatchMouseEvent", type="mousePressed", x=40, y=300, button="left", clickCount=1)
        await A.send("Input.dispatchMouseEvent", type="mouseReleased", x=40, y=300, button="left", clickCount=1); await asyncio.sleep(0.4)
        S.check("клик вне меню закрывает его", not await menu_open(A))

        # --- телефон: узкий экран, касание
        await A.send("Emulation.setDeviceMetricsOverride", width=390, height=760, deviceScaleFactor=2, mobile=True); await asyncio.sleep(1.5)
        S.check("мобильная панель: кнопка «Ещё» есть", await A.click_btn("Ещё"))
        await asyncio.sleep(0.5)
        if not await menu_open(A):   # на телефоне «Ещё» может быть внутри другой кнопки
            await A.js("document.querySelector('button[aria-label=\"Ещё\"]')?.click()"); await asyncio.sleep(0.5)
        r = await A.js(f"({CENTER})('Качество видео')")
        await A.send("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": r[0], "y": r[1]}])
        await A.send("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[]); await asyncio.sleep(0.5)
        S.check("касание раскрывает подменю внутри меню", await A.js("!!document.querySelector('.ctx-sub.inline')"))
        geo = await A.js("(() => { const m = document.querySelector('.ctx-menu').getBoundingClientRect(); return {l: m.left, r: m.right, t: m.top, b: m.bottom, w: innerWidth, h: innerHeight}; })()")
        S.check("меню целиком в пределах экрана 390 px", geo["l"] >= 0 and geo["r"] <= geo["w"] and geo["t"] >= 0 and geo["b"] <= geo["h"], str(geo))
        await A.shot("submenu-mobile")
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        await A.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
