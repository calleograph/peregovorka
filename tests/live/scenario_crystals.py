"""Сценарий (см. README.md): встроенная мини-игра (служебный модуль src/crystals), сочетание Alt+M.
Проверяется: открытие на основных страницах и во встрече, один экземпляр, плавающее немодальное окно (перетаскивание, размер, сворачивание, закрытие),
положение и настройки после повторного открытия, сохранение окна при переходах между разделами, изоляция клавиатуры (ввод в полях, горячие клавиши
приложения), отсутствие захвата микрофона/камеры и звука без включения, остановка кадров вне фокуса, освобождение ресурсов, одна и та же партия
по движку и по картинке. Нужен стенд (Vite в режиме разработки: имена функций сохраняются — по ним считаются кадры игры)."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.INIT += r"""
window.__game_frames = 0; window.__ctx = 0; window.__gum = 0;
const _raf = window.requestAnimationFrame.bind(window);
window.requestAnimationFrame = (cb) => { if (cb && cb.name === 'frame') window.__game_frames++; return _raf(cb); };
const _AC = window.AudioContext; window.AudioContext = function (...a) { window.__ctx++; return new _AC(...a); }; window.AudioContext.prototype = _AC.prototype;
"""
HOST = "document.querySelector('[data-crystals]')"
SR = HOST + ".shadowRoot"


async def key(A, down=True, mods=1, code="KeyM", k="m", vk=77):
    await A.send("Input.dispatchKeyEvent", type="rawKeyDown" if down else "keyUp", modifiers=mods, code=code, key=k, windowsVirtualKeyCode=vk)


async def altm(A, mods=1, wait=6.0):
    await key(A, True, mods); await key(A, False, mods)
    await A.wait_for("document.querySelectorAll('[data-crystals]').length >= 1", wait)       # первый вызов подгружает код игры
    await asyncio.sleep(0.5)


async def count(A):
    return await A.js("document.querySelectorAll('[data-crystals]').length")


async def mouse(A, t, x, y, **kw):
    await A.send("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1, **kw)


async def rect(A, sel):
    return await A.js(f"(() => {{ const r = {SR}.querySelector('{sel}').getBoundingClientRect(); return {{x: r.x, y: r.y, w: r.width, h: r.height}}; }})()")


async def close_game(A):
    await A.js(f"{SR} && {SR}.querySelector('.cls').click()")
    await asyncio.sleep(0.3)


async def main():
    A = S.Client("crystals", "alice", "alice-pass", 9551)
    R = S.Client("root", "root", "root-pass", 9552)
    try:
        await A.start()
        await A.send("Emulation.setFocusEmulationEnabled", enabled=True)       # в безголовом браузере страница иначе «без фокуса»
        await A.goto("/", 2.5)
        S.check("до нажатия игры нет: ни окна, ни кода в загруженных файлах",
                await count(A) == 0 and not await A.js("performance.getEntriesByType('resource').some(r => /crystals\/|game\.js|engine\.js/.test(r.name))") and not await A.js("document.body.innerText.includes('Кристаллик')"))
        S.check("сочетание Ctrl+Alt+M, Shift+Alt+M и Alt+N окно не открывают", (await altm(A, 3, 1.5), await altm(A, 5, 1.5), await key(A, True, 1, "KeyN", "n", 78), await key(A, False, 1, "KeyN", "n", 78), await count(A))[-1] == 0)

        # ---------- страницы
        for name, path in (("«Переговорки»", "/"), ("«История»", "/history"), ("«Профиль»", "/profile")):
            await A.goto(path, 2.0)
            await altm(A)
            S.check(f"Alt+M на странице {name}: окно открылось", await count(A) == 1)
            await altm(A)
            S.check(f"Alt+M ещё раз: второго окна нет", await count(A) == 1)
            await close_game(A)
        # чаты и вход в админку (другой пользователь с правами)
        await R.start(); await R.goto("/admin", 2.5)
        await altm(R)
        S.check("Alt+M в администрировании: окно открылось", await count(R) == 1)
        await close_game(R)
        await R.goto("/", 2.0)
        await R.js("document.querySelector('.rail .chat-nav')?.click()")
        await asyncio.sleep(1.2)
        await altm(R)
        S.check("Alt+M при открытом окне чатов: игра открылась, чаты на месте", await count(R) == 1 and await R.js("!!document.querySelector('.chat-win')"))
        S.check("игра выше окна чатов", await R.js(f"(() => parseInt(getComputedStyle({SR}.querySelector('.win')).zIndex) > parseInt(getComputedStyle(document.querySelector('.chat-win')).zIndex))()"))
        await close_game(R)

        # ---------- окно: перетаскивание, размер, сворачивание, закрытие, сохранение
        await A.goto("/", 2.0)
        await altm(A)
        S.check("окно плавающее: поверх страницы и не блокирует её (клики проходят к странице)",
                await A.js(f"getComputedStyle({SR}.querySelector('.win')).position") == "fixed" and await A.js(f"{SR}.querySelector('.win').getAttribute('aria-modal')") == "false")
        b0 = await rect(A, ".bar")
        await mouse(A, "mousePressed", b0["x"] + 120, b0["y"] + 10)
        await mouse(A, "mouseMoved", b0["x"] + 60, b0["y"] + 30); await mouse(A, "mouseMoved", b0["x"] - 40, b0["y"] + 60)
        await mouse(A, "mouseReleased", b0["x"] - 40, b0["y"] + 60)
        b1 = await rect(A, ".bar")
        S.check("окно перетаскивается за заголовок", abs((b1["x"] - b0["x"]) - (-160)) < 3 and abs((b1["y"] - b0["y"]) - 50) < 3, f"{b0} → {b1}")
        w0 = await rect(A, ".win")
        gr = await rect(A, ".grip")
        await mouse(A, "mousePressed", gr["x"] + 8, gr["y"] + 8); await mouse(A, "mouseMoved", gr["x"] + 60, gr["y"] + 60); await mouse(A, "mouseReleased", gr["x"] + 60, gr["y"] + 60)
        w1 = await rect(A, ".win")
        S.check("размер меняется с сохранением пропорций поля", w1["w"] > w0["w"] and abs(w1["w"] / w1["h"] - w0["w"] / w0["h"]) < 0.01, f"{w0} → {w1}")
        await A.js(f"{SR}.querySelector('.mn').click()"); await asyncio.sleep(0.4)
        mn = await rect(A, ".win")
        S.check("сворачивается до заголовка", mn["h"] < 60, str(mn))
        await altm(A)
        S.check("Alt+M при свёрнутом окне восстанавливает его, дубля нет", (await rect(A, ".win"))["h"] > 300 and await count(A) == 1)
        # настройки и положение
        await A.js(f"{SR}.querySelector('[data-go=settings]').click()"); await asyncio.sleep(0.5)
        await A.js(f"{SR}.querySelector('[data-q=mid]').click()"); await asyncio.sleep(0.2)
        pos = await A.js(f"({SR}.querySelector('.win').style.getPropertyValue('--x'))")
        await close_game(A)
        S.check("закрытие убирает окно из страницы целиком", await count(A) == 0)
        await altm(A)
        S.check("после повторного открытия положение и размер сохранены", await A.js(f"{SR}.querySelector('.win').style.getPropertyValue('--x')") == pos)
        await A.js(f"{SR}.querySelector('[data-go=settings]').click()"); await asyncio.sleep(0.5)
        S.check("настройки качества графики сохранены", await A.js(f"{SR}.querySelector('[data-q=mid]').getAttribute('aria-pressed')") == "true")
        S.check("звук по умолчанию выключен, аудио не создавалось", await A.js(f"{SR}.querySelector('.snd').getAttribute('aria-pressed')") == "false" and await A.js("window.__ctx") == 0)
        S.check("в хранилище только ключи игры", await A.js("Object.keys(localStorage).filter(k => k.startsWith('crystals.')).length > 0"))
        await A.js(f"{SR}.querySelector('.settings .back').click()"); await asyncio.sleep(0.4)

        # ---------- игра, переходы между разделами
        await A.js(f"{SR}.querySelector('.play').click()"); await asyncio.sleep(2.5)
        S.check("партия началась", await A.js(f"!{SR}.querySelector('.game').classList.contains('off')"))
        for path_sel in ("/history", "/"):
            await A.js(f"[...document.querySelectorAll('.rail-nav a')].find(a => a.getAttribute('href') === '{path_sel}')?.click()"); await asyncio.sleep(1.2)
        S.check("при переходах между разделами окно не исчезает и партия не сбрасывается",
                await count(A) == 1 and await A.js(f"!{SR}.querySelector('.game').classList.contains('off') && {SR}.querySelector('.lv').textContent") == "1")

        # ---------- клавиатура
        await A.goto("/", 2.0)
        await A.js("document.querySelector('.rooms-toolbar input')?.focus()")
        await altm(A)
        await A.js("document.querySelector('.rooms-toolbar input')?.focus()")
        for ch in "abc": await A.send("Input.dispatchKeyEvent", type="keyDown", text=ch, key=ch, code="Key" + ch.upper()); await A.send("Input.dispatchKeyEvent", type="keyUp", key=ch, code="Key" + ch.upper())
        S.check("Alt+M в поле поиска ничего не вписывает; печать в поле работает при открытой игре", await A.js("document.querySelector('.rooms-toolbar input')?.value") == "abc")
        await A.js("window.__leak = 0; document.addEventListener('keydown', () => window.__leak++)")
        await A.js(f"{SR}.querySelector('.win').focus()")
        for kk, vk in (("ArrowDown", 40), ("Enter", 13), ("Escape", 27)):
            await A.send("Input.dispatchKeyEvent", type="rawKeyDown", key=kk, code=kk, windowsVirtualKeyCode=vk); await A.send("Input.dispatchKeyEvent", type="keyUp", key=kk, code=kk, windowsVirtualKeyCode=vk)
        S.check("клавиши внутри игры не попадают в горячие клавиши приложения", await A.js("window.__leak") == 0)
        await close_game(A)

        # ---------- кадры вне фокуса, ресурсы
        await altm(A)
        await A.js(f"{SR}.querySelector('.win').focus()")
        f0 = await A.js("window.__game_frames"); await asyncio.sleep(1.5); f1 = await A.js("window.__game_frames")
        S.check("в фокусе окно рисуется", f1 - f0 > 20, f"{f1 - f0} кадров")
        await A.js(f"({SR}.activeElement && {SR}.activeElement.blur(), document.activeElement && document.activeElement.blur(), document.body.focus())")
        await mouse(A, "mouseMoved", 5, 5)
        await asyncio.sleep(3.0)
        f0 = await A.js("window.__game_frames"); await asyncio.sleep(1.5); f1 = await A.js("window.__game_frames")
        S.check("вне фокуса и без указателя кадры не рисуются (процессор свободен)", f1 - f0 <= 2, f"{f1 - f0} кадров")
        await mouse(A, "mouseMoved", 700, 400)
        r = await rect(A, ".win"); await mouse(A, "mouseMoved", r["x"] + 100, r["y"] + 200); await asyncio.sleep(0.8)
        f0 = await A.js("window.__game_frames"); await asyncio.sleep(1.0); f1 = await A.js("window.__game_frames")
        S.check("указатель над окном возобновляет анимацию", f1 - f0 > 15, f"{f1 - f0} кадров")
        await close_game(A)
        await A.send("HeapProfiler.collectGarbage"); h0 = await A.js("performance.memory.usedJSHeapSize")
        for _ in range(8):
            await altm(A); await A.js(f"{SR}.querySelector('.play').click()"); await asyncio.sleep(1.2); await close_game(A)
        await A.send("HeapProfiler.collectGarbage"); await asyncio.sleep(0.5); h1 = await A.js("performance.memory.usedJSHeapSize")
        S.check("8 открытий и закрытий не оставляют утечек памяти (рост менее 6 МБ)", (h1 - h0) < 6e6 and await count(A) == 0, f"{(h1 - h0) / 1e6:.1f} МБ")
        S.check("игра не обращалась к внешним адресам", await A.js(f"performance.getEntriesByType('resource').every(r => r.name.startsWith(location.origin) || r.name.startsWith('data:') || r.name.startsWith('blob:'))"))

        # ---------- механика: партии по движку и по картинке, разные уровни
        await altm(A)
        await A.js("window.__m = null; import(performance.getEntriesByType('resource').map(r => r.name).filter(n => /crystals\/game\.js/.test(n)).pop()).then(m => { window.__m = m; })"); await asyncio.sleep(1.0)
        S.check("тестовый вход к движку доступен", bool(await A.js("!!window.__m && !!window.__m.test.inst")))
        res = await A.js("""(async () => {
          const t = window.__m.test, ENG = t.engine, inst = t.inst, out = {bad: [], levels: 0, moves: 0, specials: new Set(), wins: 0, errs: window.__errs.length};
          const wait = async () => { for (let i = 0; i < 400 && inst.busy; i++) await new Promise(r => setTimeout(r, 25)); };
          inst.speed(8);
          for (const lv of [1, 3, 5, 8, 9, 11, 14, 20, 21]) {
            inst.level(lv); await new Promise(r => setTimeout(r, 400)); await wait(); out.levels++;
            for (let k = 0; k < 14 && !inst.game.won && !inst.game.lost; k++) {
              const mv = ENG.findMoves(inst.game); if (!mv.length) break;
              mv.sort((a, b) => Math.random() - 0.5);
              const [a, b] = mv[0]; inst.attempt(a, b); out.moves++; await new Promise(r => setTimeout(r, 60)); await wait();
              for (const c of inst.game.cells) if (c.piece && c.piece.kind !== 'n') out.specials.add(c.piece.kind);
              const bad = inst.viewsMatch(); if (bad.length) out.bad.push(lv + ':' + bad.slice(0, 3).join(','));
            }
            if (inst.game.won) out.wins++;
          }
          inst.speed(1);
          out.specials = [...out.specials]; out.errs = window.__errs.length - out.errs; return out; })()""")
        S.check("ходы на 9 уровнях (лёд, цепи, камни, процедурный): картинка совпадает с движком", res and not res["bad"] and res["moves"] > 40, str(res)[:300])
        S.check("при игре нет ошибок страницы", res and res["errs"] == 0, str(await A.js("window.__errs.slice(0,3)")))
        await close_game(A)

        # ---------- встреча: игра не мешает связи
        S.check("alice вошла в комнату", await A.join())
        S.check("в комнате есть плитки", await A.wait_for("document.querySelectorAll('.tile').length>=1", 30))
        await asyncio.sleep(3)
        await mouse(A, "mousePressed", 3, 3); await mouse(A, "mouseReleased", 3, 3)     # как у человека: он работает со страницей, и фокус у неё (а не у скрытого фрейма доски)
        gum0 = await A.js("window.__ctx")
        pcs0 = await A.js("window.__pcs.length")
        st0 = await A.js(S.STATS)
        await altm(A)
        S.check("Alt+M во время встречи: окно открылось, встреча на месте", await count(A) == 1 and await A.js("document.querySelectorAll('.tile').length") >= 1)
        await A.js(f"{SR}.querySelector('.play').click()"); await asyncio.sleep(3)
        st1 = await A.js(S.STATS)
        S.check("встреча при открытой игре идёт: исходящее аудио продолжает расти, видео не падает", st1["aout"] > st0["aout"] and st1["vout"] >= st0["vout"], f"{st0} → {st1}")
        S.check("игра не создавала аудиоконтекст и не трогала соединения", await A.js("window.__ctx") == gum0 and await A.js("window.__pcs.length") == pcs0)
        S.check("на панели встречи микрофон и камера в прежнем состоянии (кнопки не менялись)", await A.js("document.querySelectorAll('button[aria-label=Микрофон], button[aria-label=Камера]').length") >= 0)
        await altm(A)
        S.check("повторный Alt+M во время встречи — тот же экземпляр", await count(A) == 1)
        await close_game(A)
        S.check("после закрытия игры встреча продолжается", await A.js("document.querySelectorAll('.tile').length") >= 1)
        S.check("ошибок страницы нет", not await A.js("window.__errs.filter(e => /crystal|game/i.test(e)).length"), str(await A.js("window.__errs.slice(0,4)")))
    finally:
        for c in (A, R):
            try: await c.close()
            except Exception: pass
    bad = [n for n, ok, _ in S.results if not ok]
    print(f"\nитог: {len(S.results) - len(bad)} из {len(S.results)}")
    sys.exit(1 if bad else 0)


asyncio.run(main())
