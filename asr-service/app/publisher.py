"""Публикация результатов в Redis (контракт: docs/ASR_CONTRACT.md)."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from redis.asyncio import Redis

from .inference import JobResult
from .providers.base import ModelInfo

log = logging.getLogger("asr.publisher")

SEGMENTS_STREAM = "asr:segments"
HEARTBEAT_KEY = "asr:heartbeat"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds")


class SegmentPublisher:
    def __init__(self, redis: Redis, model_info: Callable[[], ModelInfo]):
        self._r = redis
        self._model_info = model_info

    async def publish(self, jr: JobResult) -> None:
        job, seg = jr.job, jr.job.segment
        fields = {
            "segment_uid": str(uuid.uuid4()),
            "meeting_id": job.room_name,  # m-<uuid hex>; backend разбирает оба формата
            "identity": job.identity,
            "started_at": _iso(seg.started_at),
            "ended_at": _iso(seg.ended_at),
            "text": jr.result.text,
            "language": jr.result.language or "",
            "model": json.dumps(self._model_info().as_dict()),
            "duration_ms": str(int(seg.duration_s * 1000)),
            "infer_ms": str(jr.infer_ms),
            "queue_ms": str(jr.queue_ms),
        }
        await self._r.xadd(SEGMENTS_STREAM, fields, maxlen=100_000, approximate=True)


async def heartbeat_loop(redis: Redis, snapshot: Callable[[], dict], interval: float = 10.0, ttl: int = 30) -> None:
    while True:
        try:
            await redis.set(HEARTBEAT_KEY, json.dumps(snapshot()), ex=ttl)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("Не удалось записать heartbeat")
        await asyncio.sleep(interval)
