"""Сценарий (см. README.md): кнопка закрытия плавающих окон (components/FloatClose) — окно чатов и общая доска в звонке.
Проверяется геометрия (правый верхний угол всего окна, одинаковые отступы, нет наложения на соседние элементы), состояния наведения и фокуса,
перетаскивание окна чатов за заголовок (кнопки его не начинают), закрытие доски во время звонка (звонок продолжается), светлая и тёмная темы, малые окна.
Нужен draw.io (DEV_DRAWIO_URL / :8771), LiveKit и стенд, как для остальных проверок доски."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

# прямоугольник кнопки относительно окна и пересечения с остальными интерактивными элементами окна
GEOM = """((sel) => { const w = document.querySelector(sel); const c = w && w.querySelector(':scope > .fw-close'); if (!c) return null;
  const R = w.getBoundingClientRect(), r = c.getBoundingClientRect();
  const hit = [...w.querySelectorAll('button, a, input, select, textarea, [role=tab], h2')].filter(e => e !== c && !e.closest('.fw-close') && e.getClientRects().length && (() => { const q = e.getBoundingClientRect(); const k = (e.closest('.board-head') || w).getBoundingClientRect();          /* полоса заголовка доски прокручивается: скрытая часть не считается */
    const qr = Math.min(q.right, k.right), ql = Math.max(q.left, k.left);
    return q.width > 0 && q.height > 0 && ql < qr && !(qr <= r.left || ql >= r.right || q.bottom <= r.top || q.top >= r.bottom); })()).map(e => e.getAttribute('aria-label') || e.textContent.trim().slice(0, 20));
  return {top: Math.round(r.top - R.top), right: Math.round(R.right - r.right), w: Math.round(r.width), h: Math.round(r.height), hit}; })"""
STYLE = "(() => { const c = document.querySelector('%s'); const s = getComputedStyle(c); return {bg: s.backgroundColor, color: s.color, outline: s.outlineStyle + ' ' + s.outlineWidth}; })()"


async def geometry(A, sel, name):
    g = await A.js(f"{GEOM}('{sel}')")
    ok = bool(g) and g["top"] == g["right"] and 6 <= g["top"] <= 12 and g["w"] == g["h"] == 32 and not g["hit"]
    S.check(f"{name}: крестик в правом верхнем углу окна, отступы равны, ничего не перекрывает", ok, str(g))


async def main():
    A = S.Client("float", "alice", "alice-pass", 9496)
    try:
        await A.start()
        S.check("Алиса вошла в комнату", await A.join())
        # ---------- общая доска в звонке
        S.check('кнопка «Доска» нажата', await A.click_btn('Доска'))
        S.check("доска открыта", await A.wait_for("document.querySelector('.board-stage:not(.off) iframe.drawio-frame') !== null", 30))
        await asyncio.sleep(6)
        await geometry(A, ".board-stage", "доска")
        frame = await A.js("(() => { const f = document.querySelector('.board-stage iframe').getBoundingClientRect(), c = document.querySelector('.board-stage .fw-close').getBoundingClientRect(); return c.bottom <= f.top + 1 || c.top >= f.bottom || c.right <= f.left || c.left >= f.right; })()")
        S.check("крестик доски не лежит поверх редактора draw.io", frame)
        base = await A.js(STYLE % ".board-stage .fw-close")
        c = await A.js("(() => { const r = document.querySelector('.board-stage .fw-close').getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=c["x"], y=c["y"])
        await asyncio.sleep(0.4)
        hov = await A.js(STYLE % ".board-stage .fw-close")
        S.check("наведение меняет вид кнопки", hov["bg"] != base["bg"], f"{base['bg']} → {hov['bg']}")
        await A.js("[...document.querySelectorAll('.board-stage .board-head button')].pop().focus()")
        await A.send("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9)
        await A.send("Input.dispatchKeyEvent", type="keyUp", key="Tab", code="Tab", windowsVirtualKeyCode=9)
        foc = await A.js("(() => { const c = document.querySelector('.board-stage .fw-close'); return {active: document.activeElement === c, outline: getComputedStyle(c).outlineStyle}; })()")
        S.check("с клавиатуры (Tab) фокус доходит до крестика и виден рамкой", foc["active"] and foc["outline"] != "none", str(foc))
        await A.send("Emulation.setDeviceMetricsOverride", width=900, height=600, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(0.6)
        await geometry(A, ".board-stage", "доска в малом окне 900×600")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False)
        await A.shot("float-close-board")
        await A.js("document.querySelector('.board-stage .fw-close').click()")
        S.check("крестик скрывает доску", await A.wait_for("!!document.querySelector('.board-stage.off')", 8))
        S.check("звонок продолжается после закрытия доски", await A.js("!!document.querySelector('.rbar') && !document.querySelector('.field-err')"))
        # ---------- окно чатов
        await A.js("document.querySelector('.chat-nav')?.click()")
        S.check("окно чатов открыто", await A.wait_for("!!document.querySelector('.chat-win')", 10))
        await asyncio.sleep(0.8)
        await geometry(A, ".chat-win", "чаты")
        # перетаскивание: за заголовок — окно движется; нажатие на крестик окно не двигает
        r0 = await A.js("(() => { const r = document.querySelector('.chat-win').getBoundingClientRect(); return {x: r.x, y: r.y}; })()")
        h = await A.js("(() => { const r = document.querySelector('.chat-side-head h2').getBoundingClientRect(); return {x: r.x + r.width + 40, y: r.y + r.height / 2}; })()")
        await A.send("Input.dispatchMouseEvent", type="mousePressed", x=h["x"], y=h["y"], button="left", clickCount=1)
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=h["x"] + 10, y=h["y"] - 20, button="left")
        await A.send("Input.dispatchMouseEvent", type="mouseReleased", x=h["x"] + 10, y=h["y"] - 20, button="left", clickCount=1)
        r1 = await A.js("(() => { const r = document.querySelector('.chat-win').getBoundingClientRect(); return {x: r.x, y: r.y}; })()")
        S.check("окно чатов перетаскивается за заголовок", abs(r1["x"] - (r0["x"] + 10)) <= 2 and abs(r1["y"] - (r0["y"] - 20)) <= 2, f"{r0} → {r1}")
        c = await A.js("(() => { const r = document.querySelector('.chat-win .fw-close').getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()")
        await A.send("Input.dispatchMouseEvent", type="mousePressed", x=c["x"], y=c["y"], button="left", clickCount=1)
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=c["x"] + 0, y=c["y"] + 40, button="left")
        r2 = await A.js("(() => { const r = document.querySelector('.chat-win').getBoundingClientRect(); return {x: r.x, y: r.y}; })()")
        await A.send("Input.dispatchMouseEvent", type="mouseReleased", x=c["x"] + 0, y=c["y"] + 40, button="left", clickCount=1)
        S.check("нажатие на крестик не начинает перетаскивание окна", r2 == r1, f"{r1} → {r2}")
        await A.js("localStorage.removeItem('pg:chat:rect')")
        for theme in ("light", "dark"):
            await A.js(f"document.documentElement.dataset.theme = '{theme}'")
            await asyncio.sleep(0.3)
            st = await A.js(STYLE % ".chat-win .fw-close")
            S.check(f"чаты, тема «{theme}»: крестик виден (цвет отличается от фона окна)", st["color"] != await A.js("getComputedStyle(document.querySelector('.chat-win')).backgroundColor"), str(st))
        for w, h in ((800, 560), (390, 780)):
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 500)
            await asyncio.sleep(0.6)
            await geometry(A, ".chat-win", f"чаты в окне {w}×{h}")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(0.5)
        await A.js("document.querySelector('.chat-win .fw-close').click()")
        S.check("крестик закрывает окно чатов", await A.wait_for("!document.querySelector('.chat-win')", 6))
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:2]))
    finally:
        await A.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
