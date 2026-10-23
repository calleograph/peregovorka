"""Сценарий (см. README.md): временная равноправная комната: создание первым переходом по ссылке /p/<код>, вход второго участника по той же ссылке, равные права, управление ссылкой."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

CODE = "kq7m2xv9bnp4rtw3hsdy"
ENABLE = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json();
  const r = await fetch('/api/v1/admin/settings/general', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}, body: JSON.stringify({peer_rooms_enabled: true})});
  return r.status; })()"""


async def main():
    R = S.Client("root", "root", "root-pass", 9494)
    A = S.Client("alice", "alice", "alice-pass", 9495)
    B = S.Client("bob", "bob", "bob-pass", 9496)
    try:
        for c in (R, A, B):
            await c.start()
        S.check("администратор включил равноправные комнаты", (await R.js(ENABLE)) == 200)
        await A.goto(f"/p/{CODE}", 3.0)
        S.check("первый переход по ссылке создал комнату и открыл страницу входа", await A.wait_for("location.pathname.startsWith('/rooms/p-')", 20), await A.js("location.pathname"))
        S.check("Алиса: вошла в комнату", await A.join(navigate=False))
        await B.goto(f"/p/{CODE}", 3.0)
        S.check("Боб по той же ссылке попал в ту же комнату", await B.wait_for(f"location.pathname === '/rooms/p-{CODE}'", 20), await B.js("location.pathname"))
        S.check("Боб: вошёл", await B.join(navigate=False))
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        await asyncio.sleep(3)
        for c in (A, B):
            s = await c.js(S.STATS)
            S.check(f"{c.label}: звук собеседника идёт", s["ain"] > 20, str(s))
        # у обоих нет руководящих действий: «Выключить у всех микрофоны» и «Завершить для всех» недоступны
        for c in (A, B):
            await c.click_btn("Ещё"); await asyncio.sleep(0.5)
            labels = await c.js("[...document.querySelectorAll('[role=menuitem]')].map(x => x.textContent.trim())")
            S.check(f"{c.label}: в меню нет «Выключить у всех микрофоны»", not any("Выключить у всех" in (x or "") for x in labels), str(labels)[:200])
            await c.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
            await c.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        # камера у Боба — видят все, без «разрешения ведущего»
        S.check("Боб: включил камеру без ведущего", await B.click_btn("Камера выкл."))
        S.check("у Алисы видео Боба", await A.wait_for("(()=>{const t=[...document.querySelectorAll('.tile')].find(x=>(x.getAttribute('title')||'').includes('Борис'));return !!t&&!t.querySelector('.avatar')&&!!t.querySelector('video')})()", 25))
        # список комнат: у Алисы (владелец) комната видна, у Боба — нет
        await A.goto("/", 2.5); await B.goto("/", 2.5)
        S.check("у владельца комната видна в списке с меткой «Равноправная»", await A.wait_for("document.body.innerText.includes('Равноправная')", 15))
        S.check("у Боба в списке этой комнаты нет", not await B.js("document.body.innerText.includes('Равноправная встреча')"))
        # управление ссылкой
        box = await A.js("(() => { const el = [...document.querySelectorAll('.room-card, .rc, .upc-card, .upc-row')].find(x => x.textContent.includes('Равноправная')); if (!el) return null; el.scrollIntoView({block: 'center'}); const r = el.getBoundingClientRect(); return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)}; })()")
        S.check("карточка равноправной комнаты найдена", bool(box), str(box))
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"], y=box["y"])            # в Chromium под Linux без движения и buttons=2 контекстное меню не открывается
        for t in ("mousePressed", "mouseReleased"):                                    # настоящий щелчок правой кнопкой
            await A.send("Input.dispatchMouseEvent", type=t, x=box["x"], y=box["y"], button="right", buttons=2 if t == "mousePressed" else 0, clickCount=1)
        await asyncio.sleep(0.6)
        S.check("в меню комнаты есть «Управление ссылкой…»", await A.js("[...document.querySelectorAll('[role=menuitem]')].some(x => x.textContent.includes('Управление ссылкой'))"))
        await A.js("[...document.querySelectorAll('[role=menuitem]')].find(x => x.textContent.includes('Управление ссылкой')).click()")
        S.check("диалог управления показывает ссылку", await A.wait_for(f"!![...document.querySelectorAll('input')].find(i => i.value.endsWith('/p/{CODE}'))", 10))
        await A.shot("peer-manage")
        S.check("ошибок страницы нет", not (await A.state())["errs"] and not (await B.state())["errs"], str((await A.state())["errs"][:2]))
    finally:
        for c in (R, A, B):
            await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
