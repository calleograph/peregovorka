"""Визуальный аудит приложения Windows: масштабы 100–200 %, размеры окна 1920×1080 … минимальный, светлая и тёмная тема. Снимки окна (экран X-сервера)
и автоматические проверки оболочки и страницы организации: нет горизонтальной прокрутки, нет обрезанного без многоточия текста, одинаковые высоты
однотипных кнопок и полей, все интерактивные элементы с названием. Смотреть снимки всё равно глазами — проверки ловят только грубое.

    DISPLAY=:98 (Xvfb 3840×2400) ~/pg-work/venv/bin/python tests/live/desktop_visual.py [масштабы через запятую]
Стенд — как для desktop_app.py."""
import asyncio, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, OUT, check, results  # noqa: E402
from desktop_app import A_URL, B_URL, login, probe_in_shell, reset_sessions  # noqa: E402

SIZES = [(1920, 1080), (1600, 900), (1366, 768), (1280, 720), (1024, 768), (800, 520)]
SCREEN = tuple(int(x) for x in os.environ.get("SCREEN", "3840x2400").split("x"))      # размер экрана X-сервера в пикселях

LAYOUT = r"""(() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = { hscroll: document.documentElement.scrollWidth > innerWidth + 1, cut: [], heights: {}, unnamed: [] };
  for (const e of document.querySelectorAll('body *')) {
    if (!vis(e) || !e.childNodes.length || e.closest('svg')) continue;
    const s = getComputedStyle(e);
    const texts = [...e.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim());
    const clamp = s.webkitLineClamp && s.webkitLineClamp !== 'none';          // ограничение строк с «…» — предусмотренное поведение
    if (!texts.length || clamp || s.textOverflow === 'ellipsis' || !(s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflow === 'clip')) continue;
    // обрезан ли сам текст: границы текста (Range) за пределами элемента; блики и тени (::after, transform) не считаются
    const box = e.getBoundingClientRect();
    const cut = texts.some((n) => { const r = document.createRange(); r.selectNodeContents(n); const t = r.getBoundingClientRect(); return t.right > box.right + 1 || t.left < box.left - 1 || t.bottom > box.bottom + 2; });
    if (cut) out.cut.push(`${e.tagName.toLowerCase()}.${[...e.classList].join('.')}: ${e.textContent.trim().slice(0, 40)}`);
  }
  for (const [sel, key] of SELECTORS) {
    const hs = [...document.querySelectorAll(sel)].filter(vis).map((e) => Math.round(e.getBoundingClientRect().height));
    if (hs.length) out.heights[key] = [...new Set(hs)];
  }
  for (const e of document.querySelectorAll('button, a[href], input, select, textarea, [role=button]')) {
    if (!vis(e) || e.type === 'hidden') continue;
    const name = (e.getAttribute('aria-label') || e.getAttribute('title') || e.textContent || e.getAttribute('placeholder') || (e.labels && e.labels[0] && e.labels[0].textContent) || '').trim();
    if (!name) out.unnamed.push(`${e.tagName.toLowerCase()}.${[...e.classList].join('.')}`);
  }
  return out;
})()"""
SHELL_SEL = '[[".btn:not(.sm):not(.icon):not(.lg)", "btn"], [".btn.sm:not(.icon)", "btn-sm"], [".input", "input"], [".seg", "seg"], [".tabs button", "tab"], [".side .org", "tile"]]'


async def layout(page, selectors="[]"):
    return await page.js(LAYOUT.replace("SELECTORS", selectors))


def judge(name, r, expect_heights=None):
    bad = []
    if r["hscroll"]:
        bad.append("горизонтальная прокрутка")
    if r["cut"]:
        bad.append("обрезано: " + "; ".join(r["cut"][:4]))
    for k, v in r["heights"].items():
        if len(v) > 1:
            bad.append(f"разная высота {k}: {v}")
        if expect_heights and k in expect_heights and v and v[0] != expect_heights[k]:
            bad.append(f"{k}: {v[0]} вместо {expect_heights[k]}")
    if r["unnamed"]:
        bad.append("без названия: " + ", ".join(r["unnamed"][:4]))
    check(name, not bad, "; ".join(bad)[:400])


async def first_run(app, shell, tag, themes):
    """Локальные экраны без единой организации (так приложение выглядит сразу после установки, в том числе без сети)."""
    await shell.wait("document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'", 15)
    for theme in themes:
        await shell.js(f"window.pg.call('shell:settings', {{ theme: '{theme}' }})")
        for (w, h) in ((1366, 768), (800, 520)):
            await app.m(f"pg.main.win.setBounds({{ x: 0, y: 0, width: {w}, height: {h} }}); return true;")
            await asyncio.sleep(0.6)
            key = f"{tag}-{theme}-{w}x{h}"
            await app.shot_window(f"v-{key}-welcome")
            judge(f"{key}: приветствие", await layout(shell, SHELL_SEL), {"btn": 36})
            await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')).click()")
            await shell.wait("!!document.querySelector('#srv')", 5)
            await shell.js("document.querySelector('#nm').value = 'Управление информационных технологий и связи головного офиса'")
            await probe_in_shell(shell, "https://peregovorka-server-with-a-very-long-name.branch-office.example.org")
            await app.shot_window(f"v-{key}-server-error")
            judge(f"{key}: шаг сервера с ошибкой", await layout(shell, SHELL_SEL), {"btn": 36, "input": 36})
            await shell.js("document.querySelector('.side .tool[aria-label=\"Настройки\"]').click()")
            for t in ("general", "updates", "diag"):
                await shell.js(f"document.querySelector('#tab-{t}').click()")
                await asyncio.sleep(0.8 if t == "diag" else 0.3)
                await app.shot_window(f"v-{key}-empty-settings-{t}")
                judge(f"{key}: настройки без организаций «{t}»", await layout(shell, SHELL_SEL), {"btn": 36, "seg": 36, "tab": 36})
            await shell.js("document.querySelector('.side .tool[aria-label=\"Подключить организацию\"]').click()")
            await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Назад')?.click()")
            await shell.wait("document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'", 5)


