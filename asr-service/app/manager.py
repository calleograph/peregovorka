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
                           record_audio=bool(data.get("record_audio", False)), started_at=float(data.get("started_at") or 0),
                           e2ee_key=str(data.get("e2ee_key") or ""))
    except (KeyError, ValueError, TypeError):
        return None


class SessionManager:
    def __init__(self, settings: AsrSettings, redis: Redis, queue: InferenceQueue, publisher: SegmentPublisher,
                 vad_factory: VadFactory, worker_cls=RoomWorker):
        self._s, self._r, self._queue, self._pub, self._vad = settings, redis, queue, publisher, vad_factory
        self._worker_cls = worker_cls
        self.workers: dict[str, RoomWorker] = {}
        self.commands_ok = True          # чтение команд из Redis работает: без него новые встречи молча остаются без стенограммы
        self.commands_error: str | None = None
        self._last_id: str | None = None

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
        """Цикл команд не должен умирать молча: сбой Redis — пауза и продолжение с той же позиции, а готовность службы честно показывает проблему."""
        pause = 1.0
        while True:
            try:
                await self._run_commands()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.commands_ok, self.commands_error = False, f"{type(exc).__name__}: {exc}"[:200]
                log.warning("Сбой чтения команд Redis, повтор", extra={"error": type(exc).__name__, "pause_s": pause})
                await asyncio.sleep(pause)
                pause = min(pause * 2, 30.0)

    async def _run_commands(self) -> None:
        # Запоминаем позицию стрима ДО чтения hash — команды, пришедшие между, не теряются (идемпотентность). После сбоя продолжаем с запомненной: команды паузы не теряются.
        if self._last_id is None:
            last = await self._r.xrevrange(CONTROL_STREAM, count=1)
            self._last_id = last[0][0] if last else "0-0"
        for mid, raw in (await self._r.hgetall(SESSIONS_HASH)).items():
            info = parse_session(mid, raw)
            if info:
                await self.start_session(info)
        recheck = asyncio.create_task(self._recheck_loop())
        try:
            while True:
                resp = await self._r.xread({CONTROL_STREAM: self._last_id}, block=2000, count=50)
                self.commands_ok, self.commands_error = True, None
                for _stream, messages in resp or []:
                    for msg_id, f in messages:
                        self._last_id = msg_id
                        try:
                            await self._handle(f)
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001 — одна неудачная команда не останавливает остальные встречи
                            log.exception("Команда не выполнена", extra={"meeting": f.get("meeting_id", "")})
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
                worker.set_flags(info.transcribe, info.record_audio)       # ключ E2EE не меняется в течение встречи: переподключаться не нужно
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
