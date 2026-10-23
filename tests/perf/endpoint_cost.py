#!/usr/bin/env python3
"""Цена одного запроса для backend: обращения выполняются по одному (без конкуренции), CPU процесса сервера меряется до и после серии.
Показывает, какой обработчик съедает процессор, — профиль по стекам этого не даёт (ORM SQLAlchemy работает в greenlet, и стек не доходит до обработчика).
Запуск: сервер tests/perf/devserver_perf.py (один процесс), затем python3 tests/perf/endpoint_cost.py --url http://127.0.0.1:18080 --pid <pid сервера> [--rooms 166] [--n 200]"""
from __future__ import annotations

import argparse, asyncio, json, time, uuid

import aiohttp, psutil

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:18080"); ap.add_argument("--pid", type=int, required=True)
ap.add_argument("--rooms", type=int, default=166); ap.add_argument("--n", type=int, default=200); ap.add_argument("--out", default="")
A = ap.parse_args()


async def main() -> None:
    proc = psutil.Process(A.pid)
    async with aiohttp.ClientSession() as s:
        async def login(name):
            async with s.post(A.url + "/api/v1/auth/login", json={"login": name, "password": "pw"}) as r:
                assert r.status == 200, await r.text()
                return {"Cookie": "; ".join(f"{k}={m.value}" for k, m in r.cookies.items()), "X-CSRF-Token": (await r.json())["csrf_token"]}
        root = await login("root")
        await s.put(A.url + "/api/v1/admin/settings/llm", headers=root, json={"enabled": True, "provider": "external", "type": "openai_compatible", "base_url": "http://llm.test/v1", "model": "m", "allow_http": True})
        rooms = []
        for k in range(A.rooms):
            async with s.post(A.url + "/api/v1/admin/rooms", headers=root, json={"slug": f"cost-{k}-{uuid.uuid4().hex[:6]}", "name": f"Комната {k}",
                                                                              "acl": [{"subject_type": "group", "subject_ref": "cn=staff,ou=groups,dc=corp,dc=test"}]}) as r:
                rooms.append(await r.json())
        # несколько участников в первой комнате и история встреч у пользователя
        users = [await login(f"u{i:04d}") for i in range(12)]
        h = users[0]
        for k in range(6):
            async with s.post(A.url + f"/api/v1/rooms/{rooms[k + 1]['id']}/join", headers=h, json={}) as r:
                mid = (await r.json())["meeting_id"]
            await s.post(A.url + f"/api/v1/meetings/{mid}/end", headers=h)
        for u in users:
            async with s.post(A.url + f"/api/v1/rooms/{rooms[0]['id']}/join", headers=u, json={}) as r:
                mid = (await r.json())["meeting_id"]
        for t in range(40):
            await s.post(A.url + f"/api/v1/meetings/{mid}/chat", headers=users[t % 12], json={"text": f"сообщение {t}"})
            await s.post(A.url + "/__dev/segment", json={"meeting_id": mid, "identity": "u-x", "text": f"фраза номер {t} про бюджет", "offset": t * 5})
        await asyncio.sleep(1.5)

        cases = [
            ("me", "GET", "/api/v1/auth/me", None),
            ("rooms", "GET", "/api/v1/rooms", None),
            ("history", "GET", "/api/v1/meetings?limit=30&offset=0", None),
            ("chat_read", "GET", f"/api/v1/meetings/{mid}/chat?limit=50", None),
            ("chat_send", "POST", f"/api/v1/meetings/{mid}/chat", {"text": "проверка"}),
            ("transcript", "GET", f"/api/v1/meetings/{mid}/transcript?after_id=0&limit=200", None),
            ("join", "POST", f"/api/v1/rooms/{rooms[0]['id']}/join", {}),
            ("resolve", "GET", f"/api/v1/rooms/resolve/{rooms[0]['slug']}", None),
            ("avatars", "GET", f"/api/v1/meetings/{mid}/avatars", None),
            ("floor", "GET", f"/api/v1/meetings/{mid}/floor", None),
            ("hands", "GET", f"/api/v1/meetings/{mid}/hands", None),
            ("health_ready", "GET", "/api/v1/health/ready", None),
            ("client_event", "POST", "/api/v1/client/events", {"event": "join_ok", "meeting_id": mid}),
            ("client_metrics", "POST", "/api/v1/client/metrics", {"meeting_id": mid, "rtt_ms": 40}),
            ("leave", "POST", f"/api/v1/meetings/{mid}/leave", None),
        ]
        res = {}
        for name, method, path, body in cases:
            hh = users[(len(res) + 1) % 12]
            for _ in range(5):              # прогрев кэшей
                async with s.request(method, A.url + path, headers=hh, json=body) as r:
                    await r.read()
            c0 = sum(proc.cpu_times()[:2]); t0 = time.perf_counter(); bad = 0
            for i in range(A.n):
                if name == "chat_send":
                    hh = users[i % 12]; await asyncio.sleep(0.07)     # предел частоты чата — на участника: отправляют по очереди 12 человек
                async with s.request(method, A.url + path, headers=hh, json=body) as r:
                    await r.read(); bad += r.status >= 400
            wall = time.perf_counter() - t0; cpu = sum(proc.cpu_times()[:2]) - c0
            wall -= 0.07 * A.n if name == "chat_send" else 0
            res[name] = {"cpu_ms": round(1000 * cpu / A.n, 2), "wall_ms": round(1000 * wall / A.n, 2), "errors": bad}
            print(f"{name:11s} CPU {res[name]['cpu_ms']:7.2f} мс  время {res[name]['wall_ms']:7.2f} мс  ошибок {bad}", flush=True)
        if A.out:
            open(A.out, "w", encoding="utf-8").write(json.dumps(res, ensure_ascii=False, indent=1))


asyncio.run(main())
