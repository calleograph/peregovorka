#!/usr/bin/env python3
"""Нагрузочное испытание Peregovorka: N одновременно активных авторизованных пользователей (вход, список комнат, история, чат, стенограмма, администрирование) плюс
параллельное формирование материалов встреч (протокол и карта разговора через языковую модель с задержкой). Фиксирует p50/p95/p99, долю ошибок (особенно 5xx), состояние PostgreSQL
(соединения, idle in transaction) и ресурсы процессов. Запуск: python3 tests/perf/loadtest.py --url http://127.0.0.1:18080 --pg postgresql://… --users 500 --duration 60
Что НЕ проверяет: медиа (LiveKit) — это отдельное испытание с настоящим SFU и клиентами; здесь — приложение, база и очереди."""
from __future__ import annotations

import argparse, asyncio, contextlib, json, random, statistics, sys, time, uuid
from collections import defaultdict

import aiohttp

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:18080"); ap.add_argument("--pg", default="")
ap.add_argument("--users", type=int, default=100); ap.add_argument("--duration", type=int, default=60); ap.add_argument("--think", type=float, default=4.0, help="средняя пауза между действиями, с")
ap.add_argument("--rooms", type=int, default=0); ap.add_argument("--heavy", type=int, default=6, help="одновременных формирований материалов"); ap.add_argument("--pids", default="", help="pid процессов сервера (через запятую) для замера CPU/RSS")
ap.add_argument("--out", default=""); ap.add_argument("--max-5xx-pct", type=float, default=-1, help="порог для кода возврата (регрессионная проверка); -1 — без проверки")
ap.add_argument("--max-idle-tx-s", type=float, default=-1)
ap.add_argument("--profile", choices=("heavy", "client"), default="heavy",
                help="heavy — случайное действие раз в --think с (прежняя матрица); client — как настоящий браузер: вход в комнату, WebSocket, опросы страницы комнаты и списка комнат")
ap.add_argument("--lobby-pct", type=float, default=15, help="client: доля людей на странице списка комнат (остальные во встречах), %%")
ap.add_argument("--ramp", type=float, default=20, help="client: за сколько секунд приходят все участники встреч (начало собраний)")
A = ap.parse_args()

class Resp:
    def __init__(self, status: int, body: bytes, cookies):
        self.status_code, self._b, self.cookies = status, body, cookies

    @property
    def text(self) -> str:
        return self._b.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._b)


class Client:
    """Тонкая обёртка над aiohttp с интерфейсом, нужным испытанию. aiohttp выдерживает тысячи запросов в секунду на одном ядре; httpx — около 200, и испытание измеряло бы сам генератор."""

    def __init__(self, limit: int):
        self.s = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=limit, limit_per_host=limit), timeout=aiohttp.ClientTimeout(total=90))

    async def request(self, method: str, url: str, headers=None, json=None, timeout=None, **kw) -> Resp:
        async with self.s.request(method, url, headers=headers, json=json) as r:
            return Resp(r.status, await r.read(), {k: m.value for k, m in r.cookies.items()})

    async def post(self, url, json=None, headers=None, timeout=None):
        return await self.request("POST", url, headers=headers, json=json)

    async def close(self):
        await self.s.close()


lat: dict[str, list[float]] = defaultdict(list); codes: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int)); errors: list[str] = []
pg_samples: list[dict] = []; res_samples: list[dict] = []
ws_stats = {"opened": 0, "failed": 0, "events": 0, "dropped": 0, "reconnected": 0}
T0 = [0.0]                                    # начало установившегося режима: от него считается шкала ошибок по 5 с
timeline: dict[int, list[int]] = defaultdict(lambda: [0, 0])


