"""Сценарий (см. README.md): сквозной: встреча → стенограмма → завершение → история → протокол (с подставной LLM). Всё через настоящий backend; UI проверяется в браузере."""
import asyncio, base64, http.cookiejar, http.server, json, os, subprocess, sys, tempfile, threading, time, urllib.request
import urllib.error
import websockets
B = "http://127.0.0.1:8000/api/v1"
HOST = os.environ.get("PEREGOVORKA_URL", "http://localhost:5173")
EDGE = os.environ.get("BROWSER_PATH", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
OUT = os.environ.get("SMOKE_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")); os.makedirs(OUT, exist_ok=True)
res = []


def check(n, ok, extra=""):
    res.append(bool(ok)); print(("OK   " if ok else "FAIL ") + n + (f"  [{extra}]" if extra and not ok else ""), flush=True)


class LLM(http.server.BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        LLM.calls += 1
        n = int(self.headers.get("content-length", 0)); self.rfile.read(n)
        body = json.dumps({"choices": [{"message": {"content": "# Протокол встречи\n\n## Решения\n\n- Утвердить бюджет проекта (по итогам обсуждения).\n"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 50, "completion_tokens": 30}}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 9100), LLM)
threading.Thread(target=srv.serve_forever, daemon=True).start()


class Api:
    def __init__(self, login, pw):
        self.cj = http.cookiejar.CookieJar(); self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj)); self.csrf = ""
        r = self.call("POST", "/auth/login", {"login": login, "password": pw}); self.csrf = r["csrf_token"]

    def call(self, m, path, body=None):
        req = urllib.request.Request(B + path, method=m, data=json.dumps(body).encode() if body is not None else None, headers={"content-type": "application/json", "X-CSRF-Token": self.csrf})
        try:
            with self.op.open(req) as r:
                t = r.read(); return json.loads(t) if t else {"_status": r.status}
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "_body": e.read().decode()[:300]}


def dev_segment(mid, ident, text, off):
    req = urllib.request.Request("http://127.0.0.1:8000/__dev/segment", method="POST", data=json.dumps({"meeting_id": mid, "identity": ident, "text": text, "offset": off}).encode(), headers={"content-type": "application/json"})
    urllib.request.urlopen(req).read()


def main_api():
    root = Api("root", "root-pass"); alice = Api("alice", "alice-pass"); bob = Api("bob", "bob-pass")
    r = root.call("PUT", "/admin/settings/llm", {"enabled": True, "provider": "external", "type": "openai_compatible", "base_url": "http://127.0.0.1:9100/v1", "model": "mock", "allow_http": True, "api_key": "k"})
    check("настройки LLM сохранены", "_status" not in r, str(r))
    root.call("PUT", "/admin/settings/protocol", {"external_mode": "free"})
    rooms = {x["slug"]: x for x in root.call("GET", "/rooms")}
    jr = alice.call("POST", f"/rooms/{rooms['legal']['id']}/join", {})
    check("Алиса вошла в комнату (API)", "meeting_id" in jr, str(jr)[:200])
    jb = bob.call("POST", f"/rooms/{rooms['legal']['id']}/join", {})
    mid = jr["meeting_id"]
    for i, (idn, t) in enumerate([(jr["identity"], "Предлагаю утвердить бюджет проекта на следующий квартал."), (jb["identity"], "Согласен, но нужно уточнить сроки поставки оборудования."), (jr["identity"], "Решили: бюджет утверждаем, сроки уточняет Борис до пятницы.")]):
        dev_segment(mid, idn, t, i * 3)
    time.sleep(2)
    segs = alice.call("GET", f"/meetings/{mid}/transcript")
    check("стенограмма: реплики записаны в БД и отдаются API", len(segs.get("segments", [])) == 3, str(segs)[:200])
    e = root.call("POST", f"/meetings/{mid}/end")
    check("встреча завершена", e.get("_status") in (200, 204), str(e))
    return mid


async def browser(mid):
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--remote-debugging-port=9441", f"--user-data-dir={tempfile.mkdtemp(prefix='edge-f-')}", "--window-size=1366,900", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:9441/json").read(); break
            except Exception:
                time.sleep(0.5)
        info = json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9441/json/new?about:blank", method="PUT")))
        ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=80_000_000, ping_interval=None)
        n = 0; errs = []

        async def send(m, **p):
            nonlocal n
            n += 1; i = n
            await ws.send(json.dumps({"id": i, "method": m, "params": p}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == i:
                    return r.get("result", {})
                if r.get("method") == "Runtime.exceptionThrown":
                    errs.append(json.dumps(r["params"]["exceptionDetails"], ensure_ascii=False)[:300])

        async def js(e):
            r = await send("Runtime.evaluate", expression=e, awaitPromise=True, returnByValue=True)
            return r.get("result", {}).get("value")

        async def wait(expr, t=20):
            t0 = time.time()
            while time.time() - t0 < t:
                if await js(expr):
                    return True
                await asyncio.sleep(0.5)
            return False

        async def shot(name):
            r = await send("Page.captureScreenshot", format="png")
            open(os.path.join(OUT, name), "wb").write(base64.b64decode(r["data"]))

        await send("Page.enable"); await send("Runtime.enable")
        await send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        await send("Page.navigate", url=HOST + "/"); await asyncio.sleep(2)
        await js("fetch('/api/v1/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({login:'root',password:'root-pass'})}).then(r=>r.status)")
        await send("Page.navigate", url=f"{HOST}/history/{mid}"); await asyncio.sleep(3)
        await js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        await js("[...document.querySelectorAll('[role=tab]')].find(t=>t.textContent.includes('Стенограмма'))?.click()")
        check("история: стенограмма показывает реплики", await wait("document.body.innerText.includes('утвердить бюджет проекта')", 10))
        await shot("flow-transcript.png")
        await js("[...document.querySelectorAll('[role=tab]')].find(t=>t.textContent.includes('Протоколы'))?.click()")
        await js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Сформировать протокол')?.click()")
        check("открылось окно «Сформировать протокол»", await wait("!!document.querySelector('[role=dialog], .modal')", 8))
        await shot("flow-protocol-dialog.png")
        await js("""(()=>{const d=document.querySelector('[role=dialog], .modal');const b=[...d.querySelectorAll('button')].find(x=>/Сформировать|Создать|Запустить/.test(x.textContent)&&!x.disabled);b&&b.click();return !!b})()""")
        ok = await wait("document.body.innerText.includes('Утвердить бюджет проекта (по итогам обсуждения)')", 40)
        check("протокол сформирован и показан (подставная LLM)", ok, str(LLM.calls))
        await shot("flow-protocol.png")
        check("LLM вызвана", LLM.calls >= 1, str(LLM.calls))
        for fmt in ("docx", "html", "md", "pdf"):
            code = await js(f"fetch('/api/v1/meetings/{mid}/protocols').then(r=>r.json()).then(l=>fetch('/api/v1/meetings/{mid}/protocols/'+l[0].id+'/export?format={fmt}').then(r=>r.status+' '+r.headers.get('content-type')))")
            check(f"экспорт протокола .{fmt} отдаётся", str(code).startswith("200"), str(code))
        print("ошибки страницы:", errs[:3])
        check("нет ошибок страницы", not errs, str(errs[:2]))
        await ws.close()
    finally:
        proc.terminate()


mid = main_api()
asyncio.run(browser(mid))
print(f"\nИТОГО: {sum(res)} из {len(res)}")
