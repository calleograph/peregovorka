#!/usr/bin/env python3
"""Регрессия: одновременный вход многих людей в комнату, где ещё нет встречи (гонка на уникальном индексе «одна активная встреча на комнату»). Раньше часть запросов получала HTTP 500
(MissingGreenlet после rollback). Ожидание: все ответы 200. Нужен сервер tests/perf/devserver_perf.py на PostgreSQL. Запуск: python3 tests/perf/race_join.py [URL] [участников]"""
import asyncio, collections, sys, uuid
import aiohttp
B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18080"; N = int(sys.argv[2]) if len(sys.argv) > 2 else 30


async def main() -> int:
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=200)) as s:
        async def login(l):
            async with s.post(B + "/api/v1/auth/login", json={"login": l, "password": "pw"}) as r:
                j = await r.json(); return "; ".join(f"{k}={m.value}" for k, m in r.cookies.items()), j.get("csrf_token", "")
        root = await login("root")
        async with s.post(B + "/api/v1/admin/rooms", json={"slug": "race-" + uuid.uuid4().hex[:6], "name": "гонка", "acl": [{"subject_type": "group", "subject_ref": "cn=staff,ou=groups,dc=corp,dc=test"}]},
                          headers={"Cookie": root[0], "X-CSRF-Token": root[1]}) as r:
            room = await r.json()
        us = await asyncio.gather(*[login(f"u{i:04d}") for i in range(N)])

        async def join(u):
            async with s.post(B + f"/api/v1/rooms/{room['id']}/join", json={}, headers={"Cookie": u[0], "X-CSRF-Token": u[1]}) as r:
                body = await r.text()
                if r.status == 409 and "room_full" in body:
                    return "room_full"                      # предел участников комнаты — штатный отказ, не ошибка гонки
                if r.status != 200:
                    print(r.status, body[:200], flush=True)     # для разбора в CI: что именно ответил сервер
                return r.status
        res = collections.Counter(await asyncio.gather(*[join(u) for u in us]))
        print(dict(res))
        return 0 if res.get(200, 0) > 0 and set(res) <= {200, 'room_full'} else 1      # ошибкой считается всё, кроме успешного входа и штатного «нет мест»
sys.exit(asyncio.run(main()))
