"""Управление настоящим приложением Peregovorka для Windows (desktop/, собранным `node build.mjs`) в испытаниях: страницы — по CDP, главный процесс — через
инспектор Node (`globalThis.__pg`, доступен только в запуске из исходников с ключом --e2e). Под Linux нужен X-сервер (DISPLAY), например Xvfb.

Не часть CI: нужен живой стенд (tests/live/README.md)."""
import asyncio, base64, json, os, shutil, subprocess, tempfile, time, urllib.request
import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESKTOP = os.path.join(ROOT, "desktop")
ELECTRON = os.path.join(DESKTOP, "node_modules", "electron", "dist", "electron")
OUT = os.environ.get("DESKTOP_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "desktop"))
os.makedirs(OUT, exist_ok=True)
results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    print(("OK   " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)


class Cdp:
    def __init__(self, ws):
        self.ws, self.n, self.events = ws, 0, []

    @classmethod
    async def connect(cls, url):
        return cls(await websockets.connect(url, max_size=120_000_000, ping_interval=None, close_timeout=2))

    async def send(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), 60))
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            self.events.append(m)

    async def js(self, expr, await_promise=True):
        r = await self.send("Runtime.evaluate", expression=expr, awaitPromise=await_promise, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(str(r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"])[:500])
        return r.get("result", {}).get("value")

    async def wait(self, expr, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if await self.js(expr):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        return False

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass


class App:
    """Один запуск приложения со своим каталогом данных (тот же каталог при повторном запуске — проверка сохранения входа и положения окна)."""

    def __init__(self, user_data=None, args=(), env=None, port=9340, inspect=9341, scale=None):
        self.user_data = user_data or tempfile.mkdtemp(prefix="pg-desktop-")
        self.args, self.env, self.port, self.inspect, self.scale = list(args), dict(env or {}), port, inspect, scale
        self.main = None
        self.pages = {}
        self.seen = set()

    async def start(self):
        env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
        env.update({"PEREGOVORKA_USER_DATA": self.user_data, **self.env})
        flags = ["--no-sandbox", "--e2e", "--allow-http-loopback", f"--remote-debugging-port={self.port}", f"--inspect={self.inspect}", "--password-store=basic",
                 "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"]
        if self.scale:
            flags.append(f"--force-device-scale-factor={self.scale}")
        self.proc = subprocess.Popen([ELECTRON, *flags, DESKTOP, *self.args], env=env, stdout=open(os.path.join(OUT, "electron.log"), "ab"), stderr=subprocess.STDOUT)
        for _ in range(80):
            try:
                lst = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.inspect}/json/list"))
                if lst:
                    self.main = await Cdp.connect(lst[0]["webSocketDebuggerUrl"])
                    break
            except Exception:
                await asyncio.sleep(0.25)
        assert self.main, "инспектор главного процесса не ответил"
        await self.main.send("Runtime.enable")
        ok = await self.main.wait("typeof globalThis.__pg === 'object'", 30)
        assert ok, "приложение не запустилось (нет __pg)"

    async def m(self, expr):
        """Выражение в главном процессе (оборачивается в async-функцию; результат — JSON)."""
        return await self.main.js(f"(async () => {{ const pg = globalThis.__pg; return JSON.parse(JSON.stringify(await (async () => {{ {expr} }})() ?? null)); }})()")

    def targets(self):
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list"))

    async def page(self, prefix, timeout=20, fresh=False):
        """CDP-сессия страницы, адрес которой начинается с prefix (оболочка app://shell/…, страница организации http://…).
        fresh=True — только окно, которого ещё не было (окно выбора экрана и индикатор создаются заново, закрытое может ещё числиться в списке)."""
        end = time.time() + timeout
        while time.time() < end:
            for t in self.targets():
                if t.get("type") == "page" and t.get("url", "").startswith(prefix):
                    key = t["id"]
                    if fresh and key in self.seen:
                        continue
                    self.seen.add(key)
                    if key not in self.pages:
                        c = await Cdp.connect(t["webSocketDebuggerUrl"])
                        await c.send("Runtime.enable")
                        await c.send("Page.enable")
                        self.pages[key] = c
                    return self.pages[key]
            await asyncio.sleep(0.25)
        raise RuntimeError(f"нет страницы {prefix}: {[t.get('url') for t in self.targets()]}")

    async def shot_window(self, name):
        """Снимок окна целиком, как его видит человек (боковая панель оболочки + страница организации — разные поверхности, поэтому снимается экран X-сервера)."""
        from PIL import ImageGrab
        await asyncio.sleep(0.3)
        b = await self.m("const b = pg.main.win.getContentBounds(); const { screen } = process.mainModule.require('electron'); return { ...b, s: screen.getPrimaryDisplay().scaleFactor };")
        s = b["s"]
        img = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY", ":99")).crop((round(b["x"] * s), round(b["y"] * s), round((b["x"] + b["width"]) * s), round((b["y"] + b["height"]) * s)))
        path = os.path.join(OUT, f"{name}.png")
        img.save(path)
        return path

    async def shot_page(self, cdp, name):
        r = await cdp.send("Page.captureScreenshot", format="png")
        path = os.path.join(OUT, f"{name}.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path

    async def stop(self, keep_data=True):
        try:
            await self.m("setTimeout(() => pg.quit(), 50); return true;")
        except Exception:
            pass
        for c in self.pages.values():
            await c.close()
        self.pages = {}
        if self.main:
            await self.main.close()
        try:
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
        if not keep_data:
            shutil.rmtree(self.user_data, ignore_errors=True)

    def log(self):
        try:
            with open(os.path.join(self.user_data, "logs", "main.log"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""
