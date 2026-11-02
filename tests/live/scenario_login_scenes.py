"""Сценарий: фон страницы входа (components/LoginScenes.tsx) — одна композиция «волны и орбиты».

Проверяется: на компьютере — волны, орбиты, дуга, сетка; ни одного изображения людей; детали движутся (точка на орбите меняет положение, волны текут);
фон не перехватывает нажатия — поле логина работает; подпись с версией видна поверх фона; во вкладке в фоне движение стоит; «уменьшить движение» —
та же картинка без анимаций; телефон (касания, 390 px) — упрощённый неподвижный фон без сетки, без горизонтальной прокрутки; сужение окна переключает
режим без перезагрузки; смена темы перекрашивает фон; тёмная тема приглушена; снимки на 1280×720, 1440×900, 1920×1080, 2560×1080, планшете и телефоне;
ошибок страницы нет. Safari, Firefox и настоящая видеокарта эмуляцией не заменяются.

Запуск на стенде: PEREGOVORKA_URL=http://127.0.0.1:5180 BROWSER_PATH=… python3 tests/live/scenario_login_scenes.py"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

STATE = """(() => { const r = document.querySelector('.lscn'); if (!r) return null;
  return { cls: r.className, waves: r.querySelectorAll('.lb-wave').length, orbits: r.querySelectorAll('.lb-orbits .lb-orbit').length, arc: !!r.querySelector('.lb-arc'),
           mesh: !!r.querySelector('.lb-mesh'), people: r.querySelectorAll('.k-person, .ls-sprite, .k-skin, .k-hair').length,
           anims: r.getAnimations ? r.getAnimations({ subtree: true }).filter((a) => a.playState === 'running').length : -1,
           paint: getComputedStyle(r).backgroundImage.slice(0, 400) }; })()"""
DOT = "(() => { const b = document.querySelector('.lb-run .lb-dot')?.getBoundingClientRect(); return b ? [Math.round(b.x), Math.round(b.y)] : null; })()"
FRAMES = """(async () => { const d = []; let last = 0; await new Promise((res) => { const t0 = performance.now();
  const f = (now) => { if (last) d.push(now - last); last = now; if (now - t0 < 3000) requestAnimationFrame(f); else res(); }; requestAnimationFrame(f); });
  d.sort((a, b) => a - b); return Math.round(d[Math.floor(d.length / 2)] * 10) / 10; })()"""


async def size(A, w, h, mobile=False, scale=1):
    await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=scale, mobile=mobile)


class Anon(S.Client):
    """Без входа: страница входа и есть предмет проверки."""
    async def start(self):
        import subprocess, tempfile, urllib.request, json, time, websockets
        self.profile = tempfile.mkdtemp(prefix="edge-ls-")
        self.proc = subprocess.Popen([S.EDGE, "--headless=new", "--disable-gpu", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}", "--window-size=1440,900",
                                      *os.environ.get("BROWSER_FLAGS", "").split(), "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try: urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read(); break
            except Exception: time.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")))
        self.ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None, close_timeout=2)
        for d in ("Page", "Runtime"): await self.send(d + ".enable")
        await self.send("Page.addScriptToEvaluateOnNewDocument", source=S.INIT)
        await self.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)


async def main():
    A = Anon("login", "", "", 9541)
    try:
        await A.start()
        await A.goto("/", 3.0)
        await A.js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        st = await A.js(STATE)
        S.check("компьютер: волны, орбиты, дуга и сетка — одна композиция", st and st["waves"] == 4 and st["orbits"] >= 3 and st["arc"] and st["mesh"], str(st)[:200])
        S.check("изображений людей нет", st["people"] == 0)
        S.check("детали движутся", "live" in st["cls"] and st["anims"] > 10, f"{st['anims']} анимаций")
        d0 = await A.js(DOT); await asyncio.sleep(2.5); d1 = await A.js(DOT)
        S.check("световая точка идёт по орбите", d0 and d1 and d0 != d1, f"{d0} → {d1}")
        await asyncio.sleep(8)                    # регулятор замеряет плавность через 3 с после открытия
        lv = await A.js("document.querySelector('.lscn').className")
        med = await A.js(FRAMES)
        print(f"  через 10 с: {lv.strip()}, медиана интервала кадров {med} мс (headless без видеокарты)")
        S.check("регулятор: движение плавное, иначе облегчено или остановлено", ("lv-0" in lv and med <= 30) or "lv-1" in lv or "lv-2" in lv, f"{lv} {med} мс")
        for w, h in ((1440, 900), (1920, 1080), (2560, 1080), (1280, 720)):
            await size(A, w, h); await asyncio.sleep(1.2); await A.shot(f"login-{w}x{h}")
            S.check(f"{w}×{h}: подпись с версией видна поверх фона", await A.js("(() => { const f = document.querySelector('.login-foot'); const b = f.getBoundingClientRect(); return b.bottom <= innerHeight + 1 && document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)?.closest('.login-foot') === f; })()"))
            S.check(f"{w}×{h}: горизонтальной прокрутки нет", not await A.js("document.documentElement.scrollWidth > innerWidth + 1"))
        await size(A, 1440, 900); await asyncio.sleep(0.5)
        await A.js("document.querySelector('input')?.focus()")
        await A.send("Input.dispatchMouseEvent", type="mousePressed", x=720, y=342, button="left", clickCount=1)
        await A.send("Input.dispatchMouseEvent", type="mouseReleased", x=720, y=342, button="left", clickCount=1)
        await A.send("Input.insertText", text="ivanov")
        S.check("фон не мешает форме: в поле логина можно ввести текст", (await A.js("document.querySelector('input')?.value")) == "ivanov")
        # вкладка в фоне
        await A.js("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
        await asyncio.sleep(0.5)
        d0 = await A.js(DOT); await asyncio.sleep(2); d1 = await A.js(DOT)
        st = await A.js(STATE)
        S.check("вкладка в фоне: движение стоит", "paused" in st["cls"] and st["anims"] == 0 and d0 == d1, f"{st['cls']} {st['anims']} {d0} {d1}")
        await A.js("Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange'))")
        # смена темы перекрашивает фон
        c0 = (await A.js(STATE))["paint"]
        await A.js("localStorage.setItem('pg:theme', 'amber'); location.reload()")
        await asyncio.sleep(3)
        c1 = (await A.js(STATE))["paint"]
        S.check("тема «Янтарная» перекрашивает фон", c0 != c1)
        await A.js("localStorage.setItem('pg:theme', 'dark'); location.reload()")
        await asyncio.sleep(3)
        st = await A.js(STATE)
        S.check("тёмная тема: фон есть и приглушён", st and st["waves"] == 4 and (await A.js("getComputedStyle(document.querySelector('.lscn')).getPropertyValue('--ls-k').trim()")) == ".72")
        await A.shot("login-dark-1440x900")
        await A.js("localStorage.removeItem('pg:theme')")
        # «уменьшить движение»
        await A.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        await A.js("location.reload()"); await asyncio.sleep(3)
        st = await A.js(STATE)
        S.check("«уменьшить движение»: та же картинка, ни одной анимации", "still" in st["cls"] and st["waves"] == 4 and st["anims"] == 0, str(st)[:160])
        await A.send("Emulation.setEmulatedMedia", features=[])
        # планшет и телефон
        await A.send("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
        for (w, h, name) in ((1024, 768, "tablet"), (390, 844, "phone")):
            await size(A, w, h, mobile=True, scale=2)
            await A.js("location.reload()"); await asyncio.sleep(3)
            st = await A.js(STATE)
            S.check(f"{name}: упрощённый неподвижный фон без сетки", st and "mobile" in st["cls"] and not st["mesh"] and st["anims"] == 0 and st["waves"] == 4, str(st)[:160])
            S.check(f"{name}: горизонтальной прокрутки нет", not await A.js("document.documentElement.scrollWidth > innerWidth + 1"))
            await A.shot(f"login-{name}")
        # сужение окна без перезагрузки
        await A.send("Emulation.setTouchEmulationEnabled", enabled=False)
        await size(A, 1440, 900)
        await A.js("location.reload()"); await asyncio.sleep(3)
        await size(A, 700, 900); await asyncio.sleep(1)
        narrow = (await A.js(STATE))["cls"]
        await size(A, 1440, 900); await asyncio.sleep(1)
        wide = (await A.js(STATE))
        S.check("сужение окна переключает фон на упрощённый и обратно без перезагрузки", "mobile" in narrow and "live" in wide["cls"] and wide["mesh"], f"{narrow} / {wide['cls']}")
        st = await A.state()
        S.check("ошибок страницы нет", not st["errs"], str(st["errs"]))
    finally:
        await A.close()
    ok = [r for r in S.results if r[1]]
    print(f"ИТОГО: {len(ok)} из {len(S.results)}; не прошли: {[r[0] for r in S.results if not r[1]]}")

if __name__ == "__main__":
    asyncio.run(main())
