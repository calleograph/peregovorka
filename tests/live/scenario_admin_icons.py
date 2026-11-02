"""Сценарий (см. README.md): пиктограммы меню администрирования.

У каждого из разделов меню своя пиктограмма (без повторов), слева от названия, одного размера, на одной вертикали и с одинаковым отступом до текста;
у активного пункта и под указателем пиктограмма — цвета текста пункта, у остальных — приглушённая, но различимая (контраст ≥ 3:1, WCAG 1.4.11).
Все семь тем оформления; ширины 1600, 1280, 1024 и телефон 390 (меню свёрнуто в кнопку с пиктограммой текущего раздела); поиск по разделам.
"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

THEMES = ["glass", "light", "dark", "diamond", "amber", "corporate", "system"]
METRICS = r"""(() => {
  const nav = document.querySelector('.admin-nav'); const items = [...nav.querySelectorAll('.nav-items button')];
  const rgb = (c) => (c.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }; return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c.length === 3 || (c.length === 4 && c[3] > .5)) return c.slice(0, 3); } return [255, 255, 255]; };
  const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  const rows = items.map((b) => { const i = b.querySelector('svg.nav-ico'), l = b.querySelector('.nav-label'); const ir = i.getBoundingClientRect(), lr = l.getBoundingClientRect();
    return { label: l.textContent, svg: i.innerHTML, w: Math.round(ir.width), x: Math.round(ir.left), gap: Math.round(lr.left - ir.right), active: b.classList.contains('active'),
             icon: getComputedStyle(i).color, text: getComputedStyle(b).color, c: contrast(rgb(getComputedStyle(i).color).slice(0, 3), bgOf(b)), firstLine: Math.abs((ir.top + ir.height / 2) - (lr.top + parseFloat(getComputedStyle(l).lineHeight) / 2)) }; });
  return { n: rows.length, uniq: new Set(rows.map((r) => r.svg)).size, empty: rows.filter((r) => !r.svg).map((r) => r.label), sizes: [...new Set(rows.map((r) => r.w))], xs: [...new Set(rows.map((r) => r.x))],
           gaps: [...new Set(rows.map((r) => r.gap))], minContrast: Math.min(...rows.map((r) => r.c)).toFixed(2), worst: rows.reduce((a, r) => r.c < a.c ? r : a).label,
           activeSame: rows.filter((r) => r.active).every((r) => r.icon === r.text), active: rows.filter((r) => r.active).map((r) => r.label), offLine: rows.filter((r) => r.firstLine > 3).map((r) => r.label),
           overflowX: nav.scrollWidth - nav.clientWidth, pageX: document.documentElement.scrollWidth - innerWidth };
})()"""


async def main():
    c = S.Client("icons", "root", "root-pass", 9661)
    try:
        await c.start()
        for theme in THEMES:
            await c.js(f"localStorage.setItem('pg:theme', '{theme}'); localStorage.removeItem('pg:adminNav'); 1")
            await c.goto("/admin?tab=security_events", 3)
            await c.js("document.querySelector(\"button[aria-label='Развернуть все разделы']\")?.click()"); await asyncio.sleep(0.8)
            m = await c.js(METRICS)
            S.check(f"{theme}: у всех {m['n']} разделов пиктограмма, все разные", m["n"] >= 57 and m["uniq"] == m["n"] and not m["empty"], json.dumps({k: m[k] for k in ("n", "uniq", "empty")}, ensure_ascii=False))
            S.check(f"{theme}: один размер, одна вертикаль, один отступ до текста", m["sizes"] == [18] and len(m["xs"]) == 1 and len(m["gaps"]) == 1, json.dumps({k: m[k] for k in ("sizes", "xs", "gaps")}))
            S.check(f"{theme}: пиктограмма — по первой строке названия", not m["offLine"], str(m["offLine"]))
            S.check(f"{theme}: пиктограммы различимы (контраст ≥ 3:1)", float(m["minContrast"]) >= 3, f"{m['minContrast']} у «{m['worst']}»")
            S.check(f"{theme}: активный пункт — пиктограмма цвета текста", m["active"] == ["События и блокировки"] and m["activeSame"], str(m["active"]))
            S.check(f"{theme}: без горизонтальной прокрутки", m["overflowX"] <= 0 and m["pageX"] <= 0, f"{m['overflowX']} {m['pageX']}")
            # наведение: пиктограмма принимает цвет текста пункта
            pos = await c.js("(() => { const b = [...document.querySelectorAll('.admin-nav .nav-items button')].find(x => !x.classList.contains('active')); b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return [r.left + 40, r.top + r.height / 2, b.querySelector('.nav-label').textContent]; })()")
            await c.send("Input.dispatchMouseEvent", type="mouseMoved", x=pos[0], y=pos[1]); await asyncio.sleep(0.4)
            hov = await c.js(f"(() => {{ const b = [...document.querySelectorAll('.admin-nav .nav-items button')].find(x => x.querySelector('.nav-label').textContent === {json.dumps(pos[2])}); return getComputedStyle(b.querySelector('svg')).color === getComputedStyle(b).color; }})()")
            S.check(f"{theme}: под указателем пиктограмма цвета текста", hov)
            await c.send("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
            await c.js("document.querySelector('.admin-nav').scrollTop = 0; scrollTo(0, 0); 1")
            if theme in ("glass", "dark", "amber"): await c.shot(f"admin-{theme}-1280")
        await c.js("localStorage.setItem('pg:theme', 'glass'); 1")
        for w in (1600, 1024):
            await c.send("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
            await c.goto("/admin?tab=updates", 3)
            await c.js("document.querySelector(\"button[aria-label='Развернуть все разделы']\")?.click()"); await asyncio.sleep(0.6)
            m = await c.js(METRICS)
            S.check(f"{w}: пиктограммы на месте, без горизонтальной прокрутки, по первой строке", m["uniq"] == m["n"] and m["overflowX"] <= 0 and m["pageX"] <= 0 and not m["offLine"] and len(m["xs"]) == 1, json.dumps({k: m[k] for k in ("n", "uniq", "overflowX", "pageX", "offLine")}, ensure_ascii=False))
        # поиск по разделам: у найденного тоже пиктограмма
        await c.send("Emulation.setDeviceMetricsOverride", width=1280, height=900, deviceScaleFactor=2, mobile=False)
        await c.goto("/admin?tab=system", 3)
        await c.js("(() => { const i = document.querySelector('.nav-search'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, 'почта'); i.dispatchEvent(new Event('input', {bubbles: true})); })()"); await asyncio.sleep(0.6)
        f = await c.js("[...document.querySelectorAll('.nav-found button')].map(b => !!b.querySelector('svg.nav-ico path, svg.nav-ico rect, svg.nav-ico circle, svg.nav-ico ellipse'))")
        S.check("поиск: у каждого найденного раздела пиктограмма", bool(f) and all(f), str(f))
        await c.shot("admin-search-2x")
        # телефон: меню свёрнуто в кнопку «Раздел» с пиктограммой текущего; раскрытое — с пиктограммами, без горизонтальной прокрутки
        await c.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=3, mobile=True)
        await c.goto("/admin?tab=sip", 3)
        t = await c.js("(() => { const b = document.querySelector('.admin-nav .nav-toggle'); const i = b.querySelector('svg.nav-ico'); return {icon: !!i && i.getBoundingClientRect().width === 18, text: b.querySelector('b').textContent}; })()")
        S.check("телефон: в кнопке раздела — пиктограмма и название текущего", t["icon"] and t["text"] == "SIP-телефония", json.dumps(t, ensure_ascii=False))
        await c.shot("admin-phone-closed")
        await c.js("document.querySelector('.admin-nav .nav-toggle').click()"); await asyncio.sleep(0.5)
        await c.js("document.querySelector(\"button[aria-label='Развернуть все разделы']\")?.click()"); await asyncio.sleep(0.6)
        m = await c.js(METRICS)
        S.check("телефон: раскрытое меню — пиктограммы у всех, без горизонтальной прокрутки", m["uniq"] == m["n"] and m["overflowX"] <= 0 and m["pageX"] <= 0, json.dumps({k: m[k] for k in ("n", "uniq", "overflowX", "pageX")}))
        await c.shot("admin-phone-open")
        print("errs:", ((await c.state()) or {}).get("errs", [])[:3])
    finally:
        await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
