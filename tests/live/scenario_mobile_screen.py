"""Сценарий: просмотр демонстрации экрана на телефоне (эмуляция iPhone в Chromium: касания, pointer: coarse, 390×844 и поворот 844×390).

Проверяется: панель демонстрации и кнопки масштаба — отдельной строкой под изображением, не поверх него; «щипок» двумя пальцами приближает
демонстрацию и не переключает сцену на другого участника; сдвиг приближенного изображения — тоже; «На весь экран» там, где браузер не умеет
разворачивать элемент (iPhone: Safari и приложение с главного экрана), — сцена поверх страницы; поворот сохраняет масштаб; «Свернуть» возвращает
сцену во встречу; нижняя панель телефона — без наложений, «Выйти» отделена. Safari и настоящий iPhone эмуляция не заменяет (движок другой).

Запуск на стенде (devserver_live + LiveKit + Vite): PEREGOVORKA_URL=http://127.0.0.1:5180 BROWSER_PATH=… python3 tests/live/scenario_mobile_screen.py"""
import asyncio, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

HERE = os.path.dirname(os.path.abspath(__file__))
ROOM_SLUG = "legal"

PINCH = """(async () => {
  const v = document.querySelector('.screen-stage.big .screen-view'); const r = v.getBoundingClientRect();
  const cx = r.left + r.width * 0.3, cy = r.top + r.height * 0.45;
  const ev = (type, id, x, y) => v.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: 'touch', clientX: x, clientY: y, bubbles: true, isPrimary: id === 1 }));
  ev('pointerdown', 1, cx - 20, cy); ev('pointerdown', 2, cx + 20, cy);
  for (let i = 1; i <= 10; i++) { ev('pointermove', 1, cx - 20 - i * 8, cy); ev('pointermove', 2, cx + 20 + i * 8, cy); await new Promise((f) => setTimeout(f, 16)); }
  ev('pointerup', 1, cx - 100, cy); ev('pointerup', 2, cx + 100, cy);
  await new Promise((f) => setTimeout(f, 300));
  return document.querySelector('.screen-stage.big .sb-pct')?.textContent || '';
})()"""
PAN = """(async () => {
  const v = document.querySelector('.screen-stage.big .screen-view'); const r = v.getBoundingClientRect();
  const ev = (type, x) => v.dispatchEvent(new PointerEvent(type, { pointerId: 7, pointerType: 'touch', clientX: x, clientY: r.top + r.height / 2, bubbles: true, isPrimary: true }));
  const z = () => document.querySelector('.screen-stage.big .screen-zoom').style.transform;
  const before = z(); ev('pointerdown', r.left + 300);
  for (let i = 1; i <= 8; i++) { ev('pointermove', r.left + 300 - i * 25); await new Promise((f) => setTimeout(f, 16)); }
  ev('pointerup', r.left + 100); await new Promise((f) => setTimeout(f, 300));
  return before !== z();
})()"""
GEOM = """(() => {
  const s = document.querySelector('.screen-stage.max, .screen-stage.big'); if (!s) return null;
  const b = (e) => { const x = e.getBoundingClientRect(); return { l: x.left, t: x.top, r: x.right, b: x.bottom, w: x.width, h: x.height }; };
  return { stage: b(s), view: b(s.querySelector('.screen-view')), bar: b(s.querySelector('.screen-bar')), max: s.classList.contains('max'),
           pct: s.querySelector('.sb-pct')?.textContent, badge: !!s.querySelector('.zoom-badge') && getComputedStyle(s.querySelector('.zoom-badge')).display !== 'none',
           vw: innerWidth, vh: innerHeight, overflow: document.documentElement.scrollWidth > innerWidth + 1, main: document.querySelector('.st-cell.big')?.className || '' };
})()"""
RBAR = """(() => {
  const b = [...document.querySelectorAll('.rbar .rbtn-wrap')].filter((e) => e.getBoundingClientRect().width > 0).map((e) => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, label: e.textContent.trim() }; });
  b.sort((x, y) => x.l - y.l);
  const gaps = b.slice(1).map((x, i) => x.l - b[i].r), exit = b.findIndex((x) => /Выйти/.test(x.label));
  return { n: b.length, labels: b.map((x) => x.label), minGap: Math.min(...gaps), exitGap: exit > 0 ? b[exit].l - b[exit - 1].r : null, right: Math.max(...b.map((x) => x.r)), vw: innerWidth };
})()"""


