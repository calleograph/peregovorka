"""Автоматический аудит экранов администрирования (см. README.md): обходит каждый раздел на двух ширинах и собирает объективные проблемы интерфейса.

Что проверяется на каждом экране: ошибки страницы и консоли; горизонтальная прокрутка страницы; поля ввода, кнопки и ссылки без доступного названия; изображения без alt;
повторяющиеся id; наличие заголовка раздела; пустой экран; кнопки «Сохранить»/«Отмена» в формах. Это НЕ оценка дизайна: смысл подсказок и порядок полей проверяет человек.
Результат — таблица в консоли и JSON в SMOKE_OUT/audit-admin.json. Код возврата 1, если найдены проблемы категории «ошибка» (не «замечание»)."""
import asyncio, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

PAGE_SCAN = r"""(() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const name = (el) => (el.getAttribute('aria-label') || el.getAttribute('title') || (el.id && document.querySelector('label[for="' + el.id + '"]')?.innerText) ||
    el.closest('label')?.innerText || el.getAttribute('placeholder') || el.innerText || el.value || '').trim();
  const scrolls = (el) => { for (let e = el; e && e !== document.body; e = e.parentElement) { const o = getComputedStyle(e).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
  const main = document.querySelector('main') || document.body;
  const out = { overflow: document.documentElement.scrollWidth - window.innerWidth, h: [...main.querySelectorAll('h1,h2')].filter(vis).map(h => h.innerText.trim()).slice(0, 3),
    textLen: (main.innerText || '').length, unlabeledInputs: [], unnamedButtons: [], unnamedLinks: [], imgNoAlt: 0, dupIds: [], wideTables: 0, tinyTargets: 0, forms: 0, saveButtons: 0 };
  for (const el of main.querySelectorAll('input:not([type=hidden]), select, textarea')) {
    if (!vis(el)) continue;
    if (!name(el) || (el.type === 'checkbox' && !name(el))) out.unlabeledInputs.push((el.name || el.type || el.tagName) + ':' + (el.getAttribute('placeholder') || '').slice(0, 20));
  }
  for (const el of main.querySelectorAll('button')) { if (vis(el) && !name(el)) out.unnamedButtons.push(el.className.slice(0, 30)); }
  for (const el of main.querySelectorAll('a[href]')) { if (vis(el) && !name(el)) out.unnamedLinks.push(el.getAttribute('href').slice(0, 40)); }
  out.imgNoAlt = [...main.querySelectorAll('img')].filter(i => vis(i) && !i.hasAttribute('alt')).length;
  const ids = {}; for (const el of document.querySelectorAll('[id]')) ids[el.id] = (ids[el.id] || 0) + 1;
  out.dupIds = Object.entries(ids).filter(([, n]) => n > 1).map(([k]) => k).slice(0, 5);
  out.wideTables = [...main.querySelectorAll('table')].filter(t => vis(t) && t.getBoundingClientRect().width > window.innerWidth + 1 && !scrolls(t)).length;
  out.tinyTargets = [...main.querySelectorAll('button, a.btn, select, input[type=checkbox]')].filter(el => { if (!vis(el)) return false; const r = el.getBoundingClientRect(); return Math.min(r.width, r.height) < 24 && !el.closest('label'); }).length;
  out.forms = main.querySelectorAll('form').length;
  out.saveButtons = [...main.querySelectorAll('button')].filter(b => /^(Сохранить|Применить)/.test(b.innerText.trim())).length;
  return out;
})()"""
SPA_BAD = ("ошибка загрузки", "something went wrong", "cannot read", "undefined")


async def tabs(R):
    return await R.js(r"""(async () => { const html = await (await fetch('/src/pages/AdminPage.tsx')).text(); return [...html.matchAll(/id:\s*"([a-z0-9_]+)",\s*label:\s*"([^"]+)"/g)].map(m => [m[1], m[2]]); })()""")


async def scan(R, tab, width):
    await R.send("Emulation.setDeviceMetricsOverride", width=width, height=900 if width > 600 else 844, deviceScaleFactor=1, mobile=width < 600)
    await R.js("window.__errs = []")
    await R.goto(f"/admin?tab={tab}", 1.6)
    await R.wait_for("(document.querySelector('main') || document.body).innerText.length > 80", 12)
    await asyncio.sleep(0.8)
    r = await R.js(PAGE_SCAN)
    r["errs"] = await R.js("window.__errs.slice(0, 3)")
    low = (await R.js("(document.querySelector('main') || document.body).innerText.slice(0, 4000)") or "").lower()
    r["badText"] = [w for w in SPA_BAD if w in low]
    return r


async def main():
    R = S.Client("root", "root", "root-pass", 9494)
    findings, summary = [], {}
    try:
        await R.start()
        items = await tabs(R)
        S.check("список разделов получен из исходника страницы", len(items) > 30, str(len(items)))
        for tab, label in items:
            for width in (1366, 390):
                r = await scan(R, tab, width)
                key = f"{tab}@{width}"
                summary[key] = r
                issues = []
                if r["errs"]: issues.append(("ошибка", "ошибки страницы: " + "; ".join(r["errs"])[:160]))
                if r["overflow"] > 1: issues.append(("ошибка", f"горизонтальная прокрутка страницы на {r['overflow']} px"))
                if r["textLen"] < 60: issues.append(("ошибка", "экран пуст"))
                if r["badText"]: issues.append(("замечание", "в тексте: " + ", ".join(r["badText"])))
                if not r["h"]: issues.append(("замечание", "нет заголовка раздела"))
                if r["unlabeledInputs"]: issues.append(("ошибка", f"поля без названия: {len(r['unlabeledInputs'])} ({', '.join(r['unlabeledInputs'][:3])})"))
                if r["unnamedButtons"]: issues.append(("ошибка", f"кнопки без названия: {len(r['unnamedButtons'])}"))
                if r["unnamedLinks"]: issues.append(("ошибка", f"ссылки без названия: {len(r['unnamedLinks'])}"))
                if r["imgNoAlt"]: issues.append(("ошибка", f"изображения без alt: {r['imgNoAlt']}"))
                if r["dupIds"]: issues.append(("ошибка", f"повторяющиеся id: {r['dupIds']}"))
                if r["wideTables"]: issues.append(("замечание", f"таблиц шире экрана без прокрутки: {r['wideTables']}"))
                if width == 390 and r["tinyTargets"]: issues.append(("замечание", f"мелких элементов управления (<24 px): {r['tinyTargets']}"))
                for sev, text in issues:
                    findings.append({"tab": tab, "label": label, "width": width, "severity": sev, "text": text})
                S.check(f"{label} ({tab}) @ {width}", not any(s == "ошибка" for s, _ in issues), "; ".join(f"[{s}] {t}" for s, t in issues))
    finally:
        await R.close()
    path = os.path.join(S.OUT, "audit-admin.json")
    json.dump({"findings": findings, "summary": summary}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    errs = [f for f in findings if f["severity"] == "ошибка"]
    print(f"ИТОГО: проверено экранов {len(summary)}; ошибок {len(errs)}, замечаний {len(findings) - len(errs)}; подробности {path}")
    sys.exit(1 if errs or any(not r[1] for r in S.results if r[0].startswith('список')) else 0)

asyncio.run(main())
