"""Потребитель Redis-стрима asr:segments (consumer group 'backend').

XACK только после записи в БД. Некорректные сообщения подтверждаются (иначе
застрянут навсегда) и логируются; при ошибке БД сообщение остаётся
необработанным и будет повторено: раз в минуту зависшие сообщения забираются
через XAUTOCLAIM, в том числе у потребителей, которых больше нет (имя меняется
при пересоздании контейнера). Повтор не даёт дублей: у реплики уникальный ключ.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..services.asr_bridge import SEGMENTS_STREAM
from ..services.segments import IngestResult, ingest_segment

log = logging.getLogger("app.segment_consumer")
GROUP = "backend"
CLAIM_EVERY_S = 60
CLAIM_IDLE_MS = 60_000           # сообщение без подтверждения дольше этого считается брошенным
DEAD_CONSUMER_MS = 24 * 3600_000


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
        handled += await _ingest(redis, session_maker, messages)
    return handled


async def _ingest(redis: Redis, session_maker: async_sessionmaker[AsyncSession], messages) -> int:
    handled = 0
    for msg_id, fields in messages:
        if not fields:                  # сообщение уже вытеснено из стрима
            await redis.xack(SEGMENTS_STREAM, GROUP, msg_id)
            continue
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


async def claim_stale(redis: Redis, session_maker: async_sessionmaker[AsyncSession], consumer: str, *, idle_ms: int = CLAIM_IDLE_MS, count: int = 50) -> int:
    """Забрать сообщения, которые давно никто не подтвердил, и записать их. Покрывает и сбой БД, и потребителя, которого больше нет."""
    handled, start = 0, "0-0"
    while True:
        res = await redis.xautoclaim(SEGMENTS_STREAM, GROUP, consumer, min_idle_time=idle_ms, start_id=start, count=count)
        start, messages = res[0], res[1]
        handled += await _ingest(redis, session_maker, messages)
        if not messages or start in ("0-0", b"0-0"):
            return handled


async def drop_dead_consumers(redis: Redis, consumer: str) -> None:
    """Имена прежних контейнеров копились бы в группе вечно."""
    for c in await redis.xinfo_consumers(SEGMENTS_STREAM, GROUP):
        if c.get("name") != consumer and not c.get("pending") and int(c.get("idle") or 0) > DEAD_CONSUMER_MS:
            await redis.xgroup_delconsumer(SEGMENTS_STREAM, GROUP, c["name"])


async def run_consumer(redis: Redis, session_maker: async_sessionmaker[AsyncSession], *, block_ms: int = 2000) -> None:
    consumer = f"backend-{socket.gethostname()}-{os.getpid()}"     # несколько процессов в контейнере: у каждого своё имя
    await ensure_group(redis)
    next_claim = 0.0                     # первый раз сразу: после перезапуска подобрать то, что не успели
    while True:
        try:
            if time.monotonic() >= next_claim:
                next_claim = time.monotonic() + CLAIM_EVERY_S
                await claim_stale(redis, session_maker, consumer)
                await drop_dead_consumers(redis, consumer)
            if await process_batch(redis, session_maker, consumer, block_ms=block_ms) == 0:
                await asyncio.sleep(0.01)  # гарантированная уступка циклу событий (и пауза у не-блокирующих клиентов)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка цикла приёма сегментов")
            await asyncio.sleep(2)
