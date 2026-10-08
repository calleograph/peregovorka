"""Служебные команды сервера (запускаются ВНУТРИ контейнера backend скриптами `scripts/…`, не через веб).

    python -m app.cli bootstrap-admin [--username admin]
        Создаёт локального администратора со случайным паролем, если его ещё нет. Печатает строки `USERNAME=…` и `PASSWORD=…`
        (пароль — только в stdout этой команды; в БД — хэш argon2id). Если администратор уже есть — печатает `EXISTS=1`.

    python -m app.cli admin-reset [--username admin] [--create] [--actor ИМЯ]
        Официальное восстановление доступа: новый случайный пароль (при первом входе его нужно сменить), завершение всех сессий этого
        администратора, снятие блокировки входа, запись в журнал аудита. LDAP не нужен. С --create создаёт администратора, если его нет.

    python -m app.cli status
        Печатает, есть ли локальный администратор (без паролей).

Права: команда выполняется только тем, кто может запустить `docker compose exec` на сервере (т. е. администратором сервера).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from redis.asyncio import Redis

from .auth.throttle import _h
from .config import Settings
from .db import create_engine, create_session_maker
from .services import local_admin as la


async def _run(args: argparse.Namespace) -> int:
    s = Settings()
    engine = create_engine(s)
    sm = create_session_maker(engine)
    redis = Redis.from_url(s.effective_redis_url, decode_responses=True)
    try:
        async with sm() as db:
            existing = await la.find_local(db)
            if args.cmd == "status":
                print(f"LOCAL_ADMIN={'1' if existing else '0'}")
                if existing:
                    print(f"USERNAME={existing.sam_account_name}")
                    print(f"MUST_CHANGE_PASSWORD={'1' if existing.must_change_password else '0'}")
                return 0

            if args.cmd == "bootstrap-admin":
                if existing is not None:
                    print("EXISTS=1")
                    print(f"USERNAME={existing.sam_account_name}")
                    return 0
                name = la.validate_username(args.username)
                password = la.generate_password()
                user = await la.create_local_admin(db, name, password, must_change=True)
                la.audit(db, "installer", "local_admin.bootstrap", str(user.id), {"user": name})
                await db.commit()
                print(f"USERNAME={name}")
                print(f"PASSWORD={password}")
                return 0

            if args.cmd == "admin-reset":
                name = args.username or (existing.sam_account_name if existing else la.DEFAULT_USERNAME)
                user = await la.find_local(db, name)
                password = la.generate_password()
                if user is None:
                    if not args.create:
                        print("Локального администратора с таким именем нет. Добавьте --create, чтобы создать нового.", file=sys.stderr)
                        return 2
                    user = await la.create_local_admin(db, la.validate_username(name), password, must_change=True)
                    action = "local_admin.create_by_console"
                else:
                    await la.set_password(db, user, password, must_change=True)
                    action = "local_admin.reset_by_console"
                la.audit(db, f"console:{args.actor or 'server'}", action, str(user.id), {"user": user.sam_account_name})
                await db.commit()
                killed = await _drop_sessions(redis, str(user.id))
                await redis.delete(*[k for u in (user.sam_account_name,) for k in (f"login:fail:user:{_h(u)}", f"login:lock:user:{_h(u)}")])
                print(f"USERNAME={user.sam_account_name}")
                print(f"PASSWORD={password}")
                print(f"SESSIONS_CLOSED={killed}")
                return 0
    finally:
        await redis.aclose()
        await engine.dispose()
    return 1


async def _drop_sessions(redis: Redis, user_id: str) -> int:
    n = 0
    for k in await redis.smembers(f"usess:{user_id}"):
        n += await redis.delete(k)
    await redis.delete(f"usess:{user_id}")
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Служебные команды Peregovorka")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap-admin")
    b.add_argument("--username", default=la.DEFAULT_USERNAME)
    r = sub.add_parser("admin-reset")
    r.add_argument("--username", default="")
    r.add_argument("--create", action="store_true")
    r.add_argument("--actor", default="")
    sub.add_parser("status")
    args = p.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except la.LocalAdminError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
