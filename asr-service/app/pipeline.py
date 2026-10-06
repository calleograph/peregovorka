"""Конвейер ОДНОГО микрофонного трека: кадры → (запись PCM) → VAD-сегментация → очередь инференса → публикация.

Не зависит от LiveKit: на вход — асинхронный поток (int16-отсчёты, время конца кадра),
поэтому конвейер полностью тестируется без WebRTC.
"""
from __future__ import annotations

import logging
import re
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
    return kind == audio_kind and source == mic_source and identity.startswith(USER_PREFIX) \
        and not identity.startswith(SERVICE_PREFIX)


class PcmRecorder:
    """Дописывает сырой PCM (int16, 16 кГц, моно) в `<каталог>/<комната>/<identity>.pcm`.

    Без заголовка — файл не портится при аварийном обрыве; в WAV его превращает backend при завершении встречи.
    """

    def __init__(self, root: str, room_name: str, identity: str):
        self._path = Path(root) / _SAFE.sub("_", room_name) / f"{_SAFE.sub('_', identity)}.pcm"
        self._fh = None
        self.bytes_written = 0

    def write(self, samples: np.ndarray) -> None:
        try:
            if self._fh is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self._path, "ab", buffering=1 << 16)
            data = samples.astype("<i2", copy=False).tobytes()
            self._fh.write(data)
            self.bytes_written += len(data)
        except OSError as exc:
            log.error("Ошибка записи аудио", extra={"path": str(self._path), "error": str(exc)})

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None


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
        self._queue.submit(Job(self.meeting_id, self.room_name, self.identity, seg, self._publisher.publish))

    async def run(self, frames: AsyncIterator[tuple[np.ndarray, float]]) -> None:
        try:
            async for samples, t_end in frames:
                f = self._flags
                if f.record_audio and self._recorder is not None:
                    self._recorder.write(samples)
                if f.transcribe:
                    self._transcribing = True
                    for seg in self._segmenter.feed(samples, t_end):
                        self._submit(seg)
                elif self._transcribing:  # запись остановили: дорезать начатую реплику, дальше — тишина для ASR
                    self._transcribing = False
                    for seg in self._segmenter.flush():
                        self._submit(seg)
        finally:
            for seg in self._segmenter.flush():
                if self._flags.transcribe:
                    self._submit(seg)
            if self._recorder is not None:
                self._recorder.close()