async def emulate(c, w, h, landscape=False):
    await c.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=3, mobile=True,
                 screenOrientation={"type": "landscapePrimary" if landscape else "portraitPrimary", "angle": 90 if landscape else 0})
    await c.send("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
    await c.send("Emulation.setUserAgentOverride", userAgent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    await asyncio.sleep(1.0)


async def main():
    pub = subprocess.Popen([sys.executable, os.path.join(HERE, "screen_publisher.py"), "bob", "bob-pass", ROOM_SLUG, "240"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    A = S.Client("alice-phone", "alice", "alice-pass", 9531)
    try:
        line = pub.stdout.readline()
        S.check("компьютер Бориса показывает экран", line.startswith("PUBLISHING"), line.strip()[:80])
        await A.start()
        await emulate(A, 390, 844)
        rid = await A.js(f"fetch('/api/v1/rooms').then(r=>r.json()).then(a=>a.find(x=>x.slug=='{ROOM_SLUG}').id)")
        await A.js("Object.keys(localStorage).filter(k=>k.startsWith('pg:stage:')).forEach(k=>localStorage.removeItem(k))")
        await A.goto(f"/rooms/{rid}", 2.5)
        S.check("Алиса вошла с телефона", await A.join(navigate=False))
        S.check("демонстрация крупно", await A.wait_for("!!document.querySelector('.st-cell.big .screen-stage.big video') && document.querySelector('.st-cell.big .screen-stage.big video').videoWidth > 0", 30))
        g = await A.js(GEOM)
        S.check("демонстрация занимает всю площадь над лентой (не полоса 16:9 посреди пустоты)", g and g["stage"]["h"] > 400, f"{g and round(g['stage']['h'])}px")
        S.check("панель демонстрации — под изображением, не поверх", g and g["bar"]["t"] >= g["view"]["b"] - 1, str(g and (round(g["bar"]["t"]), round(g["view"]["b"]))))
        S.check("панель демонстрации не шире сцены", g and g["bar"]["l"] >= g["stage"]["l"] - 1 and g["bar"]["r"] <= g["stage"]["r"] + 1)
        rb = await A.js(RBAR)
        S.check("нижняя панель: все кнопки в экране, между ними промежуток", rb["right"] <= rb["vw"] and rb["minGap"] >= 3, str(rb))
        S.check("нижняя панель: «Выйти» отделена от соседней кнопки", (rb["exitGap"] or 0) >= 14, str(rb["exitGap"]))
        S.check("на телефоне «Доска» и «Вид» — в меню «Ещё»", not any(x in ("Доска", "Вид") for x in rb["labels"]), str(rb["labels"]))

        pct = await A.js(PINCH)
        g = await A.js(GEOM)
        S.check("«щипок» приближает демонстрацию", pct.endswith("%") and int(pct[:-1]) >= 150, pct)
        S.check("«щипок» не переключает сцену на другого участника", "type-screen" in g["main"], g["main"])
        S.check("кнопки масштаба — в панели, не поверх изображения", not g["badge"])
        S.check("сдвиг приближенного изображения одним пальцем двигает его, сцена не листается", await A.js(PAN) and "type-screen" in (await A.js(GEOM))["main"])
        pins = await A.js("JSON.stringify(Object.keys(localStorage).filter(k=>k.startsWith('pg:stage:')).map(k=>JSON.parse(localStorage.getItem(k)).pins))")
        S.check("жесты не закрепили участников", pins in ("[]", "[[]]"), pins)

        # iPhone: развернуть элемент на весь экран браузер не умеет — сцена поверх страницы
        await A.js("Object.defineProperty(Document.prototype,'fullscreenEnabled',{get:()=>false,configurable:true})")
        await A.js("[...document.querySelectorAll('.screen-stage.big .screen-bar button')].find(b=>b.getAttribute('aria-label')=='На весь экран').click()")
        await asyncio.sleep(0.8)
        g = await A.js(GEOM)
        S.check("«На весь экран» без поддержки браузера: сцена поверх страницы на весь экран", g["max"] and abs(g["stage"]["w"] - 390) < 2 and abs(g["stage"]["h"] - 844) < 2, str(g["stage"]))
        S.check("масштаб сохранился при развороте", g["pct"] == pct, f"{pct} → {g['pct']}")
        await A.shot("max-portrait")
        await emulate(A, 844, 390, landscape=True)
        g = await A.js(GEOM)
        S.check("поворот: сцена по-прежнему на весь экран, изображение во всю ширину", g["max"] and abs(g["view"]["w"] - 844) < 2 and g["bar"]["b"] <= 391, str(g["view"]))
        S.check("поворот: масштаб сохранился", g["pct"] == pct, f"{pct} → {g['pct']}")
        await A.shot("max-landscape")
        await A.js("[...document.querySelectorAll('.screen-stage.max .screen-bar button')].find(b=>b.getAttribute('aria-label')=='Свернуть').click()")
        await asyncio.sleep(0.6)
        S.check("«Свернуть» возвращает демонстрацию во встречу", await A.js("!document.querySelector('.screen-stage.max') && !!document.querySelector('.st-cell .screen-stage.big') && !document.body.classList.contains('screen-max-open')"))
        await emulate(A, 390, 844)
        S.check("горизонтальной прокрутки нет", not (await A.js(GEOM))["overflow"])
        st = await A.state()
        S.check("ошибок страницы нет", not st["errs"], str(st["errs"]))
    finally:
        await A.close()
        pub.terminate()
    ok = [r for r in S.results if r[1]]
    print(f"ИТОГО: {len(ok)} из {len(S.results)}; не прошли: {[r[0] for r in S.results if not r[1]]}")


if __name__ == "__main__":
    asyncio.run(main())
