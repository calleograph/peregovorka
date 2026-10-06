"""Фоновая сверка присутствия и автозавершение опустевших встреч."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..services.meetings import MeetingService

log = logging.getLogger("app.reaper")


async def run_reaper(session_maker: async_sessionmaker[AsyncSession], service: MeetingService, interval: float = 10.0) -> None:
    while True:
        try:
            async with session_maker() as db:
                await service.reap_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка reaper")
        await asyncio.sleep(interval)
