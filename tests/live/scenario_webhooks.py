"""Сценарий (см. README.md): события публичного API (webhooks) — настоящая админка + ВНЕШНИЙ приёмник на localhost:9200, который сам проверяет подпись, метку времени и дубли.

Путь: политика on-prem (разрешить localhost по http) → получатель создаётся в интерфейсе (секрет показан один раз) → адрес 169.254.169.254 отклоняется → встреча завершается через
публичный API → приёмник отвечает 500, сервер повторяет (тот же Event-Id, новые метка и подпись) → доставлено → «Проверить» → ответ 410 отключает получателя → «Включить снова».
Стенд должен быть запущен с воркерами (devserver_live.py запускает их).
"""
import asyncio, hashlib, hmac, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

PORT = 9200
STATE = {"mode": "500", "calls": [], "secret": None, "seen": set()}


def check_signature(headers, body: bytes) -> str:
    """Проверка, как её делает получатель: подпись, свежесть, дубль."""
    ts = int(headers["X-Peregovorka-Timestamp"])
    if abs(time.time() - ts) > 300:
        return "stale"
    given = [p[3:] for p in headers["X-Peregovorka-Signature"].split(",") if p.startswith("v1=")]
    want = hmac.new(STATE["secret"].encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(g, want) for g in given):
        return "bad-signature"
    eid = headers["X-Peregovorka-Event-Id"]
    if eid in STATE["seen"]:
        return "duplicate"
    return "ok"


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        verdict = check_signature(self.headers, body) if STATE["secret"] else "no-secret-yet"
        STATE["calls"].append({"event": self.headers["X-Peregovorka-Event"], "event_id": self.headers["X-Peregovorka-Event-Id"], "ts": self.headers["X-Peregovorka-Timestamp"],
                               "attempt": self.headers["X-Peregovorka-Attempt"], "verdict": verdict, "sig": self.headers["X-Peregovorka-Signature"], "body": body, "mode": STATE["mode"]})
        code = {"500": 500, "410": 410}.get(STATE["mode"], 200)
        if code == 200 and verdict == "ok":
            STATE["seen"].add(self.headers["X-Peregovorka-Event-Id"])
        self.send_response(code); self.send_header("content-length", "2"); self.end_headers(); self.wfile.write(b"ok")


async def api(c, method, path, body=None, csrf=""):
    js = (f"fetch('{path}',{{method:'{method}',headers:{{'content-type':'application/json','X-CSRF-Token':'{csrf}'}}" + (f",body:JSON.stringify({json.dumps(body)})" if body is not None else "")
          + "}).then(async r=>({s:r.status,j:await r.json().catch(()=>null)}))")
    return await c.js(js)


async def click(c, text, scope="button"):
    return await c.js(f"(()=>{{const b=[...document.querySelectorAll({json.dumps(scope)})].find(x=>x.textContent.includes({json.dumps(text)}));if(!b)return false;b.click();return true}})()")


async def type_into(c, selector, value):
    await c.js(f"(()=>{{const i=document.querySelector({json.dumps(selector)});const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,{json.dumps(value)});i.dispatchEvent(new Event('input',{{bubbles:true}}))}})()")


