"""Внутренний интерфейс ASR-провайдера.

Остальной код (очередь, воркеры комнат, публикация) зависит ТОЛЬКО от этого модуля.
Чтобы добавить Whisper или другой локальный ASR, достаточно реализовать
AsrProvider и зарегистрировать его в providers/factory.py — без изменений
в логике комнат, backend и БД.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16000


class ModelNotPreparedError(RuntimeError):
    """Файлы модели отсутствуют. Автоматическая загрузка запрещена (закрытая сеть) — см. scripts/models.sh."""


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    name: str
    device: str
    version: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "name": self.name, "device": self.device, "version": self.version}


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    confidence: float | None = None


class AsrProvider(Protocol):
    info: ModelInfo

    def load(self) -> None:
        """Блокирующая загрузка модели (один раз на процесс) и прогрев."""

    def is_ready(self) -> bool: ...

    def transcribe(self, pcm: np.ndarray, *, language: str | None = None) -> TranscriptionResult:
        """Блокирующее распознавание одного сегмента.

        pcm — int16, моно, 16 кГц, длина не более max_segment_seconds.
        Вызывается из пула потоков; число одновременных вызовов ограничено InferenceQueue.
        """
