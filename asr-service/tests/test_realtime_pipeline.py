"""ASR не мешает звонку: запись не блокирует цикл звука, VAD меняется на лету, тайминги по сегменту, конкурентность."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

import numpy as np
import pytest

import app.pipeline as pipeline_mod
from app.audio.segmenter import SpeechSegment
from app.bench import run_concurrent
from app.config import AsrSettings
from app.inference import InferenceQueue, Job
from app.pipeline import PcmRecorder, RecorderStats, recorder_stats
from app.providers.base import TranscriptionResult
from app.runtime_config import apply_vad, current_vad, segmenter_config

from .helpers import FakeProvider, tone


# ------------------------------------------------------------------------------ запись аудио
def test_recorder_write_never_blocks_the_audio_loop_even_if_disk_is_slow(tmp_path, monkeypatch):
    gate = threading.Event()
    real_open = open

    def slow_open(*a, **kw):
        gate.wait(5)  # «зависший» диск/том
        return real_open(*a, **kw)

    monkeypatch.setattr("builtins.open", slow_open)
    rec = PcmRecorder(str(tmp_path), "m-room", "u-alice")
    t0 = time.perf_counter()
    for _ in range(100):  # 100 × 0,5 с аудио
        rec.write(np.zeros(8000, dtype=np.int16))
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, f"запись заблокировала цикл звука на {elapsed:.2f} с"
    gate.set()
    rec.close()
    monkeypatch.undo()
    assert (tmp_path / "m-room" / "u-alice.pcm").stat().st_size == 100 * 8000 * 2, "после close() всё на диске"


def test_recorder_overflow_is_explicit_not_silent_and_bounded(tmp_path, monkeypatch, caplog):
    stats = RecorderStats()
    monkeypatch.setattr(pipeline_mod, "recorder_stats", stats)
    gate = threading.Event()
    monkeypatch.setattr(PcmRecorder, "QUEUE_CHUNKS", 4)
    monkeypatch.setattr(PcmRecorder, "_run", lambda self: gate.wait(5))  # писатель стоит — очередь заполняется
    rec = PcmRecorder(str(tmp_path), "m-room", "u-bob")
    with caplog.at_level(logging.ERROR, logger="asr.pipeline"):
        for _ in range(40):
            rec.write(np.zeros(8000, dtype=np.int16))
    snap = stats.snapshot()
    assert snap["recorder_dropped"] > 0 and snap["recorder_queue_kb"] <= 4 * 16 + 1
    assert any("переполнена" in r.getMessage() for r in caplog.records), "потеря записи отражена в журнале"
    gate.set()
    rec._thread = None  # noqa: SLF001 — писатель подменён заглушкой


def test_recorder_stats_are_exposed_for_diagnostics(tmp_path):
    before = recorder_stats.snapshot()["recorder_written_mb"]
    rec = PcmRecorder(str(tmp_path), "m-room", "u-carol")
    for _ in range(10):
        rec.write(np.ones(16000, dtype=np.int16))
    rec.close()
    snap = recorder_stats.snapshot()
    assert snap["recorder_written_mb"] >= before and set(snap) == {"recorder_queue_kb", "recorder_dropped", "recorder_written_mb"}


# ----------------------------------------------------------------------------- VAD на лету
def test_vad_overrides_apply_over_env_defaults_and_can_be_cleared():
    apply_vad(None)
    s = AsrSettings(asr_vad_end_silence_ms=700, asr_vad_min_speech_ms=250, asr_vad_pad_ms=200, asr_max_segment_seconds=20)
    assert (segmenter_config(s).end_silence_ms, segmenter_config(s).max_segment_s) == (700, 20.0)
    assert apply_vad(json.dumps({"end_silence_ms": 400, "min_speech_ms": 200, "pad_ms": 120, "max_segment_seconds": 12, "threshold": 0.5}))
    c = segmenter_config(s)
    assert (c.end_silence_ms, c.min_speech_ms, c.pad_ms, c.max_segment_s, c.threshold) == (400, 200, 120, 12.0, 0.5)
    assert not apply_vad(json.dumps({"end_silence_ms": 400, "min_speech_ms": 200, "pad_ms": 120, "max_segment_seconds": 12, "threshold": 0.5})), "без изменений"
    assert apply_vad(json.dumps({"end_silence_ms": 99999, "bogus": 1, "pad_ms": 50})) and current_vad() == {"pad_ms": 50}, "вне допустимых границ — отбрасывается"
    assert not apply_vad("не json")
    assert apply_vad(None) and current_vad() == {} and segmenter_config(s).end_silence_ms == 700


# ------------------------------------------------------------------------ метрики сегмента
class TimedProvider(FakeProvider):
    def transcribe(self, pcm, *, language=None):
        time.sleep(0.01)
        return TranscriptionResult("реплика", language, timings={"preprocessing_ms": 2.0, "inference_ms": 7.0, "decoding_ms": 1.0})


async def test_segment_log_has_the_full_latency_breakdown(caplog):
    queue = InferenceQueue(TimedProvider(), max_concurrent=1, queue_size=4, language="ru")
    queue.start()
    done = []

    async def cb(jr):
        done.append(jr)

    now = time.time()
    seg = SpeechSegment(tone(2.0), now - 3.0, now - 1.0)
    with caplog.at_level(logging.INFO, logger="asr.inference"):
        queue.submit(Job("mid-1", "m-x", "u-alice", seg, cb, vad_wait_ms=420))
        for _ in range(100):
            if done:
                break
            await asyncio.sleep(0.02)
    await queue.stop()
    r = next(x for x in caplog.records if x.getMessage() == "Сегмент распознан")
    for k in ("speech_duration_ms", "vad_wait_ms", "queue_wait_ms", "preprocessing_ms", "inference_ms", "decoding_ms", "total_latency_ms", "end_to_end_ms",
              "realtime_factor", "participant", "meeting_id", "model_id", "runtime"):
        assert hasattr(r, k), k
    assert r.speech_duration_ms == 2000 and r.vad_wait_ms == 420 and r.preprocessing_ms == 2.0 and r.decoding_ms == 1.0
    assert r.end_to_end_ms == r.vad_wait_ms + r.total_latency_ms


# --------------------------------------------------------------------------- конкурентность
class Serializing(FakeProvider):
    """Модель, у которой вызовы сериализуются (общий замок): параллелизм не даёт выгоды."""

    def __init__(self):
        super().__init__()
        self._big = threading.Lock()

    def transcribe(self, pcm, *, language=None):
        with self._big:
            time.sleep(0.05)
        return TranscriptionResult("x")


class Parallel(FakeProvider):
    def transcribe(self, pcm, *, language=None):
        time.sleep(0.05)
        return TranscriptionResult("x")


def test_benchmark_shows_whether_concurrency_helps():
    pcm = tone(1.0)
    p1, p2 = run_concurrent(Parallel(), pcm, 3, 1), run_concurrent(Parallel(), pcm, 3, 2)
    assert p2["throughput_x"] > p1["throughput_x"] * 1.5, "независимые вызовы: два потока почти вдвое производительнее"
    s1, s2 = run_concurrent(Serializing(), pcm, 3, 1), run_concurrent(Serializing(), pcm, 3, 2)
    assert s2["throughput_x"] < s1["throughput_x"] * 1.3, "сериализованные вызовы: выигрыша нет"
    assert s2["avg_ms"] > s1["avg_ms"] * 1.5, "а задержка одного вызова растёт"


def test_provider_has_no_dead_lock_and_documents_thread_safety():
    import inspect

    from app.providers import gigaam

    src = inspect.getsource(gigaam)
    assert "self._lock" not in src and "threading" not in src.split("class GigaAmProvider")[0].replace("потокам", ""), "неиспользуемый замок удалён"
    assert "БЕЗ блокировки" in src and "RNNTGreedyDecoding" in src, "обоснование потокобезопасности записано в коде"


@pytest.mark.parametrize("concurrent,threads,cpus,warn", [(2, 6, 8, True), (1, 6, 8, False), (2, 3, 8, False)])
def test_threads_times_concurrency_oversubscription_rule(concurrent, threads, cpus, warn):
    assert (threads * concurrent > cpus) is warn
