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

    @property
    def depth(self) -> int:
        return self._q.qsize()

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
                infer_ms = int((time.monotonic() - started) * 1000)
                self.processed += 1
                if result.text.strip():
                    await job.on_result(JobResult(job, result, int((started - job.enqueued_at) * 1000), infer_ms))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.errors += 1
                log.exception("Ошибка распознавания сегмента", extra={"identity": job.identity})
            finally:
                self.busy -= 1
                self._q.task_done()
