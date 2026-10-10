"""Замер задержек HTTP-ручек на стенде (`devserver_live.py`): вход, список комнат, история, публичный API, выдача токена комнаты.

Запуск: `python tests/live/bench_api.py` (стенд на 127.0.0.1:8000). Цифры относительные: стенд — SQLite и fakeredis на одной машине, без сети и без нагрузки.
Они нужны, чтобы увидеть выбросы и сравнить версии между собой, а не как оценка продуктивной установки (там PostgreSQL, Redis и сеть).
Параметры: BENCH_URL, BENCH_N (число запросов на ручку, по умолчанию 60).
"""
import json, os, statistics, time
import httpx

BASE = os.environ.get("BENCH_URL", "http://127.0.0.1:8000")
N = int(os.environ.get("BENCH_N", "60"))
rows: list[tuple[str, float, float, float, int]] = []


def bench(name, fn, n=N):
    ts, bad = [], 0
    for _ in range(n):
        t = time.perf_counter()
        r = fn()
        ts.append((time.perf_counter() - t) * 1000)
        bad += r.status_code >= 400
    ts.sort()
    rows.append((name, statistics.median(ts), ts[int(len(ts) * 0.95) - 1], ts[-1], bad))


def main():
    c = httpx.Client(base_url=BASE, timeout=30)
    t = time.perf_counter()
    r = c.post("/api/v1/auth/login", json={"login": "root", "password": "root-pass"})
    first_login = (time.perf_counter() - t) * 1000
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    h = {"X-CSRF-Token": csrf}
    bench("вход (login), повторный", lambda: c.post("/api/v1/auth/login", json={"login": "root", "password": "root-pass"}), 20)
    csrf = c.post("/api/v1/auth/login", json={"login": "root", "password": "root-pass"}).json()["csrf_token"]
    h = {"X-CSRF-Token": csrf}
    rooms = c.get("/api/v1/rooms").json()
    slug = next(x["slug"] for x in rooms if x["slug"] == "sales")
    bench("GET /rooms (14 комнат)", lambda: c.get("/api/v1/rooms"))
    bench("GET /meetings (история)", lambda: c.get("/api/v1/meetings", params={"limit": 30}))
    bench("GET /profile", lambda: c.get("/api/v1/profile"))
    old = next(m for m in c.get("/api/v1/meetings", params={"limit": 30}).json() if m.get("ended_at") and len(m.get("participants", [])) >= 100)
    bench("GET /meetings/{id} (130 участников)", lambda: c.get(f"/api/v1/meetings/{old['id']}"))
    bench("GET /meetings/{id}/transcript", lambda: c.get(f"/api/v1/meetings/{old['id']}/transcript"))
    bench("GET /meetings/{id}/avatars", lambda: c.get(f"/api/v1/meetings/{old['id']}/avatars"))
    # публичный API
    c.put("/api/v1/admin/settings/api", json={"enabled": True, "rate_read": 100000}, headers=h)
    cl = c.post("/api/v1/admin/public-api/clients", json={"name": f"bench-{int(time.time())}", "scopes": ["rooms:read", "meetings:read", "transcripts:read"]}, headers=h).json()
    key = c.post(f"/api/v1/admin/public-api/clients/{cl['id']}/keys", json={}, headers=h).json()["secret"]
    p = httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {key}"})
    mid = p.get("/api/public/v1/meetings", params={"limit": 50}).json()["items"][-1]["id"]
    bench("public GET /me", lambda: p.get("/api/public/v1/me"))
    bench("public GET /meetings?limit=50", lambda: p.get("/api/public/v1/meetings", params={"limit": 50}))
    bench("public GET /meetings/{id}/transcript?format=srt", lambda: p.get(f"/api/public/v1/meetings/{mid}/transcript", params={"format": "srt"}))
    bad = {"Authorization": "Bearer pgk_00000000_" + "A" * 43}
    bench("public 401 (неверный ключ, то же соединение)", lambda: p.get("/api/public/v1/me", headers=bad), 30)
    bench("public 401 (новое соединение каждый раз)", lambda: httpx.get(BASE + "/api/public/v1/me", headers=bad), 10)
    c.delete(f"/api/v1/admin/public-api/clients/{cl['id']}", headers=h)
    # токен комнаты (LiveKit должен быть запущен)
    r = c.post(f"/api/v1/rooms/{slug}/token", headers=h, json={})
    print("токен комнаты:", r.status_code, r.text[:100])
    if r.status_code == 200:
        def tok():
            x = c.post(f"/api/v1/rooms/{slug}/token", headers=h, json={})
            return x
        bench("POST /rooms/{slug}/token", tok, 20)
    print(f"первый вход: {first_login:.0f} мс\n")
    print(f"{'ручка':<52}{'p50 мс':>8}{'p95 мс':>8}{'max мс':>8}{'ошибок':>8}")
    for name, p50, p95, mx, bad in rows:
        print(f"{name:<52}{p50:>8.1f}{p95:>8.1f}{mx:>8.1f}{bad:>8}")


main()
