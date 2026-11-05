"""Испытание диагностики эфира на настоящем эфире: Egress, HLS, RTMP, браузеры зрителей и организатора, искусственные сбои. Не часть CI: нужен тестовый экземпляр с Egress и Docker.
   BASE=http://127.0.0.1:28480 ORIGIN=http://localhost:28480 ADMIN_PW_FILE=… LK_URL=ws://127.0.0.1:27880 PROJECT=pgdoc BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox python run_diag.py ВЫХОДНОЙ_КАТАЛОГ [сбой …]
Сбои только на контейнерах испытательного экземпляра: tc netem в отдельном контейнере в сети нужной службы (NET_ADMIN, без privileged), docker update --cpus, остановка приёмника RTMP, перезапуск служб.
Нагрузка процессора — с nice 19 и ненадолго: чужие процессы на сервере имеют приоритет."""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx
import websockets

HERE = Path(__file__).resolve().parent
BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
ORIGIN = os.environ.get("ORIGIN", "http://localhost:28480")
PROJECT = os.environ.get("PROJECT", "pgdoc")
CHROME = os.environ.get("BROWSER_PATH", "chromium")
QUALITY = os.environ.get("QUALITY", "720p")
OUT = Path(sys.argv[1])
ONLY = set(sys.argv[2:])
OUT.mkdir(parents=True, exist_ok=True)
KEY = "goodkey"
RTMP_NAME = os.environ.get("RTMP_CONTAINER", "pgtest-rtmp64")
results: list[dict] = []


def sh(*a: str, check: bool = False) -> str:
    r = subprocess.run(a, capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        raise RuntimeError(f"{a}: {r.stderr[:300]}")
    return r.stdout.strip()


def api() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers={"Origin": ORIGIN}, timeout=60)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][-1]["id"]})
    r.raise_for_status()
    c.session = c.cookies["vm_session"]
    c.headers["Cookie"] = "vm_session=" + c.session
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


class Page:
    """Отдельный браузер: зритель или организатор. Управление по CDP."""
    def __init__(self, name: str, port: int):
        self.name, self.port, self.n = name, port, 0

    async def start(self, init: str = "") -> "Page":
        self.dir = tempfile.mkdtemp(prefix=f"diag-{self.name}-")
        self.proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.dir}", "--autoplay-policy=no-user-gesture-required", "--mute-audio",
                                      "--window-size=1280,800", *os.environ.get("BROWSER_FLAGS", "").split(), "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read()
                break
            except OSError:
                await asyncio.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")))
        self.ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None)
        for m in ("Page.enable", "Runtime.enable", "Network.enable"):
            await self.send(m)
        if init:
            await self.send("Page.addScriptToEvaluateOnNewDocument", source=init)
        return self

    async def send(self, m: str, **p):
        self.n += 1
        i = self.n
        await self.ws.send(json.dumps({"id": i, "method": m, "params": p}))
        while True:
            r = json.loads(await self.ws.recv())
            if r.get("id") == i:
                return r.get("result", {})

    async def js(self, e: str):
        return (await self.send("Runtime.evaluate", expression=e, awaitPromise=True, returnByValue=True)).get("result", {}).get("value")

    async def goto(self, path: str):
        await self.send("Page.navigate", url=ORIGIN + path)

    async def net(self, *, offline: bool = False, kbps: float = -1, latency: int = 0):
        await self.send("Network.emulateNetworkConditions", offline=offline, latency=latency, downloadThroughput=kbps * 125 if kbps > 0 else -1, uploadThroughput=-1)

    async def shot(self, name: str):
        r = await self.send("Page.captureScreenshot", format="png")
        (OUT / f"{name}.png").write_bytes(base64.b64decode(r["data"]))

    async def close(self):
        await self.ws.close()
        self.proc.terminate()
        self.proc.wait(10)
        shutil.rmtree(self.dir, ignore_errors=True)


