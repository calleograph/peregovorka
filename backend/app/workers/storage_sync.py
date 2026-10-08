"""Периодическая сверка хранилищ по расписанию (`storage_sync.interval_hours`). Работает в фоне, пакетами; один запуск на весь кластер (блокировка в Redis)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import StorageSyncRun, utcnow
from ..services.reconcile import Reconciler
from ..services.settings import SettingsService

log = logging.getLogger("app.storagesync")
LOCK = "lock:storage-sync"


async def due(sm: async_sessionmaker[AsyncSession], svc: SettingsService) -> tuple[bool, int, int]:
    """(пора ли, порог защиты, интервал) — по последнему ЗАВЕРШЁННОМУ запуску."""
    async with sm() as db:
        cfg = await svc.get(db, "storage_sync")
        if not cfg.enabled:  # type: ignore[attr-defined]
            return False, cfg.guard_percent, cfg.interval_hours  # type: ignore[attr-defined]
        if (await db.execute(select(StorageSyncRun.id).where(StorageSyncRun.status == "running", StorageSyncRun.started_at > utcnow() - timedelta(hours=6)))).first():
            return False, cfg.guard_percent, cfg.interval_hours  # type: ignore[attr-defined]
        last = (await db.execute(select(StorageSyncRun.started_at).order_by(StorageSyncRun.started_at.desc()).limit(1))).scalar_one_or_none()
        ok = last is None or last < utcnow() - timedelta(hours=cfg.interval_hours)  # type: ignore[attr-defined]
        return ok, cfg.guard_percent, cfg.interval_hours  # type: ignore[attr-defined]


async def run_if_due(sm, svc: SettingsService, rec: Reconciler, redis: Redis) -> bool:
    ok, guard, _ = await due(sm, svc)
    if not ok:
        return False
    if not await redis.set(LOCK, "1", nx=True, ex=6 * 3600):
        return False
    try:
        await rec.run("auto", None, guard_percent=guard)
    finally:
        await redis.delete(LOCK)
    return True


async def run_storage_sync(sm: async_sessionmaker[AsyncSession], svc: SettingsService, rec: Reconciler, redis: Redis, check_every: float = 600.0) -> None:
    await asyncio.sleep(120)     # не мешаем запуску приложения
    while True:
        try:
            await run_if_due(sm, svc, rec, redis)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка плановой сверки хранилищ")
        await asyncio.sleep(check_every)
