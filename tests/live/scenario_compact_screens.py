"""Сценарий (см. README.md): комната на компактных экранах ноутбуков 13–14" (CSS-размер окна браузера 1280×600 … 1366×650 — 1366×768 или 1920×1080 при
масштабе 150 % за вычетом панелей браузера). Проверяется: нет горизонтальной прокрутки; сцена занимает достаточную высоту; панель кнопок и их подписи
целиком в окне; окно «Устройства» помещается между шапкой и панелью кнопок, варианты шумоподавления — строкой «кружок + подпись»; подсказка о связи на
плитке не обрезается; уведомление не закрывает панель кнопок. Снимки — в $SMOKE_OUT/compact-*.png. Не проверяется: настоящий дисплей и масштабирование ОС."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/sales"
SIZES = [(1366, 650), (1354, 609), (1280, 600)]
GEOM = """(() => {
  const q = (s) => document.querySelector(s), r = (el) => el ? el.getBoundingClientRect() : null;
  const bar = r(q('.rbar')), stage = r(q('.st-view')), head = r(q('.room-head'));
  const labels = [...document.querySelectorAll('.rbar .rbtn-label')].map((e) => e.getBoundingClientRect()).filter((b) => b.width > 0);
  return {sw: document.documentElement.scrollWidth, iw: innerWidth, ih: innerHeight, sh: document.documentElement.scrollHeight,
    barTop: bar && Math.round(bar.top), barBottom: bar && Math.round(bar.bottom), stageH: stage && Math.round(stage.height), headBottom: head && Math.round(head.bottom),
    labelsIn: labels.every((b) => b.bottom <= innerHeight + 1)};
})()"""
POP = """(() => { const p = document.querySelector('.dev-pop'); if (!p) return null; const b = p.getBoundingClientRect();
  const radios = [...p.querySelectorAll('.noise-modes label.check')].map((l) => { const i = l.querySelector('input').getBoundingClientRect(), t = l.querySelector('.check-body').getBoundingClientRect();
    return {row: Math.abs(i.top - t.top) < 12 && i.right <= t.left + 2, iw: Math.round(i.width)}; });
  return {top: Math.round(b.top), bottom: Math.round(b.bottom), ih: innerHeight, scroll: p.scrollHeight > p.clientHeight, radios}; })()"""


async def main():
    A = S.Client("a", "alice", "alice-pass", 9801)
    B = S.Client("b", "bob", "bob-pass", 9802)
    try:
        await A.start(); await B.start()
        S.check("Алиса вошла", await A.join())
        S.check("Боб вошёл", await B.join())
        await asyncio.sleep(3)
        for w, h in SIZES:
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False); await asyncio.sleep(1.2)
            g = await A.js(GEOM)
            S.check(f"{w}×{h}: без горизонтальной и вертикальной прокрутки страницы", g["sw"] <= g["iw"] and g["sh"] <= g["ih"] + 1, str(g))
            S.check(f"{w}×{h}: панель кнопок и подписи целиком в окне", g["barBottom"] is not None and g["barBottom"] <= g["ih"] and g["labelsIn"], str(g))
            S.check(f"{w}×{h}: сцене остаётся ≥ 60 % высоты окна", (g["stageH"] or 0) >= 0.6 * h, str(g))
            await A.shot(f"compact-{w}x{h}")
            # окно «Устройства» из меню «Ещё» (как на снимке пользователя): между шапкой и панелью, варианты — строкой
            await A.js("document.querySelector('.rbar [aria-label=\"Ещё\"], .rbar button[title*=\"Ещё\"]')?.click()"); await asyncio.sleep(0.5)
            await A.js("[...document.querySelectorAll('[role=menuitem], .menu button')].find((b) => /Устройства/.test(b.textContent))?.click()"); await asyncio.sleep(1.0)
            p = await A.js(POP)
            S.check(f"{w}×{h}: окно «Устройства» открыто и целиком в окне браузера", bool(p) and p["top"] >= 0 and p["bottom"] <= p["ih"], str(p))
            S.check(f"{w}×{h}: варианты шумоподавления — «кружок + подпись» в одной строке", bool(p) and len(p["radios"]) == 3 and all(x["row"] and x["iw"] < 30 for x in p["radios"]), str(p))
            await A.shot(f"compact-{w}x{h}-devices")
            await A.js("document.querySelector('.dev-pop .dev-head button')?.click()"); await asyncio.sleep(0.3)
            # подсказка о связи на плитке: целиком видна (не обрезается плиткой), в пределах окна
            await A.js("document.querySelector('.tile-net')?.click()"); await asyncio.sleep(0.4)
            t = await A.js("""(() => { const p = document.querySelector('.tile-net-pop'); if (!p) return null; const b = p.getBoundingClientRect();
              return {inBody: p.parentElement === document.body, top: Math.round(b.top), left: Math.round(b.left), right: Math.round(b.right), bottom: Math.round(b.bottom), h: Math.round(b.height), sh: p.scrollHeight}; })()""")
            S.check(f"{w}×{h}: подсказка о связи на плитке целиком видна (поверх плитки, в пределах окна)",
                    bool(t) and t["inBody"] and t["top"] >= 0 and t["left"] >= 0 and t["right"] <= w and t["bottom"] <= h and t["h"] >= t["sh"] - 1, str(t))
            await A.js("document.body.click()"); await asyncio.sleep(0.2)
            S.check(f"{w}×{h}: подсказка закрывается щелчком мимо", not await A.js("!!document.querySelector('.tile-net-pop')"))
            top = await A.js("getComputedStyle(document.querySelector('.st-toasts')).top")
            S.check(f"{w}×{h}: уведомления встречи — сверху сцены (не закрывают имена и панель кнопок)", top == "10px", str(top))
    finally:
        await A.close(); await B.close()
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")


asyncio.run(main())
