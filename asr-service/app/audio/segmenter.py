"""Нарезка потока одного участника на реплики по VAD.

Логика: гистерезис по вероятности речи, конец реплики — пауза end_silence_ms,
короткие всплески короче min_speech_ms отбрасываются, перед речью и после неё
сохраняется pad_ms контекста. Слишком длинная реплика принудительно режется
(GigaAM не принимает > 25 с), предпочтительно — в ближайшей паузе.
Тишина в ASR не уходит. Время — настенное (UTC epoch) по моменту прихода кадров,
поэтому паузы без кадров (микрофон выключен) не сбивают отметки времени.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .vad import WINDOW, VadModel

SAMPLE_RATE = 16000
WINDOW_S = WINDOW / SAMPLE_RATE  # 0.032 с


@dataclass(frozen=True)
class SegmenterConfig:
    threshold: float = 0.5
    neg_threshold_delta: float = 0.15
    end_silence_ms: int = 700
    min_speech_ms: int = 250
    pad_ms: int = 200
    max_segment_s: float = 20.0
    gap_flush_s: float = 1.0
    soft_split_after: float = 0.6   # доля max_segment_s, после которой режем в первой же паузе
    soft_split_silence_ms: int = 200


@dataclass(frozen=True)
class SpeechSegment:
    pcm: np.ndarray  # int16, 16 кГц, моно
    started_at: float  # epoch, секунды
    ended_at: float

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / SAMPLE_RATE


class SpeechSegmenter:
    def __init__(self, vad: VadModel, cfg: SegmenterConfig | None = None):
        self._vad = vad
        self._cfg = cfg or SegmenterConfig()
        self._neg = max(0.01, self._cfg.threshold - self._cfg.neg_threshold_delta)
        self._pad_windows = int(np.ceil(self._cfg.pad_ms / 1000 / WINDOW_S))
        self._pre: deque[tuple[np.ndarray, float]] = deque(maxlen=max(1, self._pad_windows))
        self._reset_state()
        self._leftover = np.zeros(0, dtype=np.int16)
        self._leftover_t0 = 0.0
        self._prev_end: float | None = None

    def _reset_state(self) -> None:
        self._in_speech = False
        self._win: list[tuple[np.ndarray, float]] = []
        self._speech_windows = 0
        self._silence_run = 0

    # ------------------------------------------------------------------ вход
    def feed(self, samples: np.ndarray, t_end: float) -> list[SpeechSegment]:
        """samples — int16 моно 16 кГц; t_end — настенное время ПОСЛЕДНЕГО отсчёта."""
        out: list[SpeechSegment] = []
        if samples.size == 0:
            return out
        t_start = t_end - samples.size / SAMPLE_RATE
        if self._prev_end is not None and t_start - self._prev_end > self._cfg.gap_flush_s:
            out.extend(self.flush())  # был провал в кадрах — реплика не может «перетечь» через него
        self._prev_end = t_end

        if self._leftover.size == 0:
            self._leftover_t0 = t_start
        data = np.concatenate([self._leftover, samples]) if self._leftover.size else samples
        n = data.size // WINDOW
        for i in range(n):
            w = data[i * WINDOW:(i + 1) * WINDOW]
            tw = self._leftover_t0 + i * WINDOW_S
            seg = self._process_window(w, tw)
            if seg is not None:
                out.append(seg)
        self._leftover = data[n * WINDOW:].copy()
        self._leftover_t0 = self._leftover_t0 + n * WINDOW_S
        return out

    def flush(self) -> list[SpeechSegment]:
        """Конец потока/провал в кадрах: завершить открытую реплику."""
        out: list[SpeechSegment] = []
        if self._in_speech:
            seg = self._finalize(trim=True)
            if seg is not None:
                out.append(seg)
        self._pre.clear()
        self._reset_state()
        self._leftover = np.zeros(0, dtype=np.int16)
        self._vad.reset()
        return out

    # ------------------------------------------------------------- состояние
    def _process_window(self, w: np.ndarray, tw: float) -> SpeechSegment | None:
        p = self._vad.speech_prob(w)
        cfg = self._cfg
        if not self._in_speech:
            if p >= cfg.threshold:
                self._in_speech = True
                self._win = list(self._pre) + [(w, tw)]
                self._pre.clear()
                self._speech_windows = 1
                self._silence_run = 0
            else:
                self._pre.append((w, tw))
            return None

        self._win.append((w, tw))
        if p >= self._neg:
            self._silence_run = 0
            if p >= cfg.threshold:
                self._speech_windows += 1
        else:
            self._silence_run += 1

        silence_ms = self._silence_run * WINDOW_S * 1000
        dur = len(self._win) * WINDOW_S
        if silence_ms >= cfg.end_silence_ms:
            return self._finalize(trim=True)
        if dur >= cfg.max_segment_s:
            return self._finalize(trim=False)
        if dur >= cfg.max_segment_s * cfg.soft_split_after and silence_ms >= cfg.soft_split_silence_ms:
            return self._finalize(trim=True)
        return None

    def _finalize(self, *, trim: bool) -> SpeechSegment | None:
        win = self._win
        if trim and self._silence_run > self._pad_windows:
            win = win[: len(win) - (self._silence_run - self._pad_windows)]
        speech_ms = self._speech_windows * WINDOW_S * 1000
        self._reset_state()
        if not win or speech_ms < self._cfg.min_speech_ms:
            return None
        pcm = np.concatenate([w for w, _ in win])
        return SpeechSegment(pcm=pcm, started_at=win[0][1], ended_at=win[-1][1] + WINDOW_S)
