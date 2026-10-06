"""Параметры, которые администратор меняет на лету (без перезапуска ASR). Сейчас — VAD (деление речи на реплики).

Backend кладёт JSON в Redis (`asr:vad_config`), ASR применяет его к НОВЫМ трекам; пустое значение — берётся из .env (ASR_VAD_*).
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace

from .audio.segmenter import SegmenterConfig
from .config import AsrSettings

log = logging.getLogger("asr.runtime")
VAD_CONFIG_KEY = "asr:vad_config"
_OVERRIDES: dict = {}


def apply_vad(raw: str | None) -> bool:
    """Применить JSON из Redis. True — значения изменились. Некорректные поля пропускаются."""
    global _OVERRIDES
    new: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            return False
        limits = {"threshold": (0.01, 0.99), "end_silence_ms": (100, 5000), "min_speech_ms": (32, 5000), "pad_ms": (0, 1000), "max_segment_seconds": (3.0, 25.0)}
        for k, (lo, hi) in limits.items():
            v = data.get(k)
            if isinstance(v, (int, float)) and lo <= v <= hi:
                new[k] = v
    if new == _OVERRIDES:
        return False
    _OVERRIDES = new
    log.info("Параметры VAD изменены администратором", extra={"vad": new or "по умолчанию (.env)"})
    return True


def current_vad() -> dict:
    return dict(_OVERRIDES)


def segmenter_config(s: AsrSettings) -> SegmenterConfig:
    """Конфигурация сегментатора: значения из .env, поверх них — выбор администратора."""
    base = SegmenterConfig(threshold=s.asr_vad_threshold, end_silence_ms=s.asr_vad_end_silence_ms, min_speech_ms=s.asr_vad_min_speech_ms,
                           pad_ms=s.asr_vad_pad_ms, max_segment_s=s.asr_max_segment_seconds)
    o = _OVERRIDES
    return replace(base, threshold=o.get("threshold", base.threshold), end_silence_ms=int(o.get("end_silence_ms", base.end_silence_ms)),
                   min_speech_ms=int(o.get("min_speech_ms", base.min_speech_ms)), pad_ms=int(o.get("pad_ms", base.pad_ms)),
                   max_segment_s=float(o.get("max_segment_seconds", base.max_segment_s)))
