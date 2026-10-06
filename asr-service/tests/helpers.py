from __future__ import annotations

import threading
import time

import numpy as np

from app.audio.vad import WINDOW
from app.providers.base import ModelInfo, TranscriptionResult

SR = 16000


class EnergyVad:
    """Тестовый VAD: речь = громкий отсчёт. Заменяет Silero в unit-тестах."""

    def reset(self) -> None:
        pass

    def speech_prob(self, window: np.ndarray) -> float:
        return 1.0 if np.abs(window.astype(np.int32)).mean() > 1000 else 0.0


def tone(seconds: float, amp: int = 8000) -> np.ndarray:
    t = np.arange(int(seconds * SR))
    return (amp * np.sin(2 * np.pi * 220 * t / SR)).astype(np.int16)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.int16)


def frames_of(audio: np.ndarray, frame: int = 160):
    for i in range(0, audio.size, frame):
        yield audio[i:i + frame]


class FakeProvider:
    """Фиктивный ASR: считает конкурентность и возвращает текст по длительности."""

    def __init__(self, delay: float = 0.0, fail_on: set[int] | None = None):
        self.info = ModelInfo("fake", "fake-model", "cpu")
        self.delay = delay
        self.fail_on = fail_on or set()
        self.calls = 0
        self.max_concurrent = 0
        self._cur = 0
        self._lock = threading.Lock()

    def load(self) -> None:
        pass

    def is_ready(self) -> bool:
        return True

    def transcribe(self, pcm: np.ndarray, *, language=None) -> TranscriptionResult:
        with self._lock:
            self.calls += 1
            n = self.calls
            self._cur += 1
            self.max_concurrent = max(self.max_concurrent, self._cur)
        try:
            time.sleep(self.delay)
            if n in self.fail_on:
                raise RuntimeError("boom")
            return TranscriptionResult(text=f"реплика {len(pcm) / SR:.1f}с", language=language)
        finally:
            with self._lock:
                self._cur -= 1


class FakeRedis:
    def __init__(self):
        self.stream: list[dict] = []

    async def xadd(self, name, fields, **kw):
        self.stream.append(dict(fields))
        return f"{len(self.stream)}-0"


__all__ = ["EnergyVad", "tone", "silence", "frames_of", "FakeProvider", "FakeRedis", "WINDOW", "SR"]
