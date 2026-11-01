"""Сценарий: фон страницы входа (components/LoginScenes.tsx).

Проверяется: на компьютере — четыре сцены, через 10 с сцена сменяется плавно (прозрачность проходит промежуточные значения); фон не перехватывает
нажатия — поле логина работает; во вкладке в фоне движение стоит, сцена не меняется; «уменьшить движение» — статичная картинка без анимаций;
телефон (касания, 390 px) — упрощённый статичный фон без движущихся деталей; сужение окна переключает режим без перезагрузки; смена темы перекрашивает фон;
ошибок страницы нет. Safari, Firefox и настоящая видеокарта эмуляцией не заменяются.

Запуск на стенде: PEREGOVORKA_URL=http://127.0.0.1:5180 BROWSER_PATH=… python3 tests/live/scenario_login_scenes.py"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

STATE = """(() => { const r = document.querySelector('.lscn'); if (!r) return null;
  const sc = [...r.querySelectorAll('.ls-scene')];
  return { cls: r.className, on: sc.findIndex((s) => s.classList.contains('on')), n: sc.length, sprites: r.querySelectorAll('.ls-sprite').length,
           running: [...r.querySelectorAll('.ls-scene.live .ls-float')].some((e) => getComputedStyle(e).animationName !== 'none' && getComputedStyle(e).animationPlayState === 'running'),
           anims: r.getAnimations ? r.getAnimations({ subtree: true }).filter((a) => a.playState === 'running').length : -1,
           card: getComputedStyle(r.querySelector('.k-card') || r).fill }; })()"""


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
        st = await A.js(STATE)
        S.check("компьютер: четыре сцены, видна первая, детали движутся", st and st["n"] == 4 and st["on"] == 0 and st["anims"] > 0, str(st))
        fades = await A.js("""(async () => { const out = new Set(); const t0 = performance.now();
          while (performance.now() - t0 < 13000) { const o = [...document.querySelectorAll('.ls-scene')].map((s) => +getComputedStyle(s).opacity);
            o.forEach((v) => { if (v > 0.05 && v < 0.95) out.add(v.toFixed(1)); }); await new Promise((r) => setTimeout(r, 150)); }
          return [...out].length; })()""")
        st = await A.js(STATE)
        S.check("через 10 с — следующая сцена", st["on"] == 1, str(st["on"]))
        S.check("смена сцен плавная (прозрачность проходит промежуточные значения)", fades >= 3, f"{fades} промежуточных значений")
        await A.js("document.querySelector('input')?.focus()")
        await A.send("Input.dispatchMouseEvent", type="mousePressed", x=720, y=342, button="left", clickCount=1)
        await A.send("Input.dispatchMouseEvent", type="mouseReleased", x=720, y=342, button="left", clickCount=1)
        await A.send("Input.insertText", text="ivanov")
        S.check("фон не мешает форме: в поле логина можно ввести текст", (await A.js("document.querySelector('input')?.value")) == "ivanov")
        # вкладка в фоне
        await A.js("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
        await asyncio.sleep(0.5)
        before = (await A.js(STATE))["on"]
        await asyncio.sleep(11)
        st = await A.js(STATE)
        S.check("вкладка в фоне: движение стоит, сцена не меняется", "paused" in st["cls"] and st["on"] == before and st["anims"] == 0, str(st))
        await A.js("Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange'))")
        # смена темы перекрашивает фон
        c0 = (await A.js(STATE))["card"]
        await A.js("localStorage.setItem('pg:theme', 'amber'); location.reload()")
        await asyncio.sleep(3)
        c1 = (await A.js(STATE))["card"]
        S.check("тема «Янтарная» перекрашивает фон", c0 != c1, f"{c0} → {c1}")
        await A.js("localStorage.setItem('pg:theme', 'dark'); location.reload()")
        await asyncio.sleep(3)
        st = await A.js(STATE)
        S.check("тёмная тема: фон есть и приглушён", st and st["n"] == 4 and (await A.js("getComputedStyle(document.querySelector('.lscn')).getPropertyValue('--ls-k').trim()")) == ".72")
        await A.js("localStorage.removeItem('pg:theme')")
        # «уменьшить движение»
        await A.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        await A.js("location.reload()"); await asyncio.sleep(3)
        st = await A.js(STATE)
        S.check("«уменьшить движение»: одна статичная сцена, ни одной анимации", "still" in st["cls"] and st["n"] == 1 and st["anims"] == 0, str(st))
        await A.send("Emulation.setEmulatedMedia", features=[])
        # телефон
        await A.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=3, mobile=True)
        await A.send("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
        await A.js("location.reload()"); await asyncio.sleep(3)
        st = await A.js(STATE)
        S.check("телефон: упрощённый статичный фон без движущихся деталей", st and "mobile" in st["cls"] and st["sprites"] == 0 and st["anims"] == 0, str(st))
        S.check("телефон: горизонтальной прокрутки нет", not await A.js("document.documentElement.scrollWidth > innerWidth + 1"))
        # сужение окна без перезагрузки
        await A.send("Emulation.setTouchEmulationEnabled", enabled=False)
        await A.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
        await A.js("location.reload()"); await asyncio.sleep(3)
        await A.send("Emulation.setDeviceMetricsOverride", width=700, height=900, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(1)
        narrow = (await A.js(STATE))["cls"]
        await A.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(1)
        wide = (await A.js(STATE))
        S.check("сужение окна переключает фон на упрощённый и обратно без перезагрузки", "mobile" in narrow and wide["n"] == 4, f"{narrow} / {wide['cls']}")
        st = await A.state()
        S.check("ошибок страницы нет", not st["errs"], str(st["errs"]))
    finally:
        await A.close()
    ok = [r for r in S.results if r[1]]
    print(f"ИТОГО: {len(ok)} из {len(S.results)}; не прошли: {[r[0] for r in S.results if not r[1]]}")


if __name__ == "__main__":
    asyncio.run(main())
