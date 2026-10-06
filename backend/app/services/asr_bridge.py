"""Мост backend → ASR через Redis (контракт: docs/ASR_CONTRACT.md).

Backend не знает про GigaAM и не импортирует код ASR.
"""
from __future__ import annotations

import json
import logging
import time

from redis.asyncio import Redis

log = logging.getLogger("app.asr_bridge")

SESSIONS_HASH = "asr:sessions"
CONTROL_STREAM = "asr:control"
SEGMENTS_STREAM = "asr:segments"
HEARTBEAT_KEY = "asr:heartbeat"


class AsrBridge:
    def __init__(self, redis: Redis):
        self._r = redis

    async def start(self, *, meeting_id: str, room_name: str, room_id: str, transcribe: bool, record_audio: bool) -> None:
        payload = {"meeting_id": meeting_id, "room_name": room_name, "room_id": room_id,
                   "transcribe": transcribe, "record_audio": record_audio, "started_at": time.time()}
        await self._r.hset(SESSIONS_HASH, meeting_id, json.dumps(payload))
        await self._r.xadd(CONTROL_STREAM, {"type": "start", "meeting_id": meeting_id, "room_name": room_name,
                                            "payload": json.dumps(payload)}, maxlen=10000, approximate=True)

    async def configure(self, *, meeting_id: str, room_name: str, room_id: str, transcribe: bool, record_audio: bool) -> None:
        """Изменить флаги идущей сессии (пауза/возобновление записи) без переподключения."""
        payload = {"meeting_id": meeting_id, "room_name": room_name, "room_id": room_id,
                   "transcribe": transcribe, "record_audio": record_audio}
        await self._r.hset(SESSIONS_HASH, meeting_id, json.dumps(payload))
        await self._r.xadd(CONTROL_STREAM, {"type": "config", "meeting_id": meeting_id, "room_name": room_name,
                                            "payload": json.dumps(payload)}, maxlen=10000, approximate=True)

    async def stop(self, *, meeting_id: str, room_name: str) -> None:
        await self._r.hdel(SESSIONS_HASH, meeting_id)
        await self._r.xadd(CONTROL_STREAM, {"type": "stop", "meeting_id": meeting_id, "room_name": room_name,
                                            "payload": "{}"}, maxlen=10000, approximate=True)

    async def heartbeat(self) -> dict | None:
        raw = await self._r.get(HEARTBEAT_KEY)
        return json.loads(raw) if raw else None
