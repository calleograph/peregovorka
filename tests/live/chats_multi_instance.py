"""Чаты при нескольких экземплярах backend (общие PostgreSQL и Redis, без закрепления клиента за экземпляром) и замер поиска на большой истории.

Подготовка (пример — временные контейнеры, после проверки удаляются):
  docker run -d --rm --name pgm-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=pg -p 127.0.0.1:55439:5432 --tmpfs /var/lib/postgresql/data postgres:16-alpine
  docker run -d --rm --name pgm-redis -p 127.0.0.1:56379:6379 redis:7-alpine
  DATABASE_URL=postgresql+asyncpg://postgres:pw@127.0.0.1:55439/pg alembic upgrade head          (из backend/)
  два экземпляра: python tests/live/multi_app.py 8101 и ... 8102 с теми же DATABASE_URL и REDIS_URL=redis://127.0.0.1:56379/0
Запуск: python tests/live/chats_multi_instance.py [ROWS]  — ROWS сообщений истории для замера поиска (по умолчанию 200000; вставляются напрямую в PostgreSQL).

Проверяется: сообщение, отправленное через экземпляр 1, приходит по WebSocket собеседнику на экземпляре 2 и второму устройству отправителя на экземпляре 2;
прочитанность — обратно; одинаковая повторная отправка в оба экземпляра одновременно — одно сообщение; поиск по истории ROWS сообщений — время ответа API
и план запроса (индекс GIN). Это замер на одной машине, а не нагрузочное испытание."""
import asyncio, json, os, subprocess, sys, time, uuid

import httpx
import websockets

A_URL, B_URL = os.environ.get("INSTANCE_1", "http://127.0.0.1:8101"), os.environ.get("INSTANCE_2", "http://127.0.0.1:8102")
ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
PG = os.environ.get("PG_CONTAINER", "pgm-pg")
results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok)))
    print(("OK   " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)


def session(base: str, login: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=60)
    c.post("/api/v1/auth/login", json={"login": login, "password": f"{login}-pass"}).raise_for_status()
    c.headers["X-CSRF-Token"] = c.get("/api/v1/auth/me").json()["csrf_token"]
    return c


async def socket(base: str, c: httpx.Client):
    cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    ws = await websockets.connect(base.replace("http", "ws") + "/api/v1/ws", additional_headers={"Cookie": cookie, "Origin": "http://localhost:5173"})
    await ws.send(json.dumps({"type": "subscribe_user"}))
    assert json.loads(await ws.recv())["type"] == "subscribed_user"
    return ws


async def wait_event(ws, kind: str, timeout=5.0) -> dict | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=end - time.monotonic()))
        except asyncio.TimeoutError:
            return None
        if ev.get("type") == kind:
            return ev
    return None


def psql(sql: str) -> str:
    return subprocess.run(["docker", "exec", PG, "psql", "-U", "postgres", "-d", "pg", "-At", "-c", sql], capture_output=True, text=True, check=True).stdout.strip()


