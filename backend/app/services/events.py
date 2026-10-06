"""Живые события приложения: Redis pub/sub → WebSocket."""
from __future__ import annotations

import json
import uuid

from redis.asyncio import Redis


def channel(meeting_id: uuid.UUID | str) -> str:
    return f"meeting:{meeting_id}:events"


async def publish(redis: Redis, meeting_id: uuid.UUID | str, event: dict) -> None:
    await redis.publish(channel(meeting_id), json.dumps(event, ensure_ascii=False, default=str))
