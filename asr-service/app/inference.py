"""Ограниченная очередь инференса над ОДНОЙ общей копией модели.

 * не более max_concurrent одновременных вызовов provider.transcribe (пул потоков);
 * очередь ограничена: при переполнении новый сегмент отбрасывается и считается
   (защита памяти важнее одной реплики; счётчик dropped виден в /readyz и логах);
 * ошибка распознавания одного сегмента не валит остальные.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .audio.segmenter import SpeechSegment
from .providers.base import AsrProvider, TranscriptionResult

log = logging.getLogger("asr.inference")


@dataclass
class Job:
    meeting_id: str
    room_name: str
    identity: str
    segment: SpeechSegment
    on_result: Callable[["JobResult"], Awaitable[None]]
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass
class JobResult:
    job: Job
    result: TranscriptionResult
    queue_ms: int
    infer_ms: int
    total_ms: int = 0  # от постановки сегмента в очередь до готового текста


class InferenceQueue:
    def __init__(self, provider: AsrProvider, *, max_concurrent: int, queue_size: int, language: str | None):
        self._provider = provider
        self._language = language
        self._max = max_concurrent
        self._q: asyncio.Queue[Job] = asyncio.Queue(maxsize=queue_size)
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="asr-infer")
        self._tasks: list[asyncio.Task] = []
        self.processed = 0
        self.dropped = 0
        self.errors = 0
        self.busy = 0
        self._lat: deque[tuple[int, int, float]] = deque(maxlen=50)  # (infer_ms, queue_ms, длительность аудио, с)

    @property
    def depth(self) -> int:
        return self._q.qsize()

    def latency_stats(self) -> dict:
        """Средние за последние сегменты: время инференса, ожидание в очереди и RTF (инференс / длительность аудио; <1 — быстрее реального времени)."""
        if not self._lat:
            return {"avg_infer_ms": None, "avg_queue_ms": None, "rtf": None}
        n = len(self._lat)
        infer = sum(x[0] for x in self._lat) / n
        audio_ms = sum(x[2] for x in self._lat) * 1000
        return {"avg_infer_ms": round(infer), "avg_queue_ms": round(sum(x[1] for x in self._lat) / n),
                "rtf": round(sum(x[0] for x in self._lat) / audio_ms, 3) if audio_ms else None}

    def start(self) -> None:
        for i in range(self._max):
            self._tasks.append(asyncio.create_task(self._worker(), name=f"asr-worker-{i}"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._pool.shutdown(wait=False, cancel_futures=True)

    def submit(self, job: Job) -> bool:
        try:
            self._q.put_nowait(job)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            log.warning("Очередь инференса переполнена, сегмент отброшен",
                        extra={"dropped_total": self.dropped, "queue": self._q.maxsize, "identity": job.identity})
            return False

    async def _worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._q.get()
            started = time.monotonic()
            self.busy += 1
            try:
                result = await loop.run_in_executor(
                    self._pool, lambda: self._provider.transcribe(job.segment.pcm, language=self._language))
                done = time.monotonic()
                infer_ms = int((done - started) * 1000)
                queue_ms = int((started - job.enqueued_at) * 1000)
                total_ms = int((done - job.enqueued_at) * 1000)
                audio_ms = int(job.segment.duration_s * 1000)
                self.processed += 1
                self._lat.append((infer_ms, queue_ms, job.segment.duration_s))
                # Текст реплик в журнал НЕ пишется — только тайминги: по ним видно, тормозит ли распознавание.
                log.info("Сегмент распознан", extra={
                    "meeting_id": job.meeting_id, "participant": job.identity, "audio_duration_ms": audio_ms,
                    "inference_ms": infer_ms, "realtime_factor": round(infer_ms / audio_ms, 3) if audio_ms else None,
                    "queue_wait_ms": queue_ms, "total_latency_ms": total_ms, "has_text": bool(result.text.strip()),
                    "model_id": getattr(getattr(self._provider, "info", None), "model_id", ""), "runtime": getattr(getattr(self._provider, "info", None), "runtime", "")})
                if result.text.strip():
                    await job.on_result(JobResult(job, result, queue_ms, infer_ms, total_ms))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "ModelNotReadyError":  # модель не загружена/переключается: сегмент пропускается без стека ошибок
                    self.dropped += 1
                    if self.dropped % 20 == 1:
                        log.warning("Нет активной модели распознавания — сегменты пропускаются", extra={"dropped_total": self.dropped})
                else:
                    self.errors += 1
                    log.exception("Ошибка распознавания сегмента", extra={"identity": job.identity})
            finally:
                self.busy -= 1
                self._q.task_done()
