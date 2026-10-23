"""Состояние пулов и PostgreSQL: сколько соединений занято, как долго, сколько «idle in transaction», сколько запросов получили 503 из-за нехватки соединений.

Диагностика удержания: соединение, занятое дольше `db_hold_warn_s`, при возврате в пул пишется в журнал с указанием запроса или фоновой задачи, которая его держала, —
так видно, какой именно код забыл завершить транзакцию.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("app.db")
db_context: contextvars.ContextVar[str] = contextvars.ContextVar("db_context", default="")


@dataclass
class DbStats:
    pool_timeouts: int = 0              # запросы, которым не хватило соединения (ответили 503)
    db_unavailable: int = 0             # ответы 503 из-за недоступности PostgreSQL
    redis_unavailable: int = 0          # ответы 503 из-за недоступности Redis (после повторов клиента)
    long_holds: int = 0                 # соединения, удержанные дольше порога
    longest_hold_s: float = 0.0
    last_long_hold: dict | None = None
    checkouts: int = 0
    engines: dict[str, AsyncEngine] = field(default_factory=dict)


def _who() -> str:
    ctx = db_context.get()
    if ctx:
        return ctx
    try:
        t = asyncio.current_task()
        return f"task:{t.get_name()}" if t else "?"
    except RuntimeError:
        return "?"


def install(engine: AsyncEngine, label: str, stats: DbStats, warn_s: float) -> None:
    stats.engines[label] = engine
    pool = engine.sync_engine.pool

    @event.listens_for(pool, "checkout")
    def _out(dbapi_con, record, proxy):  # noqa: ANN001
        record.info["t0"], record.info["who"] = time.monotonic(), _who()
        stats.checkouts += 1

    @event.listens_for(pool, "checkin")
    def _in(dbapi_con, record):  # noqa: ANN001
        t0 = record.info.pop("t0", None) if record is not None else None
        if t0 is None:
            return
        held = time.monotonic() - t0
        if held > stats.longest_hold_s:
            stats.longest_hold_s = round(held, 1)
        if held >= warn_s:
            stats.long_holds += 1
            who = record.info.get("who", "?")
            stats.last_long_hold = {"pool": label, "held_s": round(held, 1), "who": who}
            log.warning("Соединение с базой удерживалось слишком долго: транзакция не завершена во время долгой операции",
                        extra={"pool": label, "held_s": round(held, 1), "who": who})


def pool_info(engine: AsyncEngine) -> dict:
    p = engine.sync_engine.pool
    try:
        size, out, over = p.size(), p.checkedout(), p.overflow()
        cap = size + p._max_overflow  # noqa: SLF001
    except AttributeError:                                    # SQLite/пулы без этих методов
        return {}
    return {"size": size, "max_overflow": p._max_overflow, "checked_out": out, "overflow": max(0, over), "capacity": cap, "saturation": round(out / cap, 2) if cap else 0.0}  # noqa: SLF001


async def pg_activity(engine: AsyncEngine | None) -> dict:
    """Что PostgreSQL видит у приложения: соединения по состояниям, самая долгая «idle in transaction», предел max_connections. Через отдельный пул проверок."""
    if engine is None:
        return {}
    q = text("SELECT coalesce(state,'other') AS state, count(*) AS n, coalesce(max(extract(epoch from now() - state_change)),0) AS age "
             "FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() GROUP BY 1")
    async with engine.connect() as c:
        rows = (await c.execute(q)).all()
        mx = (await c.execute(text("SHOW max_connections"))).scalar()
    by = {r.state: {"n": int(r.n), "oldest_s": round(float(r.age), 1)} for r in rows}
    idle_tx = by.get("idle in transaction", {"n": 0, "oldest_s": 0.0})
    return {"connections": sum(v["n"] for v in by.values()), "max_connections": int(mx), "active": by.get("active", {}).get("n", 0), "idle": by.get("idle", {}).get("n", 0),
            "idle_in_transaction": idle_tx["n"], "idle_in_transaction_oldest_s": idle_tx["oldest_s"]}


async def snapshot(stats: DbStats, health_engine: AsyncEngine | None) -> dict:
    out: dict = {"pools": {k: pool_info(e) for k, e in stats.engines.items()}, "pool_timeouts": stats.pool_timeouts, "db_unavailable": stats.db_unavailable, "redis_unavailable": stats.redis_unavailable,
                 "long_holds": stats.long_holds, "longest_hold_s": stats.longest_hold_s, "last_long_hold": stats.last_long_hold}
    try:
        out["postgres"] = {"ok": True, **await asyncio.wait_for(pg_activity(health_engine), 4)}
    except Exception as exc:  # noqa: BLE001
        out["postgres"] = {"ok": False, "error": type(exc).__name__} if health_engine is not None else {"ok": True, "note": "SQLite (разработка)"}
    return out
