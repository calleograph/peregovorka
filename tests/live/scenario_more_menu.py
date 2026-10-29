"""Сценарий (см. README.md): меню «Ещё» стоит на месте и компактно; плавающий индикатор подключения не сдвигает раскладку.

Настоящие события ввода в двух браузерах с настоящим LiveKit. Меню «Ещё» открыто над кнопкой; пока оно открыто, происходят «фоновые» события конференции: прокрутка,
потеря фокуса окна, изменение размера, выход/вход участника, включение/выключение его микрофона. Меню не должно закрыться и сдвинуться; закрывают его только Escape,
нажатие вне меню, повторное нажатие на «Ещё» или выбор пункта. Индикатор подключения: при обрыве связи он плавающий (панель управления не двигается), по нажатию
показывает подробности, после восстановления исчезает.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/sales"
MENU = "(() => { const m = document.querySelector('.ctx-menu'); if (!m) return null; const r = m.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: innerWidth, h: innerHeight, sub: !!document.querySelector('.ctx-sub'), scroll: m.classList.contains('scroll')}; })()"
MORE = "(() => { const b = document.querySelector('button[aria-label=\"Ещё\"]'); const r = b.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom, cx: r.left + r.width / 2, cy: r.top + r.height / 2}; })()"
RBAR = "(() => { const r = document.querySelector('.controls.rbar').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom].map(Math.round); })()"


def same(a, b, eps=0.6):
    return a and b and all(abs(a[k] - b[k]) <= eps for k in ("l", "t", "r", "b"))


async def mouse(c, t, x, y, **kw):
    await c.send("Input.dispatchMouseEvent", type=t, x=x, y=y, **kw)


async def click_at(c, x, y):
    await mouse(c, "mouseMoved", x, y)
    for t in ("mousePressed", "mouseReleased"):
        await mouse(c, t, x, y, button="left", clickCount=1)


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9511)
    B = S.Client("bob", "bob", "bob-pass", 9512)
    try:
        for c in (A, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        ok = await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        S.check("оба в комнате", all(ok), str([await c.js("document.querySelectorAll('.tile').length") for c in (A, B)])); await asyncio.sleep(2)

        # ------------------------------------------------------------ меню «Ещё»: открытие и размеры
        btn = await A.js(MORE)
        await click_at(A, btn["cx"], btn["cy"]); await asyncio.sleep(0.5)
        m0 = await A.js(MENU)
        S.check("«Ещё» открывает меню", m0 is not None)
        S.check("меню над кнопкой: нижняя кромка выше кнопки и не перекрывает её", m0["b"] <= btn["t"] + 0.5, f"меню до {m0['b']:.0f}, кнопка с {btn['t']:.0f}")
        S.check("меню целиком в окне", m0["l"] >= 0 and m0["t"] >= 0 and m0["r"] <= m0["w"] and m0["b"] <= m0["h"], str(m0))
        rows = await A.js("[...document.querySelectorAll('.ctx-menu > div > .ctx-item')].map(x => Math.round(x.getBoundingClientRect().height))")
        S.check("пункты плотные, но не тоньше 26 px (область нажатия)", rows and 26 <= min(rows) and max(rows) <= 36, str(rows))
        S.check("ширина меню не больше 300 px", (m0["r"] - m0["l"]) <= 300.5, f"{m0['r'] - m0['l']:.0f} px")
        await A.shot("more-menu")
        await asyncio.sleep(1.2)
        S.check("в покое меню не двигается", same(m0, await A.js(MENU)))

        # ------------------------------------------------------------ наведение на пункты и подменю: меню не прыгает
        n = await A.js("document.querySelectorAll('.ctx-menu > div > .ctx-item').length")
        moved = []
        for i in range(n):
            xy = await A.js(f"(() => {{ const r = document.querySelectorAll('.ctx-menu > div > .ctx-item')[{i}].getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
            await mouse(A, "mouseMoved", xy[0], xy[1]); await asyncio.sleep(0.25)
            if not same(m0, await A.js(MENU)): moved.append(i)
        S.check(f"при наведении на все {n} пунктов меню не сдвигается", not moved, f"сдвиг на пунктах {moved}")
        sub = await A.js("(() => { const s = document.querySelector('.ctx-sub'); if (!s) return null; const r = s.getBoundingClientRect(); return {t: r.top, b: r.bottom, l: r.left, r: r.right, h: innerHeight, w: innerWidth}; })()")
        S.check("открытое подменю целиком в окне", sub is None or (sub["t"] >= 0 and sub["b"] <= sub["h"] and sub["r"] <= sub["w"]), str(sub))

        # ------------------------------------------------------------ фоновые события не закрывают и не двигают
        await A.js("window.dispatchEvent(new Event('blur')); document.body.dispatchEvent(new Event('scroll')); document.querySelectorAll('*').forEach((e, i) => { if (i % 40 === 0) e.dispatchEvent(new Event('scroll')); }); window.dispatchEvent(new Event('resize'))")
        await asyncio.sleep(0.6)
        S.check("прокрутка, потеря фокуса и resize не закрывают меню", await A.js(MENU) is not None)
        await A.js("(() => { const t = document.querySelector('.transcript-list, .tr-list, .transcript'); if (t) { t.scrollTop = 0; t.dispatchEvent(new Event('scroll')); } })()")
        await B.click_btn("Микрофон"); await asyncio.sleep(0.8); await B.click_btn("Микрофон"); await asyncio.sleep(0.8)
        S.check("включение/выключение микрофона участником не закрывает меню", await A.js(MENU) is not None)
        await B.goto("/", 1.0); await asyncio.sleep(1.5)
        S.check("выход участника из комнаты не закрывает меню", await A.js(MENU) is not None)
        S.check("и не сдвигает его", same(m0, await A.js(MENU)) or (await A.js(MENU))["b"] <= btn["t"] + 0.5, str(await A.js(MENU)))
        # настоящее изменение размера окна (как при показе экрана): меню остаётся открытым и в пределах окна
        await A.send("Emulation.setDeviceMetricsOverride", width=1180, height=700, deviceScaleFactor=1, mobile=False); await asyncio.sleep(0.8)
        mr, btn2 = await A.js(MENU), await A.js(MORE)
        S.check("после изменения размера окна меню открыто", mr is not None)
        S.check("и осталось в окне над кнопкой", mr and mr["r"] <= mr["w"] and mr["t"] >= 0 and mr["b"] <= btn2["t"] + 0.5 + 8, f"{mr} кнопка {btn2}")

        # ------------------------------------------------------------ закрывается только по действию пользователя
        await click_at(A, btn2["cx"], btn2["cy"]); await asyncio.sleep(0.8)
        S.check("повторное нажатие на «Ещё» закрывает меню и не открывает заново", await A.js(MENU) is None)
        await click_at(A, btn2["cx"], btn2["cy"]); await asyncio.sleep(0.5)
        S.check("следующее нажатие открывает снова", await A.js(MENU) is not None)
        for t in ("keyDown", "keyUp"):
            await A.send("Input.dispatchKeyEvent", type=t, key="Escape", code="Escape", windowsVirtualKeyCode=27)
        await asyncio.sleep(0.4)
        S.check("Escape закрывает", await A.js(MENU) is None)

        # ------------------------------------------------------------ низкое окно: меню помещается (прокрутка), подменю внутри
        await A.send("Emulation.setDeviceMetricsOverride", width=1000, height=420, deviceScaleFactor=1, mobile=False); await asyncio.sleep(1.0)
        b3 = await A.js(MORE)
        await click_at(A, b3["cx"], b3["cy"]); await asyncio.sleep(0.5)
        ml = await A.js(MENU)
        S.check("в низком окне меню открывается и помещается", ml is not None and ml["t"] >= 0 and ml["b"] <= ml["h"] and ml["scroll"], str(ml))
        xy = await A.js("(() => { const b = [...document.querySelectorAll('.ctx-menu [role=menuitem]')].find(x => x.textContent.includes('Шумоподавление')); if (!b) return null; const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()")
        if xy:
            await click_at(A, xy[0], xy[1]); await asyncio.sleep(0.5)
            ml2 = await A.js(MENU)
            S.check("в низком окне подменю раскрывается внутри меню, меню остаётся в окне", await A.js("!!document.querySelector('.ctx-sub.inline')") and ml2["t"] >= 0 and ml2["b"] <= ml2["h"], str(ml2))
        await A.shot("more-menu-low")
        for t in ("keyDown", "keyUp"):
            await A.send("Input.dispatchKeyEvent", type=t, key="Escape", code="Escape", windowsVirtualKeyCode=27)
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False); await asyncio.sleep(0.8)

        # ------------------------------------------------------------ индикатор подключения: плавающий, подробности по нажатию, исчезает
        S.check("при нормальной связи индикатора нет", not await A.js("!!document.querySelector('.conn-chip')"))
        bar0 = await A.js(RBAR)
        await A.send("Network.emulateNetworkConditions", offline=True, latency=0, downloadThroughput=0, uploadThroughput=0)
        shown = await A.wait_for("!!document.querySelector('.conn-chip')", 60)
        if shown:
            await asyncio.sleep(0.6)
            chip = await A.js("(() => { const r = document.querySelector('.conn-chip').getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom, pos: getComputedStyle(document.querySelector('.conn-chip')).position}; })()")
            bar1 = await A.js(RBAR)
            S.check("при обрыве связи появляется небольшой плавающий индикатор", chip["pos"] == "fixed" and (chip["r"] - chip["l"]) < 360 and (chip["b"] - chip["t"]) < 50, str(chip))
            S.check("панель управления не сдвинулась", bar0 == bar1, f"{bar0} → {bar1}")
            S.check("индикатор не перекрывает панель управления", chip["b"] <= bar1[1] + 1, f"индикатор до {chip['b']:.0f}, панель с {bar1[1]}")
            await A.shot("conn-chip")
            await A.js("document.querySelector('.conn-pill').click()"); await asyncio.sleep(0.4)
            pop = await A.js("document.querySelector('.conn-pop')?.innerText || ''")
            S.check("по нажатию — подробности подключения", len(pop) > 20, pop[:80].replace("\n", " "))
            await A.shot("conn-chip-open")
        else:
            S.check("при обрыве связи появляется индикатор", False, "обрыв не обнаружен за 60 с (эмуляция offline не затронула уже открытые соединения?)")
        await A.send("Network.emulateNetworkConditions", offline=False, latency=0, downloadThroughput=-1, uploadThroughput=-1)
        gone = await A.wait_for("!document.querySelector('.conn-chip')", 90)
        S.check("после восстановления связи индикатор исчез сам", gone)
        S.check("ошибок страницы нет", not [e for e in (await A.state())["errs"] if "Uncaught" in e or "hooks" in e], str((await A.state())["errs"][:3]))
    finally:
        for c in (A, B):
            await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
