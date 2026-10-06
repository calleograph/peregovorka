"""Управление сессиями: восстановление из Redis hash и команды из stream (контракт backend ⇄ ASR)."""
from __future__ import annotations

import asyncio
import json
import logging

from redis.asyncio import Redis

from .audio.vad import VadFactory
from .config import AsrSettings
from .inference import InferenceQueue
from .publisher import SegmentPublisher
from .worker import RoomWorker, SessionInfo

log = logging.getLogger("asr.manager")

SESSIONS_HASH = "asr:sessions"
CONTROL_STREAM = "asr:control"
SESSION_RECHECK_SECONDS = 30


def parse_session(meeting_id: str, raw: str | dict) -> SessionInfo | None:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        room_name = str(data["room_name"])
        if not room_name.startswith("m-"):
            return None
        return SessionInfo(meeting_id=str(data.get("meeting_id") or meeting_id), room_name=room_name,
                           room_id=str(data.get("room_id", "")), transcribe=bool(data.get("transcribe", True)),
                           record_audio=bool(data.get("record_audio", False)))
    except (KeyError, ValueError, TypeError):
        return None


class SessionManager:
    def __init__(self, settings: AsrSettings, redis: Redis, queue: InferenceQueue, publisher: SegmentPublisher,
                 vad_factory: VadFactory, worker_cls=RoomWorker):
        self._s, self._r, self._queue, self._pub, self._vad = settings, redis, queue, publisher, vad_factory
        self._worker_cls = worker_cls
        self.workers: dict[str, RoomWorker] = {}

    @property
    def active_meetings(self) -> int:
        return len(self.workers)

    async def start_session(self, info: SessionInfo) -> None:
        if info.meeting_id in self.workers:
            return  # идемпотентно
        w = self._worker_cls(self._s, info, self._queue, self._pub, self._vad)
        self.workers[info.meeting_id] = w
        w.start()
        log.info("Сессия запущена", extra={"meeting": info.meeting_id, "room": info.room_name})

    async def stop_session(self, meeting_id: str) -> None:
        w = self.workers.pop(meeting_id, None)
        if w:
            await w.stop()
            log.info("Сессия остановлена", extra={"meeting": meeting_id})

    async def stop_all(self) -> None:
        for mid in list(self.workers):
            await self.stop_session(mid)

    async def run(self) -> None:
        # Запоминаем позицию стрима ДО чтения hash — команды, пришедшие между, не теряются (идемпотентность).
        last = await self._r.xrevrange(CONTROL_STREAM, count=1)
        last_id = last[0][0] if last else "0-0"
        for mid, raw in (await self._r.hgetall(SESSIONS_HASH)).items():
            info = parse_session(mid, raw)
            if info:
                await self.start_session(info)
        recheck = asyncio.create_task(self._recheck_loop())
        try:
            while True:
                resp = await self._r.xread({CONTROL_STREAM: last_id}, block=2000, count=50)
                for _stream, messages in resp or []:
                    for msg_id, f in messages:
                        last_id = msg_id
                        await self._handle(f)
        finally:
            recheck.cancel()

    async def _handle(self, f: dict) -> None:
        kind, mid = f.get("type"), f.get("meeting_id", "")
        if kind == "start":
            info = parse_session(mid, f.get("payload") or {"room_name": f.get("room_name", "")})
            if info:
                await self.start_session(info)
            else:
                log.warning("Некорректная команда start", extra={"meeting": mid})
        elif kind == "stop":
            await self.stop_session(mid)
        elif kind == "config":
            info = parse_session(mid, f.get("payload") or {})
            worker = self.workers.get(mid)
            if info and worker:
                worker.set_flags(info.transcribe, info.record_audio)
                log.info("Флаги сессии изменены", extra={"meeting": mid, "transcribe": info.transcribe, "record_audio": info.record_audio})
            elif info:  # сессии нет (рестарт) — поднимаем с актуальными флагами
                await self.start_session(info)

    async def _recheck_loop(self) -> None:
        """Страховка от потерянной команды stop: сессии нет в hash — воркер останавливается."""
        while True:
            await asyncio.sleep(SESSION_RECHECK_SECONDS)
            try:
                for mid in list(self.workers):
                    if not await self._r.hexists(SESSIONS_HASH, mid):
                        await self.stop_session(mid)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("Ошибка сверки сессий")