class VU:
    def __init__(self, i: int, c: Client):
        self.i, self.c, self.cookie, self.csrf = i, c, "", ""

    async def req(self, kind: str, method: str, path: str, **kw) -> Resp | None:
        h = {"Cookie": self.cookie}
        if method != "GET":
            h["X-CSRF-Token"] = self.csrf
        t0 = time.perf_counter()
        try:
            r = await self.c.request(method, A.url + path, headers=h, timeout=60, **kw)
        except Exception as exc:  # noqa: BLE001
            lat[kind].append(time.perf_counter() - t0); codes[kind][0] += 1; errors.append(f"{kind}: {type(exc).__name__}")
            if T0[0]:
                timeline[int((time.monotonic() - T0[0]) // 5)][1] += 1
            return None
        lat[kind].append(time.perf_counter() - t0); codes[kind][r.status_code] += 1
        if T0[0]:
            timeline[int((time.monotonic() - T0[0]) // 5)][1 if r.status_code >= 500 else 0] += 1
        if r.status_code >= 400:
            errors.append(f"{kind}: HTTP {r.status_code} {r.text[:90]}")
        return r

    async def login(self, login: str) -> bool:
        t0 = time.perf_counter()
        r = await self.c.post(A.url + "/api/v1/auth/login", json={"login": login, "password": "pw"}, timeout=60)
        lat["login"].append(time.perf_counter() - t0); codes["login"][r.status_code] += 1
        if r.status_code != 200:
            errors.append(f"login {login}: {r.status_code} {r.text[:80]}"); return False
        self.cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items()); self.csrf = r.json().get("csrf_token", "")
        return True


async def setup(c: Client, n_rooms: int) -> tuple[list[dict], list[str]]:
    root = VU(-1, c)
    assert await root.login("root"), "вход администратора"
    await root.req("setup", "PUT", "/api/v1/admin/settings/llm", json={"enabled": True, "provider": "external", "type": "openai_compatible", "base_url": "http://llm.test/v1", "model": "m", "allow_http": True})
    rooms = []
    for k in range(n_rooms):
        r = await root.req("setup", "POST", "/api/v1/admin/rooms", json={"slug": f"perf-{k}-{uuid.uuid4().hex[:6]}", "name": f"Нагрузка {k}", "acl": [{"subject_type": "group", "subject_ref": "cn=staff,ou=groups,dc=corp,dc=test"}]})
        if r is not None and r.status_code == 201:
            rooms.append(r.json())
    return rooms, []


async def seed_meetings(c: Client, rooms: list[dict], count: int) -> list[str]:
    """Завершённые встречи со стенограммой — материал для формирования протоколов и карт."""
    out = []
    for k in range(count):
        u = VU(k, c); await u.login(f"u{k:04d}")
        r = await u.req("setup", "POST", f"/api/v1/rooms/{rooms[k % len(rooms)]['id']}/join", json={})
        if r is None or r.status_code != 200:
            continue
        j = r.json()
        for t in range(6):
            await c.post(A.url + "/__dev/segment", json={"meeting_id": j["meeting_id"], "identity": j["identity"], "text": f"Обсуждаем вопрос номер {t}, принимаем решение по бюджету", "offset": t * 30})
        await asyncio.sleep(0.3)
        await u.req("setup", "POST", f"/api/v1/meetings/{j['meeting_id']}/end")
        out.append((j["meeting_id"], u))
    await asyncio.sleep(2)
    return out


async def vu_loop(u: VU, room: dict, stop: float) -> None:
    r = await u.req("join", "POST", f"/api/v1/rooms/{room['id']}/join", json={})
    mid = r.json().get("meeting_id") if r is not None and r.status_code == 200 else None
    while time.monotonic() < stop:
        await asyncio.sleep(random.expovariate(1 / A.think))
        x = random.random()
        if x < 0.18: await u.req("me", "GET", "/api/v1/auth/me")
        elif x < 0.33: await u.req("rooms", "GET", "/api/v1/rooms")
        elif x < 0.48: await u.req("history", "GET", "/api/v1/meetings?limit=30&offset=0")
        elif x < 0.70 and mid: await u.req("chat_send", "POST", f"/api/v1/meetings/{mid}/chat", json={"text": f"сообщение {uuid.uuid4().hex[:6]}"})
        elif x < 0.88 and mid: await u.req("chat_read", "GET", f"/api/v1/meetings/{mid}/chat?limit=50")
        elif mid: await u.req("transcript", "GET", f"/api/v1/meetings/{mid}/transcript?after_id=0&limit=200")
        else: await u.req("rooms", "GET", "/api/v1/rooms")
    if mid:
        await u.req("leave", "POST", f"/api/v1/meetings/{mid}/leave")


async def client_room_loop(u: VU, room: dict, stop: float) -> None:
    """Участник встречи, как страница комнаты в браузере: вход (resolve, join, аватарки, слово, руки, чат, стенограмма, события жизненного цикла),
    WebSocket с подпиской на встречу и пингом раз в 30 с, /health/ready раз в 15 с, метрики раз в 30 с, сообщение в чат в среднем раз в 2 мин."""
    await asyncio.sleep(random.uniform(0, A.ramp))           # люди приходят не в одну миллисекунду
    await u.req("resolve", "GET", f"/api/v1/rooms/resolve/{room['slug']}")
    r = await u.req("join", "POST", f"/api/v1/rooms/{room['id']}/join", json={})
    if r is None or r.status_code != 200:
        return
    mid = r.json()["meeting_id"]
    for kind, path in (("avatars", f"/meetings/{mid}/avatars"), ("floor", f"/meetings/{mid}/floor"), ("hands", f"/meetings/{mid}/hands"),
                       ("chat_read", f"/meetings/{mid}/chat?limit=100"), ("transcript", f"/meetings/{mid}/transcript?after_id=0")):
        await u.req(kind, "GET", "/api/v1" + path)
    for ev in ("join_attempt", "join_ok", "backend_ws_connected"):
        await u.req("client_event", "POST", "/api/v1/client/events", json={"event": ev, "meeting_id": mid})
    ws = None

    async def connect() -> bool:
        nonlocal ws
        try:
            ws = await u.c.s.ws_connect(A.url.replace("http", "ws", 1) + "/api/v1/ws", headers={"Cookie": u.cookie}, heartbeat=None)
            await ws.send_str(json.dumps({"type": "subscribe", "meeting_id": mid}))
            return True
        except Exception as exc:  # noqa: BLE001
            ws_stats["failed"] += 1; errors.append(f"ws: {type(exc).__name__}"); ws = None
            return False
    if await connect():
        ws_stats["opened"] += 1

    async def drain():
        while time.monotonic() < stop:
            if ws is None or ws.closed:                      # как браузер: переподключение через 1–3 с и повторная подписка
                await asyncio.sleep(random.uniform(1, 3))
                if time.monotonic() < stop and await connect():
                    ws_stats["reconnected"] += 1
                continue
            m = await ws.receive()
            if m.type == aiohttp.WSMsgType.TEXT:
                ws_stats["events"] += 1
            elif m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                if time.monotonic() < stop:
                    ws_stats["dropped"] += 1
                with contextlib.suppress(Exception):
                    await ws.close()
    reader = asyncio.create_task(drain())
    nxt = {"ready": time.monotonic() + random.uniform(0, 15), "metrics": time.monotonic() + random.uniform(0, 30), "ping": time.monotonic() + 30,
           "chat": time.monotonic() + random.expovariate(1 / 120)}
    while time.monotonic() < stop:
        k = min(nxt, key=nxt.get)
        await asyncio.sleep(max(0.0, nxt[k] - time.monotonic()))
        if time.monotonic() >= stop:
            break
        if k == "ready":
            await u.req("health_ready", "GET", "/api/v1/health/ready"); nxt[k] += 15
        elif k == "metrics":
            await u.req("client_metrics", "POST", "/api/v1/client/metrics", json={"meeting_id": mid, "rtt_ms": 40, "packet_loss_pct": 0.1, "jitter_ms": 5}); nxt[k] += 30
        elif k == "ping":
            if ws is not None and not ws.closed:
                await ws.send_str('{"type":"ping"}')
            nxt[k] += 30
        else:
            await u.req("chat_send", "POST", f"/api/v1/meetings/{mid}/chat", json={"text": f"сообщение {uuid.uuid4().hex[:6]}"}); nxt[k] += random.expovariate(1 / 120)
    reader.cancel()
    if ws is not None:
        await ws.close()
    await u.req("leave", "POST", f"/api/v1/meetings/{mid}/leave")


async def client_lobby_loop(u: VU, stop: float) -> None:
    """Страница списка комнат: обновление раз в 10 с («кто сейчас в комнате»), изредка история встреч."""
    await asyncio.sleep(random.uniform(0, 10))
    while time.monotonic() < stop:
        await u.req("rooms", "GET", "/api/v1/rooms")
        if random.random() < 0.1:
            await u.req("history", "GET", "/api/v1/meetings?limit=30&offset=0")
        await asyncio.sleep(10)


async def admin_loop(c: Client, stop: float) -> None:
    a = VU(-2, c); await a.login("root")
    while time.monotonic() < stop:
        await asyncio.sleep(random.uniform(1, 3))
        await a.req("admin_perf", "GET", "/api/v1/admin/performance")
        await a.req("admin_rooms", "GET", "/api/v1/admin/rooms")


async def heavy_loop(c: Client, meetings: list, stop: float, k: int) -> None:
    """Формирование материалов: протокол и карта — модель отвечает с задержкой; параллельно обычные запросы не должны страдать."""
    i = k
    while time.monotonic() < stop and meetings:
        mid, u = meetings[i % len(meetings)]; i += 7
        kind = random.choice(["map", "protocol"])
        r = await u.req(f"heavy_{kind}_start", "POST", f"/api/v1/meetings/{mid}/map") if kind == "map" else await u.req("heavy_protocol_start", "POST", f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "кратко"})
        await asyncio.sleep(random.uniform(2, 6))


async def sample_pg(stop: float) -> None:
    if not A.pg:
        return
    import asyncpg
    con = await asyncpg.connect(A.pg)
    try:
        while time.monotonic() < stop:
            rows = await con.fetch("SELECT coalesce(state,'other') s, count(*) n, coalesce(max(extract(epoch from now()-state_change)),0)::float age FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() GROUP BY 1")
            d = {r["s"]: (r["n"], r["age"]) for r in rows}
            pg_samples.append({"t": time.time(), "total": sum(v[0] for v in d.values()), "active": d.get("active", (0, 0))[0], "idle": d.get("idle", (0, 0))[0],
                               "idle_tx": d.get("idle in transaction", (0, 0))[0], "idle_tx_age": d.get("idle in transaction", (0, 0))[1]})
            await asyncio.sleep(1)
    finally:
        await con.close()


async def sample_res(stop: float) -> None:
    pids = [int(p) for p in A.pids.split(",") if p]
    if not pids:
        return
    import psutil
    procs = [psutil.Process(p) for p in pids]
    for p in procs:
        p.cpu_percent(None)
    while time.monotonic() < stop:
        await asyncio.sleep(2)
        try:
            res_samples.append({"cpu": sum(p.cpu_percent(None) for p in procs), "rss_mb": sum(p.memory_info().rss for p in procs) / 1e6, "threads": sum(p.num_threads() for p in procs)})
        except psutil.Error:
            break


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs); return s[min(len(s) - 1, int(len(s) * q))] if s else 0.0


async def main() -> int:
    c = Client(min(A.users + 200, 4000))
    try:
        n_rooms = A.rooms or max(2, A.users // 12)       # у комнаты свой предел участников (медиа): нагрузка распределяется по комнатам
        rooms, _ = await setup(c, n_rooms)
        seeds = await seed_meetings(c, rooms, min(24, max(6, A.users // 40)))
        sem = asyncio.Semaphore(100)
        users: list[VU] = []

        async def mk(i: int):
            async with sem:
                u = VU(i, c)
                if await u.login(f"u{i:04d}"):
                    users.append(u)
        t0 = time.monotonic()
        await asyncio.gather(*[mk(i) for i in range(A.users)])
        login_s = time.monotonic() - t0
        print(f"вошло {len(users)} из {A.users} за {login_s:.1f} с", flush=True)
        stop = time.monotonic() + A.duration
        T0[0] = time.monotonic()
        tasks = [asyncio.create_task(sample_pg(stop + 5)), asyncio.create_task(sample_res(stop)), asyncio.create_task(admin_loop(c, stop))]
        tasks += [asyncio.create_task(heavy_loop(c, seeds, stop, k)) for k in range(A.heavy)]
        if A.profile == "client":
            lobby = set(random.sample(range(len(users)), int(len(users) * A.lobby_pct / 100)))
            tasks += [asyncio.create_task(client_lobby_loop(u, stop) if k in lobby else client_room_loop(u, rooms[u.i % len(rooms)], stop)) for k, u in enumerate(users)]
        else:
            tasks += [asyncio.create_task(vu_loop(u, rooms[u.i % len(rooms)], stop)) for u in users]
        await asyncio.gather(*tasks, return_exceptions=True)
        # дожидаемся окончания тяжёлых заданий и фиксируем состояние
        await asyncio.sleep(8)
    finally:
        await c.close()
    report = {"profile": A.profile, "ramp_s": A.ramp if A.profile == "client" else None, "websocket": ws_stats, "users": A.users, "logged_in": len(users), "duration_s": A.duration, "login_s": round(login_s, 1), "heavy_parallel": A.heavy, "endpoints": {}}
    total = bad5 = 0
    for k, xs in sorted(lat.items()):
        n = len(xs); c5 = sum(v for code, v in codes[k].items() if code >= 500 or code == 0); total += n; bad5 += c5
        report["endpoints"][k] = {"n": n, "p50_ms": round(statistics.median(xs) * 1000), "p95_ms": round(pct(xs, .95) * 1000), "p99_ms": round(pct(xs, .99) * 1000), "max_ms": round(max(xs) * 1000),
                                  "codes": dict(codes[k]), "errors_5xx_or_net": c5}
    user_total = sum(len(v) for k, v in lat.items() if not k.startswith(("heavy", "setup")))
    user_bad = sum(sum(v for code, v in codes[k].items() if code >= 500 or code == 0) for k in lat if not k.startswith(("heavy", "setup")))
    allu = [x for k, v in lat.items() if not k.startswith(("heavy", "setup", "login", "join", "leave", "resolve", "avatars", "floor", "hands", "client_event")) for x in v]          # установившийся режим: без пика входа и подключения к комнатам
    report["total_requests"], report["errors_5xx_or_net"] = total, bad5
    report["user_requests"], report["user_errors_pct"] = user_total, round(100 * user_bad / max(1, user_total), 3)
    report["user_latency_ms"] = {"p50": round(pct(allu, .5) * 1000), "p95": round(pct(allu, .95) * 1000), "p99": round(pct(allu, .99) * 1000)}
    if pg_samples:
        report["postgres"] = {"max_connections_seen": max(s["total"] for s in pg_samples), "max_active": max(s["active"] for s in pg_samples), "max_idle_in_transaction": max(s["idle_tx"] for s in pg_samples),
                              "max_idle_in_transaction_age_s": round(max(s["idle_tx_age"] for s in pg_samples), 1)}
    if res_samples:
        report["server"] = {"cpu_pct_avg": round(statistics.mean(s["cpu"] for s in res_samples)), "cpu_pct_max": round(max(s["cpu"] for s in res_samples)), "rss_mb_max": round(max(s["rss_mb"] for s in res_samples))}
    report["error_samples"] = sorted(set(errors))[:8]
    report["timeline_5s"] = [[k * 5, v[0], v[1]] for k, v in sorted(timeline.items())]       # [секунда, ответов без 5xx, 5xx и ошибок сети]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if A.out:
        open(A.out, "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=1))
    rc = 0
    if A.max_5xx_pct >= 0 and report["user_errors_pct"] > A.max_5xx_pct:
        print(f"ПРОВАЛ: доля 5xx/ошибок сети {report['user_errors_pct']} % > {A.max_5xx_pct} %"); rc = 1
    if A.max_idle_tx_s >= 0 and report.get("postgres", {}).get("max_idle_in_transaction_age_s", 0) > A.max_idle_tx_s:
        print(f"ПРОВАЛ: транзакция простаивала {report['postgres']['max_idle_in_transaction_age_s']} с > {A.max_idle_tx_s} с"); rc = 1
    return rc


sys.exit(asyncio.run(main()))