async def main():
    alice1, bob2, alice2 = session(A_URL, "alice"), session(B_URL, "bob"), session(B_URL, "alice")
    bob_id = bob2.get("/api/v1/auth/me").json()["user"]["id"]
    cid = alice1.post("/api/v1/chats/direct", json={"id": bob_id}).json()["id"]
    ws_bob, ws_alice2, ws_alice1 = await socket(B_URL, bob2), await socket(B_URL, alice2), await socket(A_URL, alice1)
    t0 = time.monotonic()
    m = alice1.post(f"/api/v1/chats/{cid}/messages", json={"text": "через экземпляр 1", "client_id": uuid.uuid4().hex}).json()
    ev = await wait_event(ws_bob, "chat_message")
    dt = (time.monotonic() - t0) * 1000
    check("сообщение через экземпляр 1 доставлено собеседнику на экземпляре 2", ev and ev["message"]["id"] == m["id"], f"{dt:.0f} мс")
    ev = await wait_event(ws_alice2, "chat_message")
    check("второе устройство отправителя на экземпляре 2 получило сообщение", ev and ev["message"]["id"] == m["id"])
    bob2.post(f"/api/v1/chats/{cid}/read", json={"last_id": m["id"]})
    ev = await wait_event(ws_alice1, "chat_read")
    check("прочитанность с экземпляра 2 пришла на экземпляр 1", ev and ev["last_read_id"] == m["id"])
    cid_ = uuid.uuid4().hex
    with httpx.Client(base_url=A_URL, cookies=alice1.cookies, headers=alice1.headers, timeout=30) as a, httpx.Client(base_url=B_URL, cookies=alice1.cookies, headers=alice1.headers, timeout=30) as b:
        rs = await asyncio.gather(asyncio.to_thread(a.post, f"/api/v1/chats/{cid}/messages", json={"text": "повтор", "client_id": cid_}),
                                  asyncio.to_thread(b.post, f"/api/v1/chats/{cid}/messages", json={"text": "повтор", "client_id": cid_}))
    ids = {r.json().get("id") for r in rs}
    check("одновременная повторная отправка в два экземпляра — одно сообщение", len(ids) == 1 and all(r.status_code in (200, 201) for r in rs), str([r.status_code for r in rs]))
    for w in (ws_bob, ws_alice2, ws_alice1):
        await w.close()
    # ---- поиск по большой истории
    me = alice1.get("/api/v1/auth/me").json()["user"]["id"]
    words = "отчёт план бюджет договор релиз сервер встреча клиент проект задача срок команда".split()
    psql(f"""INSERT INTO chat_messages (chat_id, author_id, author_name, kind, text, created_at)
             SELECT '{cid}', '{me}', 'Алиса Крылова', 'text',
                    'сообщение ' || g || ' про ' || (ARRAY[{','.join(repr(w) for w in words)}])[1 + g % {len(words)}] || ' и '
                      || (ARRAY[{','.join(repr(w) for w in words)}])[1 + (g * 7) % {len(words)}] || CASE WHEN g % 50000 = 0 THEN ' уникальныйзапрос' ELSE '' END,
                    now() - (g || ' seconds')::interval FROM generate_series(1, {ROWS}) g""")
    psql("ANALYZE chat_messages")
    total = int(psql(f"SELECT count(*) FROM chat_messages WHERE chat_id = '{cid}'"))
    times = []
    for q in ("уникальныйзапрос", "бюджет договор", '"про релиз"', "серв"):
        t0 = time.monotonic()
        r = alice1.get(f"/api/v1/chats/{cid}/search", params={"q": q}).json()
        times.append((q, round((time.monotonic() - t0) * 1000), r["total"], r["more"]))
    plan = psql(f"""EXPLAIN SELECT id FROM chat_messages WHERE chat_id = '{cid}' AND to_tsvector('russian'::regconfig, text) @@ to_tsquery('russian'::regconfig, 'уникальныйзапрос:*')""")
    check(f"поиск по истории {total} сообщений: ответ API и выдача ограничена", all(t < 2000 for _, t, _, _ in times) and times[0][2] == ROWS // 50000,
          "; ".join(f"«{q}»: {t} мс, {n}{'+' if more else ''}" for q, t, n, more in times))
    check("поиск использует полнотекстовый индекс GIN", "ix_chat_messages_fts" in plan, plan.replace("\n", " | ")[:300])
    t0 = time.monotonic()
    alice1.get(f"/api/v1/chats/{cid}/messages", params={"limit": 50})
    page = round((time.monotonic() - t0) * 1000)
    t0 = time.monotonic()
    alice1.get("/api/v1/chats")
    lst = round((time.monotonic() - t0) * 1000)
    t0 = time.monotonic()
    bob2.get("/api/v1/chats/unread")
    unr = round((time.monotonic() - t0) * 1000)
    check("последняя страница, список и счётчик непрочитанных на большой истории", page < 500 and lst < 1000 and unr < 1000, f"страница {page} мс, список {lst} мс, непрочитанные у собеседника {unr} мс")
    failed = [n for n, ok in results if not ok]
    print(f"ИТОГО: {len(results) - len(failed)} из {len(results)}; не прошли: {failed}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