async def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Receiver)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    R = S.Client("root", "root", "root-pass", 9551)
    try:
        await R.start()
        csrf = await R.js("fetch('/api/v1/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({login:'root',password:'root-pass'})}).then(r=>r.json()).then(j=>j.csrf_token)")
        r = await api(R, "PUT", "/api/v1/admin/settings/api", {"enabled": True, "webhook_allow_http": True, "webhook_allow_hosts": "localhost", "rate_read": 100000}, csrf)
        S.check("политика on-prem сохранена (http и узел localhost разрешены)", r["s"] == 200, str(r)[:120])

        # адреса, которые политика обязана отклонить, — через настоящий интерфейс
        await R.goto("/admin?tab=public_api", 3.0)
        await click(R, "События (webhooks)", "button[role=tab]")
        await asyncio.sleep(0.5)
        await click(R, "Новый получатель")
        await asyncio.sleep(0.5)
        await type_into(R, "[role=dialog] input:not([type=checkbox])", "SSRF-проба")
        await R.js("""(()=>{const i=[...document.querySelectorAll('[role=dialog] input')].find(x=>x.placeholder.includes('hooks.example.com'));const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'http://169.254.169.254/latest/meta-data');i.dispatchEvent(new Event('input',{bubbles:true}))})()""")
        await click(R, "Сохранить", "[role=dialog] button")
        S.check("адрес метаданных облака отклонён политикой (сообщение в окне)", await R.wait_for("document.querySelector('[role=dialog]')?.innerText.includes('метаданных') || document.querySelector('[role=dialog]')?.innerText.includes('не принят')", 10))
        await R.shot("webhook-ssrf-rejected")
        await R.js("[...document.querySelectorAll('[role=dialog] button')].find(b=>b.textContent.includes('Отмена'))?.click()")

        # настоящий получатель
        await click(R, "Новый получатель")
        await asyncio.sleep(0.5)
        await type_into(R, "[role=dialog] input:not([type=checkbox])", "Приёмник стенда")
        await R.js("""(()=>{const i=[...document.querySelectorAll('[role=dialog] input')].find(x=>x.placeholder.includes('hooks.example.com'));const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'http://localhost:9200/hook');i.dispatchEvent(new Event('input',{bubbles:true}))})()""")
        await R.js("[...document.querySelectorAll('[role=dialog] label.check')].find(l=>l.textContent.includes('meeting.ended'))?.querySelector('input')?.click()")
        await click(R, "Сохранить", "[role=dialog] button")
        S.check("секрет подписи показан один раз", await R.wait_for("!!document.querySelector('input[aria-label=\"Секрет подписи\"]')", 10))
        STATE["secret"] = await R.js("document.querySelector('input[aria-label=\"Секрет подписи\"]').value")
        S.check("секрет имеет формат whsec_…", bool(STATE["secret"]) and STATE["secret"].startswith("whsec_"))
        await R.shot("webhook-secret")
        await click(R, "Я сохранил секрет")

        # интеграция с правами «завершить» и «читать» + ключ
        cl = (await api(R, "POST", "/api/v1/admin/public-api/clients", {"name": "завершатель", "scopes": ["meetings:end", "meetings:read"]}, csrf))["j"]
        key = (await api(R, "POST", f"/api/v1/admin/public-api/clients/{cl['id']}/keys", {}, csrf))["j"]["secret"]
        live = await R.js(f"fetch('/api/public/v1/meetings?state=active',{{headers:{{Authorization:'Bearer {key}'}}}}).then(r=>r.json())")
        mid = next(m["id"] for m in live["items"] if m["room"]["slug"] == "ops")
        S.check("есть идущая встреча для проверки", bool(mid), mid)

        # завершить встречу: приёмник сначала отвечает 500 → сервер повторит
        t0 = time.time()
        end = await R.js(f"fetch('/api/public/v1/meetings/{mid}/end',{{method:'POST',headers:{{Authorization:'Bearer {key}'}}}}).then(async r=>({{s:r.status,j:await r.json()}}))")
        S.check("встреча завершена через публичный API", end["s"] == 200 and end["j"]["state"] == "ended", str(end)[:100])
        ok1 = False
        for _ in range(60):
            if any(c["event"] == "meeting.ended" for c in STATE["calls"]):
                ok1 = True
                break
            await asyncio.sleep(0.5)
        S.check("первое событие дошло до приёмника (он ответил 500)", ok1, f"{time.time() - t0:.1f} с")
        STATE["mode"] = "ok"
        for _ in range(60):
            if sum(c["event"] == "meeting.ended" for c in STATE["calls"]) >= 2:
                break
            await asyncio.sleep(0.5)
        ended = [c for c in STATE["calls"] if c["event"] == "meeting.ended"]
        S.check("сервер повторил доставку после ошибки", len(ended) >= 2, f"попыток: {len(ended)}, через {time.time() - t0:.0f} с")
        if len(ended) >= 2:
            a, b = ended[0], ended[1]
            S.check("подпись, метка времени и дубль проверены приёмником: обе доставки валидны", a["verdict"] in ("ok",) and b["verdict"] in ("ok", "duplicate"), f"{a['verdict']} / {b['verdict']}")
            S.check("при повторе тот же Event-Id, но новые метка времени и подпись", a["event_id"] == b["event_id"] and a["ts"] != b["ts"] and a["sig"] != b["sig"] and b["attempt"] == "2")
            ev = json.loads(b["body"])
            S.check("в событии идентификаторы без персональных данных", ev["type"] == "meeting.ended" and ev["data"]["meeting_id"] == mid and "@" not in b["body"].decode(), b["body"].decode()[:90])
        # история и проверка в интерфейсе
        await R.goto("/admin?tab=public_api", 3.0)
        await click(R, "События (webhooks)", "button[role=tab]")
        await asyncio.sleep(0.5)
        await click(R, "История")
        S.check("в истории видна доставленная доставка", await R.wait_for("document.querySelector('[role=dialog]')?.innerText.includes('доставлено')", 10))
        await R.shot("webhook-history")
        await R.js("[...document.querySelectorAll('[role=dialog] button')].find(b=>b.textContent.includes('✕'))?.click()")
        await click(R, "Проверить")
        S.check("кнопка «Проверить» доставляет проверочное событие", await R.wait_for("document.body.innerText.includes('проверочное событие доставлено')", 15))

        # 410 Gone → получатель отключается; кнопка возвращает его
        STATE["mode"] = "410"
        await click(R, "Проверить")
        await asyncio.sleep(2)
        await R.goto("/admin?tab=public_api", 3.0)
        await click(R, "События (webhooks)", "button[role=tab]")
        await asyncio.sleep(0.5)
        S.check("ответ 410 отключил получателя (в интерфейсе видно причину)", await R.wait_for("document.body.innerText.includes('Отключён') && document.body.innerText.includes('410')", 10))
        await R.shot("webhook-disabled")
        STATE["mode"] = "ok"
        await click(R, "Включить снова")
        S.check("«Включить снова» возвращает получателя в работу", await R.wait_for("document.body.innerText.includes('работает')", 10))
        print("ошибки консоли:", (await R.state())["errs"][:2])
    finally:
        await R.close()
        srv.shutdown()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
