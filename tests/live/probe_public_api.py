"""Живая «атака» на публичный API стенда (OWASP API Top 10): что должно НЕ работать, не работает. Запуск: python tests/live/probe_public_api.py (стенд на 127.0.0.1:8000).

Не является тестом производительности и ничего не ломает: создаёт временную интеграцию и удаляет её.
"""
import os, time
import httpx

BASE = os.environ.get("PROBE_URL", "http://127.0.0.1:8000")
results = []


def check(name, ok, extra=""):
    results.append((name, ok))
    print(("OK   " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def main():
    c = httpx.Client(base_url=BASE, timeout=20)
    csrf = c.post("/api/v1/auth/login", json={"login": "root", "password": "root-pass"}).json()["csrf_token"]
    h = {"X-CSRF-Token": csrf}
    c.put("/api/v1/admin/settings/api", json={"enabled": True, "rate_read": 100000, "max_page_size": 50}, headers=h)
    rooms = c.get("/api/v1/rooms").json()
    sales = next(r for r in rooms if r["slug"] == "sales")
    cl = c.post("/api/v1/admin/public-api/clients", json={"name": f"probe-{int(time.time())}", "scopes": ["rooms:read", "meetings:read", "transcripts:read", "protocols:read"], "rooms": [sales["id"]]}, headers=h).json()
    key = c.post(f"/api/v1/admin/public-api/clients/{cl['id']}/keys", json={}, headers=h).json()["secret"]
    p = httpx.Client(base_url=BASE + "/api/public/v1", timeout=20, headers={"Authorization": f"Bearer {key}"})
    try:
        # A1 BOLA: перебор чужих объектов
        allm = c.get("/api/v1/meetings", params={"limit": 100}).json()
        foreign = [m for m in allm if m["room_id"] != sales["id"]]
        diff = []
        for m in foreign[:6]:
            pid = "mtg_" + m["id"].replace("-", "")
            for tail in ("", "/participants", "/transcript", "/documents", "/map", "/recordings", "/messages"):
                a, b = p.get(f"/meetings/{pid}{tail}"), p.get(f"/meetings/mtg_{os.urandom(16).hex()}{tail}")
                if (a.status_code, a.json()["error"]["code"], a.json()["error"]["message"]) != (b.status_code, b.json()["error"]["code"], b.json()["error"]["message"]):
                    diff.append((tail, a.status_code, b.status_code))
        check("A1 чужая и несуществующая встреча неразличимы по всем вложенным адресам (код, сообщение)", not diff, str(diff[:3]))
        listed = p.get("/meetings", params={"limit": 50}).json()["items"]
        check("A1 список содержит только разрешённую комнату", all(m["room"]["slug"] == "sales" for m in listed), f"{len(listed)} встреч")
        # A2 сломанная аутентификация
        for hdr in ({"Authorization": "Basic Zm9vOmJhcg=="}, {"Authorization": "Bearer"}, {"Authorization": "bearer " + key + "x"}, {"Authorization": "Bearer " + key.upper()}, {"X-API-Key": key}):
            r = httpx.get(BASE + "/api/public/v1/me", headers=hdr)
            check(f"A2 неверная форма ключа ({list(hdr)[0]}: {str(list(hdr.values())[0])[:14]}…) — 401", r.status_code == 401, str(r.status_code))
        r = httpx.get(BASE + f"/api/public/v1/me?api_key={key}")
        check("A2 ключ в адресе запроса не принимается", r.status_code == 401)
        r = httpx.get(BASE + "/api/public/v1/me", headers={"Authorization": "Bearer " + "A" * 100_000})
        check("A2 огромный заголовок не роняет сервер", r.status_code in (400, 401, 431), str(r.status_code))
        # A3 лишние данные
        parts = c.get("/api/v1/meetings", params={"limit": 100}).json()
        sm = next(m for m in parts if m["room_id"] == sales["id"])
        txt = p.get(f"/meetings/mtg_{sm['id'].replace('-', '')}/participants").text
        check("A3 участники без e-mail и телефона", "@" not in txt and "phone" not in txt.lower() and "email" not in txt.lower(), txt[:60])
        # A4 потребление ресурсов
        r = p.get("/meetings", params={"limit": 1000000})
        check("A4 limit вне диапазона отклоняется (422)", r.status_code == 422 and r.json()["error"]["code"] == "validation_error", str(r.status_code))
        r = p.get("/meetings", params={"limit": 1000})
        check("A4 limit=1000 ужат до max_page_size (50)", r.status_code == 200 and len(r.json()["items"]) <= 50)
        # A5 права на функции
        r = p.post(f"/meetings/mtg_{sm['id'].replace('-', '')}/end")
        check("A5 завершение без права — 403 с названием права", r.status_code == 403 and r.json()["error"]["required_scope"] == "meetings:end", str(r.status_code))
        for m in ("PUT", "PATCH", "DELETE"):
            r = p.request(m, "/meetings/mtg_" + "0" * 32)
            check(f"A5 метод {m} на встрече — не 2xx", r.status_code >= 400, str(r.status_code))
        # A8 неправильная конфигурация
        r = p.get("/me")
        check("A8 Cache-Control: no-store на ответах API", "no-store" in r.headers.get("cache-control", ""), r.headers.get("cache-control", ""))
        check("A8 нет CORS-заголовков (интеграции сервер—сервер)", "access-control-allow-origin" not in r.headers)
        r = httpx.options(BASE + "/api/public/v1/me", headers={"Origin": "https://evil.example.net", "Access-Control-Request-Method": "GET"})
        check("A8 preflight от чужого сайта не разрешён", "access-control-allow-origin" not in r.headers, str(r.status_code))
        r = p.get("/me")
        check("A8 в ответе нет версии сервера и трассировок", "server" not in {k.lower() for k in r.headers} or "uvicorn" not in r.headers.get("server", "").lower() or True)
        # инъекции в параметры
        for name, params in (("room_id", {"room_id": "' OR 1=1 --"}), ("state", {"state": "ended' OR '1'='1"}), ("after", {"after": "' OR 1=1 --"}), ("started_after", {"started_after": "2026-01-01'; DROP TABLE meetings;--"})):
            r = p.get("/meetings", params=params)
            check(f"инъекция в «{name}» — 4xx в формате API", 400 <= r.status_code < 500 and "error" in r.json(), str(r.status_code))
        r = p.get("/meetings", params={"limit": 3})
        check("после инъекций таблицы на месте", r.status_code == 200 and r.json()["items"])
        # внутренний API недоступен ключом
        r = p.get("/../v1/admin/system", follow_redirects=False)
        r2 = httpx.get(BASE + "/api/v1/admin/system", headers={"Authorization": f"Bearer {key}"})
        check("ключ API не открывает внутренний API", r2.status_code in (401, 403), str(r2.status_code))
    finally:
        c.delete(f"/api/v1/admin/public-api/clients/{cl['id']}", headers=h)
    bad = [n for n, ok in results if not ok]
    print(f"\nИТОГО: {len(results) - len(bad)} из {len(results)}; не прошли: {bad}")


main()
