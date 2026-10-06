"""Потребитель Redis-стрима asr:segments (consumer group 'backend').

XACK только после записи в БД. Некорректные сообщения подтверждаются (иначе
застрянут навсегда) и логируются; при ошибке БД сообщение остаётся
необработанным и будет повторено.
"""
from __future__ import annotations

import asyncio
import logging
import socket

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..services.asr_bridge import SEGMENTS_STREAM
from ..services.segments import IngestResult, ingest_segment

log = logging.getLogger("app.segment_consumer")
GROUP = "backend"


async def ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(SEGMENTS_STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def process_batch(redis: Redis, session_maker: async_sessionmaker[AsyncSession], consumer: str,
                        *, read_id: str = ">", block_ms: int = 2000, count: int = 50) -> int:
    resp = await redis.xreadgroup(GROUP, consumer, {SEGMENTS_STREAM: read_id}, count=count,
                                  block=block_ms if read_id == ">" else None)
    handled = 0
    for _stream, messages in resp or []:
        for msg_id, fields in messages:
            try:
                async with session_maker() as db:
                    result = await ingest_segment(db, redis, fields)
            except Exception:  # noqa: BLE001 — БД недоступна: оставляем без ACK
                log.exception("Ошибка записи сегмента, будет повтор", extra={"msg_id": msg_id})
                await asyncio.sleep(1)
                continue
            if result is IngestResult.REJECTED:
                log.warning("Сегмент отклонён", extra={"msg_id": msg_id})
            await redis.xack(SEGMENTS_STREAM, GROUP, msg_id)
            handled += 1
    return handled


async def run_consumer(redis: Redis, session_maker: async_sessionmaker[AsyncSession], *, block_ms: int = 2000) -> None:
    consumer = f"backend-{socket.gethostname()}"
    await ensure_group(redis)
    # Сначала — неподтверждённые сообщения этого consumer'а (после рестарта).
    while True:
        try:
            if await process_batch(redis, session_maker, consumer, read_id="0") == 0:
                break
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка обработки pending")
            await asyncio.sleep(2)
            break
    while True:
        try:
            if await process_batch(redis, session_maker, consumer, block_ms=block_ms) == 0:
                await asyncio.sleep(0.01)  # гарантированная уступка циклу событий (и пауза у не-блокирующих клиентов)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка цикла приёма сегментов")
            await asyncio.sleep(2)
