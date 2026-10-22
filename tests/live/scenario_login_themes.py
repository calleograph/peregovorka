"""Сценарий (см. README.md): страница входа в каждой теме оформления. Тема берётся из сохранённого выбора ДО загрузки приложения (без вспышки другой схемы);
цвета формы, полей, кнопки и подписей — из токенов темы; контраст подписей и полей достаточный; на 1366 и 390 px нет горизонтальной прокрутки."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

THEMES = ["light", "dark", "diamond", "amber", "corporate"]
EARLY = """(() => { window.__themeAtBody = null; new MutationObserver((m, o) => { if (document.body) { window.__themeAtBody = document.documentElement.getAttribute('data-theme'); o.disconnect(); } })
  .observe(document, {childList: true, subtree: true}); })();"""
PROBE = """(() => {
  const rgba = (c) => { const n = c.match(/[\\d.]+/g).map(Number); return c.startsWith('color(') ? [n[0] * 255, n[1] * 255, n[2] * 255, n.length > 3 ? n[3] : 1] : [n[0], n[1], n[2], n.length > 3 ? n[3] : 1]; };
  const lum = (c) => { const m = rgba(c); const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }; return .2126 * f(m[0]) + .7152 * f(m[1]) + .0722 * f(m[2]); };
  const cr = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  const shell = document.querySelector('.px-shell'), form = document.querySelector('.px-form'), input = form.querySelector('input'), label = form.querySelector('label'), btn = form.querySelector('.px-cta');
  const cs = (e) => getComputedStyle(e);
  const solid = (e) => { let n = e; while (n) { const b = cs(n).backgroundColor; if (b !== 'transparent' && rgba(b)[3] >= .5) return b; n = n.parentElement; } return 'rgb(255,255,255)'; };
  return {theme: document.documentElement.getAttribute('data-theme'), early: window.__themeAtBody, accent: cs(document.documentElement).getPropertyValue('--accent').trim(),
    shellBg: solid(shell), inputBg: cs(input).backgroundColor, inputText: cs(input).color, label: cs(label).color, btnBg: cs(btn).backgroundImage + ' ' + cs(btn).backgroundColor,
    labelContrast: +cr(cs(label).color, solid(form)).toFixed(2), inputContrast: +cr(cs(input).color, cs(input).backgroundColor).toFixed(2),
    sw: document.documentElement.scrollWidth, iw: innerWidth};
})()"""


async def main():
    A = S.Client("A", "root", "root-pass", 9801)
    seen = {}
    try:
        await A.start()
        await A.send("Page.addScriptToEvaluateOnNewDocument", source=EARLY)
        await A.js("(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); await fetch('/api/v1/auth/logout', {method: 'POST', headers: {'X-CSRF-Token': me.csrf_token}}); })()")
        await A.js("sessionStorage.clear()")
        for t in THEMES:
            await A.js(f"localStorage.setItem('pg:theme', {t!r})")
            for w, h, mob in ((1366, 800, False), (390, 844, True)):
                await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=mob)
                await A.goto("/", 1.5)
                S.check(f"{t} {w}px: страница входа открыта", await A.wait_for("!!document.querySelector('.px-form input')", 15))
                p = await A.js(PROBE)
                if w == 1366:
                    seen[t] = p
                    S.check(f"{t}: тема применена до появления страницы (без вспышки)", p["early"] == t and p["theme"] == t, f"при появлении body: {p['early']}, сейчас: {p['theme']}")
                    S.check(f"{t}: контраст подписей и полей достаточный (≥ 4.5)", p["labelContrast"] >= 4.5 and p["inputContrast"] >= 4.5, json.dumps(p, ensure_ascii=False))
                    await A.shot(f"login-{t}-1366")
                else:
                    S.check(f"{t} 390 px: без горизонтальной прокрутки", p["sw"] <= p["iw"], f"{p['sw']}/{p['iw']}")
                    if t in ("light", "dark"):
                        await A.shot(f"login-{t}-390")
        S.check("цвета формы различаются между темами (не одно синее оформление)", len({seen[t]["shellBg"] for t in THEMES}) >= 4 and len({seen[t]["accent"] for t in THEMES}) >= 3,
                json.dumps({t: (seen[t]["shellBg"], seen[t]["accent"]) for t in THEMES}, ensure_ascii=False))
        S.check("в тёмной теме поле ввода тёмное, в светлой — светлое", seen["dark"]["inputBg"] != seen["light"]["inputBg"], str((seen["dark"]["inputBg"], seen["light"]["inputBg"])))
        await A.js("localStorage.removeItem('pg:theme')")
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        await A.close()
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

asyncio.run(main())