async def run_scale(scale, dark_too):
    reset_sessions()
    app = App(scale=scale, port=9370, inspect=9371)
    tag = f"s{int(scale * 100)}"
    try:
        await app.start()
        shell = await app.page("app://shell/index.html")
        themes = ["light", "dark"] if dark_too else ["light"]
        await first_run(app, shell, tag, themes)
        await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')).click()")
        await shell.wait("!!document.querySelector('#srv')", 5)
        await probe_in_shell(shell, A_URL)
        pa = await app.page(A_URL)
        await login(pa, "alice", "alice-pass")
        await shell.js("document.querySelector('.side .tool[aria-label=\"Подключить организацию\"]').click()")
        await shell.wait("!!document.querySelector('#srv')", 10)
        await shell.js("document.querySelector('#nm').value = 'Филиал «Северо-Западный» с очень длинным названием'")
        await probe_in_shell(shell, B_URL)
        pb = await app.page(B_URL)
        await login(pb, "bob", "bob-pass")
        await pb.js("document.querySelector('.cookie-note .btn.primary')?.click()")
        await app.m("pg.orgs.activate(pg.orgs.list[0].rec.id); return true;")
        await pa.js("document.querySelector('.cookie-note .btn.primary')?.click()")
        await shell.js("window.pg.call('shell:settings', { theme: 'system' })")
        for theme in themes:
            for p in (pa, pb):
                await p.js(f"document.documentElement.setAttribute('data-theme', '{theme}')")
            await asyncio.sleep(0.4)
            for (w, h) in SIZES:
                if w * scale > SCREEN[0] or h * scale > SCREEN[1]:
                    continue
                await app.m(f"pg.main.win.setBounds({{ x: 0, y: 0, width: {w}, height: {h} }}); pg.main.showPage('org'); return true;")
                await shell.js("window.dispatchEvent(new Event('focus'))")
                await asyncio.sleep(0.8)
                key = f"{tag}-{theme}-{w}x{h}"
                await app.shot_window(f"v-{key}-org")
                judge(f"{key}: страница организации", await layout(pa))
                judge(f"{key}: боковая панель", await layout(shell, SHELL_SEL), {"tile": 40})
                if (w, h) in ((1366, 768), (800, 520)):
                    await shell.js("document.querySelector('.side .tool[aria-label=\"Настройки\"]').click()")
                    for t in ("general", "orgs", "updates", "diag"):
                        await shell.js(f"document.querySelector('#tab-{t}').click()")
                        await asyncio.sleep(0.8 if t == "diag" else 0.3)
                        await app.shot_window(f"v-{key}-settings-{t}")
                        judge(f"{key}: настройки «{t}»", await layout(shell, SHELL_SEL), {"btn": 36, "input": 36, "seg": 36, "tab": 36})
                    await shell.js("document.querySelector('.side .tool[aria-label=\"Подключить организацию\"]').click()")
                    await asyncio.sleep(0.4)
                    await app.shot_window(f"v-{key}-add")
                    judge(f"{key}: мастер подключения", await layout(shell, SHELL_SEL), {"btn": 36, "input": 36})
                    await shell.js("document.querySelector('.side .org').click()")
                    await asyncio.sleep(0.4)
                    await pa.js("document.querySelector('.topnav a[href=\"/\"]') && [...document.querySelectorAll('button')].find(b => /Чаты/.test(b.textContent))?.click()")
                    await asyncio.sleep(1.2)
                    await app.shot_window(f"v-{key}-chats")
                    judge(f"{key}: окно чатов", await layout(pa))
                    await pa.js("document.querySelector('.chat-win [aria-label=\"Закрыть\"]')?.click()")
    finally:
        await app.stop(keep_data=False)


async def main():
    scales = [float(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "1,1.25,1.5,1.75,2").split(",")]
    for i, s in enumerate(scales):
        await run_scale(s, dark_too=s in (1.0, 1.5))
    failed = [n for n, ok, _ in results if not ok]
    print(f"ИТОГО: {len(results) - len(failed)} из {len(results)}; не прошли: {len(failed)}")
    for n, ok, extra in results:
        if not ok:
            print(" -", n, "|", extra)
    sys.exit(1 if failed else 0)


asyncio.run(main())
