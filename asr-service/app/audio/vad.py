"""VAD. Один экземпляр на аудиопоток (у Silero есть внутреннее состояние)."""
from __future__ import annotations

from typing import Callable, Protocol

import numpy as np

WINDOW = 512  # отсчётов при 16 кГц (32 мс) — требование Silero VAD


class VadModel(Protocol):
    def reset(self) -> None: ...

    def speech_prob(self, window: np.ndarray) -> float:
        """window — int16, ровно WINDOW отсчётов. Возвращает вероятность речи 0..1."""


class SileroVad:
    """Silero VAD (ONNX). Каждый экземпляр — свой ONNXWrapper со своим состоянием."""

    def __init__(self) -> None:
        import torch  # noqa: PLC0415
        from silero_vad import load_silero_vad  # noqa: PLC0415

        self._torch = torch
        self._model = load_silero_vad(onnx=True)

    def reset(self) -> None:
        self._model.reset_states()

    def speech_prob(self, window: np.ndarray) -> float:
        x = self._torch.from_numpy(window.astype(np.float32) / 32768.0)
        return float(self._model(x, 16000).item())


VadFactory = Callable[[], VadModel]


def silero_factory() -> VadFactory:
    return lambda: SileroVad()
