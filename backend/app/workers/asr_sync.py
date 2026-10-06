"""Раздаёт выбор ASR-модели из админки (БД) сервису распознавания через Redis (`asr:desired_model`).

Выбор хранится централизованно в настройках, а не в compose: при перезапуске ASR или Redis ключ восстанавливается этим воркером.
"""
from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..services.settings import SettingsService

log = logging.getLogger("app.asr_sync")
DESIRED_MODEL_KEY = "asr:desired_model"


async def publish_desired(db: AsyncSession, svc: SettingsService, redis: Redis) -> str:
    """Записать выбранную модель в Redis. Пустой выбор (модель по умолчанию из .env) ключ удаляет."""
    model = (await svc.get(db, "asr")).active_model  # type: ignore[attr-defined]
    if model:
        await redis.set(DESIRED_MODEL_KEY, model)
    else:
        await redis.delete(DESIRED_MODEL_KEY)
    return model


async def run_asr_sync(session_maker: async_sessionmaker[AsyncSession], svc: SettingsService, redis: Redis, interval: float = 30.0) -> None:
    while True:
        try:
            async with session_maker() as db:
                await publish_desired(db, svc, redis)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("Не удалось опубликовать выбор модели ASR", exc_info=True)
        await asyncio.sleep(interval)
