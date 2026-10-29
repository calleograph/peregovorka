"""Диктовка сообщений чата через то же распознавание, что и встречи (контракт: docs/ASR_CONTRACT.md, раздел «Диктовка»).

Звук приходит не из LiveKit, а из Redis: backend кладёт кадры PCM (int16, 16 кГц, моно, base64) в поток `asr:dict:<sid>:in`, результаты уходят в
`asr:dict:<sid>:out` (`final` с номером фрагмента, затем `done`). Нарезка по паузам и модель — общие со встречами (`ParticipantPipeline`, общая очередь
инференса), но встречи важнее: при заполненной наполовину очереди фрагмент диктовки не ставится, а клиенту сообщается «распознавание занято».
Звук не записывается на диск и не логируется; ключи Redis удаляются по завершении (и живут не дольше KEY_TTL при сбое).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from collections.abc import AsyncIterator

import numpy as np
from redis.asyncio import Redis

from .audio.segmenter import SegmenterConfig, SpeechSegment
from .audio.vad import VadFactory
from .inference import InferenceQueue, Job, JobResult
from .pipeline import Flags, ParticipantPipeline

log = logging.getLogger("asr.dictation")

CONTROL = "asr:dictation:control"
KEY_TTL = 600
IDLE_S = 20.0                     # нет звука столько — диктовка завершается сама
DRAIN_S = 30.0                    # после конца звука ждём распознавания не дольше
BUSY_SHARE = 0.5                  # очередь заполнена больше чем наполовину — фрагмент диктовки не ставим (встречи важнее)


def in_key(sid: str) -> str:
    return f"asr:dict:{sid}:in"


def out_key(sid: str) -> str:
    return f"asr:dict:{sid}:out"


class _Out:
    """Результаты одной диктовки: фрагменты по порядку постановки (номер фрагмента — защита клиента от повторов)."""

    def __init__(self, redis: Redis, sid: str):
        self._r, self._key = redis, out_key(sid)

    async def put(self, **fields) -> None:
        await self._r.xadd(self._key, {k: str(v) for k, v in fields.items()}, maxlen=500, approximate=True)
        await self._r.expire(self._key, KEY_TTL)


class DictationPipeline(ParticipantPipeline):
    """Тот же конвейер, но каждое задание сообщает о завершении, а при занятой очереди фрагмент не ставится."""

    def __init__(self, *, out: _Out, queue: InferenceQueue, queue_size: int, **kw):
        super().__init__(queue=queue, publisher=self, **kw)       # publisher.publish — ниже: результаты уходят в поток сессии
        self._out, self._qsize = out, queue_size
        self.pending = 0
        self.seq = 0
        self.busy_dropped = 0
        self._order: dict[int, int] = {}

    async def publish(self, jr: JobResult) -> None:                 # вызывает очередь инференса для фрагмента с текстом
        await self._out.put(type="final", seq=self._order.get(id(jr.job), 0), text=jr.result.text.strip())

    def _done(self) -> None:
        self.pending -= 1

    def _submit(self, seg: SpeechSegment) -> None:
        self.segments_emitted += 1
        self.seq += 1
        if self._queue.depth >= self._qsize * BUSY_SHARE:
            self.busy_dropped += 1
            asyncio.get_running_loop().create_task(self._out.put(type="busy", seq=self.seq))
            return
        job = Job(self.meeting_id, self.room_name, self.identity, seg, self.publish, vad_wait_ms=max(0, int((time.time() - seg.ended_at) * 1000)),
                  on_done=self._done)
        self._order[id(job)] = self.seq
        self.pending += 1
        if not self._queue.submit(job):
            self.pending -= 1
            self.busy_dropped += 1
            asyncio.get_running_loop().create_task(self._out.put(type="busy", seq=self.seq))


class DictationManager:
    def __init__(self, redis: Redis, queue: InferenceQueue, vad_factory: VadFactory, seg_cfg, *, queue_size: int, max_sessions: int = 8):
        self._r, self._queue, self._vad, self._seg_cfg, self._qsize = redis, queue, vad_factory, seg_cfg, queue_size
        self._max = max_sessions
        self.active: dict[str, asyncio.Task] = {}
        self.stats = {"sessions": 0, "rejected": 0, "segments": 0, "busy": 0}

    def _cfg(self) -> SegmenterConfig:
        return self._seg_cfg() if callable(self._seg_cfg) else self._seg_cfg

    async def run(self) -> None:
        # старые команды не повторяем (прерванные перезапуском диктовки backend завершит по таймауту), но позицию запоминаем явно, а не «$» в каждом
        # чтении: иначе команда, пришедшая между двумя чтениями (или в паузу после сбоя), терялась. Как в manager.py.
        tail = await self._r.xrevrange(CONTROL, count=1)
        last = tail[0][0] if tail else "0-0"
        while True:
            try:
                # 3 с, а не 5: redis-py 8 обрывает блокирующее чтение по своему 5-секундному пределу (TimeoutError в журнале каждые 6 с)
                res = await self._r.xread({CONTROL: last}, block=3000, count=20)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Диктовка: сбой чтения команд Redis", extra={"error": type(exc).__name__})
                await asyncio.sleep(1.0)
                continue
            for _stream, items in res or []:
                for mid, fields in items:
                    last = mid
                    sid = str(fields.get("sid") or "")
                    if fields.get("type") == "start" and sid and sid not in self.active:
                        await self.start(sid, float(fields.get("max_seconds") or 120))

    async def start(self, sid: str, max_seconds: float) -> None:
        out = _Out(self._r, sid)
        if len(self.active) >= self._max:
            self.stats["rejected"] += 1
            await out.put(type="error", code="busy", message="Распознавание сейчас занято, попробуйте позже")
            return
        self.stats["sessions"] += 1
        task = asyncio.create_task(self._session(sid, out, max_seconds), name=f"dictation-{sid[:8]}")
        self.active[sid] = task
        task.add_done_callback(lambda _t: self.active.pop(sid, None))

    async def _frames(self, sid: str, max_seconds: float) -> AsyncIterator[tuple[np.ndarray, float]]:
        key, last, started, heard = in_key(sid), "0-0", time.monotonic(), time.monotonic()
        while time.monotonic() - started < max_seconds:
            res = await self._r.xread({key: last}, block=1000, count=50)
            if not res:
                if time.monotonic() - heard > IDLE_S:
                    return
                continue
            for _stream, items in res:
                for mid, fields in items:
                    last = mid
                    if fields.get("eof"):
                        return
                    try:
                        pcm = np.frombuffer(base64.b64decode(fields.get("pcm") or ""), dtype=np.int16).copy()
                    except (binascii.Error, ValueError):
                        continue
                    if pcm.size:
                        heard = time.monotonic()
                        yield pcm, time.time()

    async def _session(self, sid: str, out: _Out, max_seconds: float) -> None:
        pipe = DictationPipeline(out=out, queue=self._queue, queue_size=self._qsize, meeting_id="dictation", room_name=f"d-{sid[:12]}", identity="dictation",
                                 vad_factory=self._vad, seg_cfg=self._cfg(), flags=Flags(transcribe=True, record_audio=False), recordings_dir=None)
        try:
            await pipe.run(self._frames(sid, max_seconds))           # в конце сегментатор дорезает последнюю фразу
            deadline = time.monotonic() + DRAIN_S
            while pipe.pending > 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            await out.put(type="done", segments=pipe.seq, busy=pipe.busy_dropped, timeout=int(pipe.pending > 0))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Диктовка: сбой конвейера")
            await out.put(type="error", code="failed", message=f"Сбой распознавания ({type(exc).__name__})")
        finally:
            self.stats["segments"] += pipe.seq
            self.stats["busy"] += pipe.busy_dropped
            await self._r.delete(in_key(sid))

    async def stop_all(self) -> None:
        for t in list(self.active.values()):
            t.cancel()
        for t in list(self.active.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


def control_message(sid: str, max_seconds: int) -> dict:
    """Команда начала диктовки (её же пишет backend) — для тестов и документации контракта."""
    return {"type": "start", "sid": sid, "max_seconds": str(max_seconds), "payload": json.dumps({"sid": sid})}