class Tc:
    """netem или tbf в сети контейнера службы: отдельный контейнер с NET_ADMIN."""
    def __init__(self, service: str):
        self.target = f"{PROJECT}-{service}-1"
        self.name = f"pgdiag-tc-{service}"

    def __enter__(self):
        sh("docker", "rm", "-f", self.name)
        sh("docker", "run", "-d", "--rm", "--name", self.name, "--cap-add", "NET_ADMIN", "--network", f"container:{self.target}", "pgtc:1", "sleep", "3600", check=True)
        return self

    def add(self, *rule: str):
        sh("docker", "exec", self.name, "tc", "qdisc", "replace", "dev", "eth0", "root", *rule, check=True)

    def __exit__(self, *_):
        sh("docker", "exec", self.name, "tc", "qdisc", "del", "dev", "eth0", "root")
        sh("docker", "rm", "-f", self.name)


class Run:
    def __init__(self):
        self.c = api()
        self.seen: set[int] = set()

    def get(self, path: str) -> dict:
        r = self.c.get(path)
        if r.status_code == 401:             # сеанс мог закрыться (лимит сеансов, перезапуск Redis): только для запросов сценария, браузер организатора остаётся со своим
            self.c = api()
            r = self.c.get(path)
        return r.json()

    def events(self) -> list[dict]:
        return self.get(f"/api/v1/live-diag/{self.bid}/events?limit=1000")["items"]

    def state(self) -> dict:
        return self.get(f"/api/v1/meetings/{self.mid}/live/diag?ring=false&events=false")

    async def wait(self, codes: set[str], timeout: float) -> tuple[dict | None, float]:
        """Ждём новое событие с одним из кодов (или префиксом «код*»)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            for e in self.events():
                if e["id"] not in self.seen and any(e["code"].startswith(c[:-1]) if c.endswith("*") else e["code"] == c for c in codes):
                    self.seen.add(e["id"])
                    return e, round(time.time() - t0, 1)
            await asyncio.sleep(2)
        return None, round(time.time() - t0, 1)

    async def clear(self, timeout: float = 70):
        """Находки ушли и остальные события этого шага просмотрены."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            n = (self.state().get("now") or {}).get("findings") or []
            if not [f for f in n if f["code"] not in ("no_video",)]:
                break
            await asyncio.sleep(3)
        self.seen |= {e["id"] for e in self.events()}

    async def step(self, name: str, want: set[str], timeout: float, act, undo=None, metric=None):
        if ONLY and name not in ONLY:
            return
        print(f"--- {name}", flush=True)
        await self.clear()
        before = self.state().get("now") or {}
        await act()
        ev, took = await self.wait(want, timeout)
        await asyncio.sleep(4)                     # панель успела обновиться
        now = self.state().get("now") or {}
        await self.panel.shot(f"panel-{name}")
        panel = await self.panel.js("document.querySelector('.ld')?.innerText?.slice(0, 500) || ''")
        if undo:
            await undo()
            await asyncio.sleep(float(os.environ.get("RECOVER_S", "20")))        # эфир приходит в норму, прежде чем начнётся следующий сбой
        res = {"name": name, "ok": ev is not None, "code": ev and ev["code"], "level": ev and ev["level"], "message": ev and ev["message"], "cause": ev and ev["cause"], "kind": ev and ev["kind"], "detect_s": took,
               "snapshot": ((ev or {}).get("details") or {}).get("snapshot"), "metric_before": metric(before) if metric else None, "metric_during": metric(now) if metric else None, "panel": panel[:300]}
        results.append(res)
        (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
        print(json.dumps({k: res[k] for k in ("name", "ok", "code", "detect_s", "metric_before", "metric_during")}, ensure_ascii=False), flush=True)


async def main():
    r = Run()
    c = r.c
    prev = c.get("/api/v1/admin/settings/live").json()
    prev = prev.get("values", prev)
    c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3, "allow_private_destinations": True, "cdn_nodes_enabled": False, "diag_enabled": True}).raise_for_status()
    sh("docker", "start", RTMP_NAME)
    sh("docker", "network", "connect", "--alias", "rtmptest", f"{PROJECT}_default", RTMP_NAME)
    slug = f"diag-{time.strftime('%H%M%S')}"
    rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": QUALITY}).json()
    token = lv["view_path"].rsplit("/", 1)[-1]
    d = c.post(f"/api/v1/rooms/{rid}/live/destinations", json={"platform": "custom", "name": "Проверочная площадка", "server_url": "rtmp://rtmptest:1935/live", "key": KEY})
    d.raise_for_status()
    j = c.post(f"/api/v1/rooms/{rid}/join", json={}).json()
    r.mid = j["meeting_id"]
    size = {"720p": "1280x720", "1080p": "1920x1080"}[QUALITY]
    pub = subprocess.Popen([sys.executable, str(HERE / "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"], "--size", size, "--seconds", "7200"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "PUBLISHING" in pub.stdout.readline()
    await asyncio.sleep(4)
    c.post(f"/api/v1/meetings/{r.mid}/live", json={"enabled": True}).raise_for_status()
    viewers: list[Page] = []
    panel = None
    try:
        v = httpx.Client(base_url=BASE, headers={"Origin": ORIGIN}, timeout=30)
        for _ in range(120):
            if v.get(f"/api/v1/live/{token}").json()["status"] == "live":
                break
            await asyncio.sleep(0.5)
        r.bid = r.state()["bid"]
        print("эфир", r.bid, flush=True)
        for i in range(int(os.environ.get("VIEWERS", "4"))):
            vt = v.post(f"/api/v1/live/{token}/viewer", json={"display_name": f"Зритель {i}"}).json()["viewer"]
            p = await Page(f"v{i}", 19330 + i).start(f"sessionStorage.setItem('pg:live:viewer:{token}', '{vt}');")
            await p.goto(f"/live/{token}")
            viewers.append(p)
        r.panel = panel = await Page("org", 19329).start()
        await panel.send("Network.setCookie", name="vm_session", value=c.session, url=ORIGIN)
        await panel.goto(f"/live/{token}")
        vt = v.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Организатор"}).json()["viewer"]
        await panel.js(f"sessionStorage.setItem('pg:live:viewer:{token}', '{vt}')")
        await panel.goto(f"/live/{token}")
        await asyncio.sleep(10)
        await panel.js("""[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click()); document.querySelector('[aria-label="Статистика воспроизведения"]').click()""")
        await asyncio.sleep(45)                    # все зрители прислали первый отчёт
        base = r.state()["now"]
        (OUT / "baseline.json").write_text(json.dumps(base, ensure_ascii=False, indent=1))
        await panel.shot("panel-baseline")
        print("зрителей", base.get("viewer_count"), "отчётов", base["viewers"]["reporting"], flush=True)

        hls = lambda s: {"ready": (s.get("hls") or {}).get("ready_s"), "age": (s.get("hls") or {}).get("age_s"), "load": ((s.get("svc") or {}).get("egress") or {}).get("load"), "cores": ((s.get("svc") or {}).get("egress") or {}).get("cores")}   # noqa: E731
        view = lambda s: {k: (s.get("viewers") or {}).get(k) for k in ("reporting", "bad", "seg_ms", "lat", "buf", "stalls")}                           # noqa: E731

        async def burn():
            sh("docker", "run", "-d", "--rm", "--name", "pgdiag-burn", "alpine:3", "sh", "-c", "for i in $(seq 19); do nice -n 19 sh -c 'while :; do :; done' & done; sleep 50; kill 0")
        await r.step("cpu", {"cpu_high"}, 45, burn, lambda: asyncio.to_thread(sh, "docker", "rm", "-f", "pgdiag-burn"), lambda s: (s.get("host") or {}).get("cpu"))

        with Tc("web") as t:
            async def on():
                t.add("tbf", "rate", "1500kbit", "burst", "32kbit", "latency", "300ms")

            async def off():
                sh("docker", "exec", t.name, "tc", "qdisc", "del", "dev", "eth0", "root")
            await r.step("bandwidth", {"viewers_common"}, 90, on, off, view)

            async def on2():
                t.add("netem", "delay", "900ms")
            await r.step("latency", {"viewers_common"}, 90, on2, off, view)

            async def on3():
                t.add("netem", "loss", "25%")
            await r.step("loss_delivery", {"viewers_common"}, 90, on3, off, view)

        with Tc("livekit") as t:
            async def on4():
                t.add("netem", "loss", "15%")

            async def off4():
                sh("docker", "exec", t.name, "tc", "qdisc", "del", "dev", "eth0", "root")
            await r.step("loss_livekit", {"lk_loss_in", "lk_loss_out"}, 60, on4, off4, lambda s: {k: ((s.get("svc") or {}).get("livekit") or {}).get(k) for k in ("loss_in", "loss_out", "nack_s")})

        async def low():
            free = (r.state()["now"]["host"]["disk"]["free"]) / 1024**3
            c.put("/api/v1/admin/settings/live", json={"min_free_gb": int(free / 1.5)}).raise_for_status()

        async def low_off():
            c.put("/api/v1/admin/settings/live", json={"min_free_gb": 1}).raise_for_status()
        await r.step("disk_low", {"disk_low"}, 40, low, low_off, lambda s: ((s.get("host") or {}).get("disk") or {}).get("free"))

        egress = f"{PROJECT}-livekit-egress-1"

        async def slow():
            sh("docker", "update", "--cpus", "0.4", egress)

        async def fast():
            sh("docker", "update", "--cpus", str(os.cpu_count()), egress)            # «--cpus 0» предел не снимает
        await r.step("hls_slow", {"hls_slow", "hls_stall"}, 70, slow, fast, hls)
        await asyncio.sleep(30)

        async def rtmp_off():
            sh("docker", "stop", "-t", "1", RTMP_NAME)
        await r.step("rtmp_down", {"rtmp_state", "rtmp_*"}, 70, rtmp_off, None, lambda s: s.get("rtmp"))

        async def thin():
            await viewers[0].js("""document.querySelector('[aria-label="Статистика воспроизведения"]').click()""")
            await viewers[0].net(kbps=1200)

        async def thin_off():
            await viewers[0].shot("viewer-thin")
            (OUT / "viewer-thin.txt").write_text(str(await viewers[0].js("document.querySelector('.ld')?.innerText || ''")))
            await viewers[0].net()
        await r.step("one_viewer", {"viewer_single"}, 90, thin, thin_off, view)

        async def drop():
            await viewers[1].net(offline=True)
            await asyncio.sleep(8)
            await viewers[1].net()
        await r.step("reconnect", {"viewer_single"}, 60, drop, None, view)

        async def rs():
            sh("docker", "restart", f"{PROJECT}-redis-1")
        await r.step("restart_redis", {"component_restart"}, 40, rs)

        async def rb():
            sh("docker", "restart", f"{PROJECT}-backend-1")
            await asyncio.sleep(25)
        await r.step("restart_backend", {"collector_resumed"}, 60, rb)
        if not ONLY or "restart_egress" in ONLY:
            r.c = api()
            for p in (panel,):
                await p.send("Network.setCookie", name="vm_session", value=r.c.session, url=ORIGIN)

        async def re():
            sh("docker", "restart", egress)
        await r.step("restart_egress", {"finished_failed", "component_restart", "egress_down"}, 90, re)

        await asyncio.sleep(10)
        for fmt, name in (("json", "export.json"), ("txt", "journal.txt"), ("report", "report.txt")):
            body = r.c.get(f"/api/v1/live-diag/{r.bid}/export?format={fmt}")
            body.raise_for_status()
            (OUT / name).write_bytes(body.content)
            assert f"/{KEY}" not in body.text and KEY not in body.text, f"ключ потока в выгрузке {fmt}"
        print("выгрузки без ключа потока", flush=True)
    finally:
        for p in viewers + ([panel] if panel else []):
            try:
                await p.close()
            except Exception:  # noqa: BLE001
                pass
        pub.terminate()
        sh("docker", "update", "--cpus", str(os.cpu_count()), f"{PROJECT}-livekit-egress-1")
        sh("docker", "rm", "-f", "pgdiag-burn", "pgdiag-tc-web", "pgdiag-tc-livekit")
        sh("docker", "network", "disconnect", f"{PROJECT}_default", RTMP_NAME)
        sh("docker", "stop", "-t", "1", RTMP_NAME)
        try:
            c2 = api()
            c2.post(f"/api/v1/meetings/{r.mid}/live", json={"enabled": False})
            c2.post(f"/api/v1/meetings/{r.mid}/end")
            c2.put("/api/v1/admin/settings/live", json={k: prev[k] for k in ("min_free_gb", "allow_private_destinations", "max_concurrent", "cdn_nodes_enabled") if k in prev})
            (OUT / "room.txt").write_text(rid)
        except Exception as exc:  # noqa: BLE001
            print("уборка:", exc)
    ok = sum(1 for x in results if x["ok"])
    print(f"итог: {ok} из {len(results)}")
    for x in results:
        print(("OK  " if x["ok"] else "НЕТ ") + f"{x['name']:<18} {x['code']} за {x['detect_s']} с: {x['message']} | {x['kind']}: {x['cause']}")


asyncio.run(main())
