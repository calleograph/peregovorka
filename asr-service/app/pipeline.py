"""Конвейер ОДНОГО микрофонного трека: кадры → (запись PCM) → VAD-сегментация → очередь инференса → публикация.

Не зависит от LiveKit: на вход — асинхронный поток (int16-отсчёты, время конца кадра),
поэтому конвейер полностью тестируется без WebRTC.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio.segmenter import SegmenterConfig, SpeechSegment, SpeechSegmenter
from .audio.vad import VadFactory
from .inference import InferenceQueue, Job
from .publisher import SegmentPublisher

log = logging.getLogger("asr.pipeline")

SERVICE_PREFIX = "asr-"
USER_PREFIX = "u-"
# Телефонные абоненты (SIP): исходящий звонок из комнаты — "p-…", входящий — "sip_…" (identity выдаёт LiveKit SIP). Их звук распознаётся и пишется как у обычных участников.
PHONE_PREFIXES = ("p-", "sip_")
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass
class Flags:
    """Изменяемые флаги сессии: кнопка «начать/завершить запись» меняет их на лету."""

    transcribe: bool = True
    record_audio: bool = False

    @property
    def any(self) -> bool:
        return self.transcribe or self.record_audio


def is_user_microphone_track(kind, source, identity: str, *, audio_kind, mic_source) -> bool:
    """Распознаём ТОЛЬКО микрофон пользовательской identity. Экранное аудио, видео и
    служебные участники (asr-*) в распознавание не попадают."""
    return kind == audio_kind and source == mic_source and identity.startswith((USER_PREFIX, *PHONE_PREFIXES)) \
        and not identity.startswith(SERVICE_PREFIX)


class RecorderStats:
    """Сводка по записи аудио для диагностики: очередь писателя, потерянные при переполнении данные, объём записанного."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.queued_bytes = 0
        self.dropped_bytes = 0
        self.written_bytes = 0

    def add(self, queued: int = 0, dropped: int = 0, written: int = 0) -> None:
        with self._lock:
            self.queued_bytes += queued - written
            self.dropped_bytes += dropped
            self.written_bytes += written

    def snapshot(self) -> dict:
        with self._lock:
            return {"recorder_queue_kb": max(0, self.queued_bytes) // 1024, "recorder_dropped": self.dropped_bytes, "recorder_written_mb": round(self.written_bytes / 1048576, 1)}


recorder_stats = RecorderStats()


class PcmRecorder:
    """Дописывает сырой PCM (int16, 16 кГц, моно) в `<каталог>/<комната>/<identity>.pcm`.

    Без заголовка — файл не портится при аварийном обрыве; в WAV его превращает backend при завершении встречи.

    Запись НЕ выполняется в цикле обработки звука: `write()` только копирует кадр в буфер и при накоплении ~0,5 с кладёт его в ограниченную
    очередь; файловые операции делает отдельный поток-писатель. Если диск/том тормозит, звонок и распознавание не страдают; при
    переполнении очереди (диск не справляется минуты) данные теряются ЯВНО — счётчик `recorder_dropped` и запись в журнал.
    """

    FLUSH_BYTES = 16000 * 2 // 2  # ~0,5 с
    GAP_PAD_S = 0.25              # пропуск между кадрами длиннее — заполняется тишиной (микрофон выключали, DTX): файл остаётся непрерывной временной шкалой
    MAX_PAD_S = 6 * 3600
    QUEUE_CHUNKS = 240            # ~2 минуты аудио на один микрофон

    def __init__(self, root: str, room_name: str, identity: str):
        self._path = Path(root) / _SAFE.sub("_", room_name) / f"{_SAFE.sub('_', identity)}.pcm"
        self._buf = bytearray()
        self._q: queue.Queue[bytes | None] = queue.Queue(maxsize=self.QUEUE_CHUNKS)
        self._thread: threading.Thread | None = None
        self._warned = False
        self.bytes_written = 0
        self.t0: float | None = None          # настенное время первого отсчёта: по нему backend совмещает файлы участников в общую запись
        self._last_end: float | None = None

    def _ensure_thread(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"pcm-writer-{self._path.stem[:12]}", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        fh = None
        try:
            while True:
                chunk = self._q.get()
                if chunk is None:
                    break
                try:
                    if fh is None:
                        self._path.parent.mkdir(parents=True, exist_ok=True)
                        fh = open(self._path, "ab", buffering=1 << 16)
                        self._write_t0()
                    fh.write(chunk)
                except OSError as exc:
                    log.error("Ошибка записи аудио", extra={"path": str(self._path), "error": str(exc)})
                recorder_stats.add(written=len(chunk))
        finally:
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass

    def _write_t0(self) -> None:
        """Рядом с файлом — отметка времени его начала (`<identity>.t0`); пишется один раз, до первых данных."""
        side = self._path.with_suffix(".t0")
        if self.t0 is not None and not side.exists():
            try:
                side.write_text(f"{self.t0:.3f}", encoding="ascii")
            except OSError as exc:
                log.error("Не удалось записать отметку времени записи", extra={"path": str(side), "error": str(exc)})

    def _enqueue(self, chunk: bytes) -> None:
        self._ensure_thread()
        try:
            self._q.put_nowait(chunk)
            recorder_stats.add(queued=len(chunk))
        except queue.Full:
            recorder_stats.add(dropped=len(chunk))
            if not self._warned:
                self._warned = True
                log.error("Очередь записи аудио переполнена — часть записи потеряна (диск не успевает)", extra={"path": str(self._path)})

    def write(self, samples: np.ndarray, t_end: float | None = None) -> None:
        """t_end — настенное время последнего отсчёта кадра. По нему фиксируется начало файла и заполняются пропуски тишиной, чтобы общая запись шла в реальном времени."""
        data = samples.astype("<i2", copy=False).tobytes()
        if t_end is not None:
            start = t_end - samples.size / 16000
            if self.t0 is None:
                self.t0 = start
            elif self._last_end is not None and start - self._last_end > self.GAP_PAD_S:
                pad = int(min(start - self._last_end, self.MAX_PAD_S) * 16000) * 2
                self._buf += bytes(pad)
                self.bytes_written += pad
            self._last_end = max(t_end, self._last_end or t_end)
        self.bytes_written += len(data)
        self._buf += data
        if len(self._buf) >= self.FLUSH_BYTES:
            self._enqueue(bytes(self._buf))
            self._buf.clear()

    def close(self) -> None:
        """Сбросить остаток, дождаться писателя (файл полностью на диске после возврата)."""
        if self._buf:
            self._enqueue(bytes(self._buf))
            self._buf.clear()
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout=10)
            self._thread = None


class ParticipantPipeline:
    def __init__(self, *, meeting_id: str, room_name: str, identity: str, vad_factory: VadFactory,
                 seg_cfg: SegmenterConfig, queue: InferenceQueue, publisher: SegmentPublisher,
                 flags: Flags | None = None, recordings_dir: str | None = None):
        self.meeting_id, self.room_name, self.identity = meeting_id, room_name, identity
        self._segmenter = SpeechSegmenter(vad_factory(), seg_cfg)
        self._queue = queue
        self._publisher = publisher
        self._flags = flags or Flags()
        self._recorder = PcmRecorder(recordings_dir, room_name, identity) if recordings_dir else None
        self._transcribing = False
        self.segments_emitted = 0

    def _submit(self, seg: SpeechSegment) -> None:
        self.segments_emitted += 1
        wait_ms = max(0, int((time.time() - seg.ended_at) * 1000))  # сколько ждали паузы-конца реплики
        self._queue.submit(Job(self.meeting_id, self.room_name, self.identity, seg, self._publisher.publish, vad_wait_ms=wait_ms))

    async def run(self, frames: AsyncIterator[tuple[np.ndarray, float]]) -> None:
        try:
            async for samples, t_end in frames:
                f = self._flags
                if f.record_audio and self._recorder is not None:
                    self._recorder.write(samples, t_end)
                if f.transcribe:
                    self._transcribing = True
                    for seg in self._segmenter.feed(samples, t_end):
                        self._submit(seg)
                elif self._transcribing:  # запись остановили: дорезать начатую реплику, дальше — тишина для ASR
                    self._transcribing = False
                    for seg in self._segmenter.flush():
                        self._submit(seg)
        finally:
            aclose = getattr(frames, "aclose", None)  # закрыть источник кадров (аудиопоток LiveKit) явно, а не сборщиком мусора
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    log.debug("ошибка закрытия источника кадров", exc_info=True)
            for seg in self._segmenter.flush():
                if self._flags.transcribe:
                    self._submit(seg)
            if self._recorder is not None:
                self._recorder.close()
