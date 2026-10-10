"""Сценарий (см. README.md): Bitrix24 как источник профиля — настоящая админка, подставной портал на localhost:9100, вход не зависит от портала.

Порядок: администратор настраивает интеграцию через страницу → «Проверить подключение», пробный поиск, синхронизация → Борис видит должность
и фото с портала → портал «падает» → вход Бориса и Алисы по-прежнему мгновенный, данные остаются.
"""
import asyncio, io, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

PORT = 9100
STATE = {"down": False, "calls": 0}


def photo() -> bytes:
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (300, 300), (30, 160, 70)).save(b, "PNG"); return b.getvalue()


class Portal(BaseHTTPRequestHandler):
    def log_message(self, *a):  # тишина
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code); self.send_header("content-type", ctype); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        STATE["calls"] += 1
        if STATE["down"]:
            return self._send(503, b"{}")
        self._send(200, photo(), "image/png") if self.path.startswith("/upload/") else self._send(404, b"{}")

    def do_POST(self):
        STATE["calls"] += 1
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if STATE["down"]:
            time.sleep(8)                                   # портал «завис»
            return self._send(503, b"{}")
        if self.path.endswith("/user.current.json"):
            return self._send(200, json.dumps({"result": {"ID": "7", "NAME": "Сервис", "LAST_NAME": "Интеграция"}}).encode())
        if self.path.endswith("/department.get.json"):
            return self._send(200, json.dumps({"result": [{"ID": "5", "NAME": "Служба информационных технологий"}]}).encode())
        if self.path.endswith("/user.get.json"):
            email = (body.get("FILTER") or {}).get("EMAIL")
            rows = [{"ID": "42", "ACTIVE": "Y", "NAME": "Борис", "LAST_NAME": "Мартынов", "EMAIL": "bob@corp.test", "WORK_POSITION": "Ведущий инженер",
                     "UF_DEPARTMENT": [5], "WORK_PHONE": "+7 495 000-00-02", "PERSONAL_PHOTO": "/upload/main/bob.png"}] if email == "bob@corp.test" else []
            return self._send(200, json.dumps({"result": rows, "total": len(rows)}).encode())
        self._send(404, b"{}")


async def api(c, method, path, body=None, csrf=""):
    js = f"fetch('/api/v1{path}',{{method:'{method}',headers:{{'content-type':'application/json','X-CSRF-Token':'{csrf}'}}" + (f",body:JSON.stringify({json.dumps(body)})" if body is not None else "") + "}).then(async r=>({s:r.status,j:await r.json().catch(()=>null)}))"
    return await c.js(js)


async def login_token(c, user, pw):
    return await c.js(f"fetch('/api/v1/auth/login',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{login:'{user}',password:'{pw}'}})}}).then(r=>r.json()).then(j=>j.csrf_token)")


async def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Portal)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    R = S.Client("root", "root", "root-pass", 9531)
    B = S.Client("bob", "bob", "bob-pass", 9532)
    try:
        await R.start()
        csrf = await login_token(R, "root", "root-pass")
        r = await api(R, "PUT", "/admin/settings/bitrix24", {"enabled": True, "portal_url": f"http://localhost:{PORT}", "webhook_url": f"http://localhost:{PORT}/rest/7/s3cr3tTOKEN/", "allow_http": True}, csrf)
        S.check("настройки сохранены", r and r["s"] == 200, str(r))
        S.check("секрет webhook не возвращается", "s3cr3tTOKEN" not in json.dumps(r["j"]))

        await R.goto("/admin?tab=bitrix24", 2.5)
        S.check("страница Bitrix24 открылась", bool(await R.js("document.body.innerText.includes('Bitrix24: данные профиля')")))
        await R.shot("admin-bitrix")
        await R.js("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Проверить подключение'))?.click()")
        S.check("кнопка «Проверить подключение» показывает владельца webhook", await R.wait_for("document.body.innerText.includes('Webhook создан от имени: Интеграция Сервис')", 15))
        await R.js("""(()=>{const i=document.querySelector('input[type=email]');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'bob@corp.test');i.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        await R.js("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Показать, что вернёт портал'))?.click()")
        S.check("пробный поиск показывает должность", await R.wait_for("document.body.innerText.includes('Ведущий инженер')", 15))
        await R.shot("admin-bitrix-lookup")

        # вход Бориса: страница отвечает сразу, данные приходят в фоне
        await B.start()
        t0 = time.monotonic()
        await B.js("fetch('/api/v1/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({login:'bob',password:'bob-pass'})}).then(r=>r.status)")
        S.check("вход Бориса быстрый", time.monotonic() - t0 < 2.0, f"{time.monotonic() - t0:.2f} с")
        ok = False
        for _ in range(20):
            p = await B.js("fetch('/api/v1/profile').then(r=>r.json())")
            if p and p.get("title"):
                ok = True; break
            await asyncio.sleep(0.5)
        S.check("должность и подразделение пришли с портала", ok and p["title"] == "Ведущий инженер" and p["department"] == "Служба информационных технологий", str(p))
        S.check("фото пришло с портала", bool(p and p.get("avatar_url")), str(p))
        await B.goto("/profile", 2.5)
        await B.shot("profile-bitrix")
        img = await B.js("(()=>{const i=document.querySelector('img[src*=\"/avatar\"]');return i?[i.naturalWidth,i.complete]:null})()")
        S.check("фото профиля отображается в браузере", bool(img) and img[0] > 0, str(img))

        # портал «упал»: вход не замедляется, данные не пропадают
        STATE["down"] = True
        await B.js("fetch('/api/v1/auth/logout',{method:'POST',headers:{'X-CSRF-Token':''}}).catch(()=>0)")
        csrf_r = await login_token(R, "root", "root-pass")
        await api(R, "POST", "/admin/bitrix24/sync", None, csrf_r)             # принудительное обновление при «зависшем» портале запускается в фоне только у входящих
        t0 = time.monotonic()
        st = await B.js("fetch('/api/v1/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({login:'bob',password:'bob-pass'})}).then(r=>r.status)")
        dt = time.monotonic() - t0
        S.check("при недоступном портале вход Бориса проходит", st == 200, str(st))
        S.check("и не ждёт портал", dt < 3.0, f"{dt:.2f} с")
        p2 = await B.js("fetch('/api/v1/profile').then(r=>r.json())")
        S.check("прежние данные и фото остаются", p2 and p2.get("title") == "Ведущий инженер" and bool(p2.get("avatar_url")), str(p2))
        A = await B.js("fetch('/api/v1/rooms').then(r=>r.status)")
        S.check("комнаты доступны при упавшем портале", A == 200, str(A))
        print("ошибки консоли:", (await B.state())["errs"][:2])
    finally:
        STATE["down"] = False
        await R.close(); await B.close()
        srv.shutdown()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
