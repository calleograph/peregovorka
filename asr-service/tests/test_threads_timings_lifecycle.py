"""Потоки torch, тайминги распознавания и порядок освобождения ресурсов воркера."""
from __future__ import annotations

import asyncio
import logging
import sys
import types

import numpy as np
import pytest

from app.audio.segmenter import SpeechSegment
from app.config import AsrSettings
from app.inference import InferenceQueue, Job
from app.providers.factory import build_provider
from app.providers.gigaam import GigaAmProvider
from app.publisher import SegmentPublisher
from app.worker import RoomWorker, SessionInfo

from .helpers import FakeProvider, tone


class FakeTorch(types.SimpleNamespace):
    def __init__(self, intra=4, inter=4, inter_locked=False):
        super().__init__(calls=[], intra=intra, inter=inter, inter_locked=inter_locked)

    def set_num_threads(self, n):
        self.calls.append(("intra", n))
        self.intra = n

    def set_num_interop_threads(self, n):
        self.calls.append(("interop", n))
        if self.inter_locked:
            raise RuntimeError("cannot set number of interop threads after parallel work has started")
        self.inter = n

    def get_num_threads(self):
        return self.intra

    def get_num_interop_threads(self):
        return self.inter


def test_env_values_reach_the_provider_and_torch(monkeypatch):
    monkeypatch.setenv("ASR_CPU_THREADS", "2")
    monkeypatch.setenv("ASR_INTEROP_THREADS", "1")
    s = AsrSettings()
    assert (s.asr_cpu_threads, s.asr_interop_threads) == (2, 1)
    p = build_provider(s)
    assert isinstance(p, GigaAmProvider)
    t = FakeTorch()
    assert p.apply_threads(t) == {"intra": 2, "interop": 1}
    assert ("intra", 2) in t.calls and ("interop", 1) in t.calls


def test_zero_means_library_default_and_nothing_is_called():
    p = GigaAmProvider("m", "/x", "cpu", 0, 0)
    t = FakeTorch(intra=8, inter=8)
    assert p.apply_threads(t) == {"intra": 8, "interop": 8} and t.calls == []


def test_interop_failure_is_reported_not_fatal(caplog):
    p = GigaAmProvider("m", "/x", "cpu", 2, 1)
    with caplog.at_level(logging.WARNING, logger="asr.gigaam"):
        res = p.apply_threads(FakeTorch(inter_locked=True))
    assert res["intra"] == 2 and any("INTEROP" in r.message for r in caplog.records)


async def test_every_segment_logs_timings_without_text(caplog):
    provider = FakeProvider()
    queue = InferenceQueue(provider, max_concurrent=1, queue_size=4, language="ru")
    queue.start()
    got = []

    async def on_result(jr):
        got.append(jr)

    seg = SpeechSegment(tone(2.0), 10.0, 12.0)
    with caplog.at_level(logging.INFO, logger="asr.inference"):
        assert queue.submit(Job("mid-1", "m-abc", "u-alice", seg, on_result))
        for _ in range(100):
            if got:
                break
            await asyncio.sleep(0.02)
    await queue.stop()
    rec = next(r for r in caplog.records if r.getMessage() == "Сегмент распознан")
    for k in ("meeting_id", "participant", "audio_duration_ms", "inference_ms", "realtime_factor", "queue_wait_ms", "total_latency_ms"):
        assert hasattr(rec, k), k
    assert rec.audio_duration_ms == 2000 and rec.participant == "u-alice" and rec.meeting_id == "mid-1"
    assert rec.total_latency_ms >= rec.inference_ms >= 0
    assert got[0].total_ms == rec.total_latency_ms
    assert not any("text" == k for k in rec.__dict__), "текст реплики в журнал не пишется"


class TimingRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.streams: list = []

    async def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    async def ltrim(self, k, a, b):
        self.lists[k] = self.lists[k][a:b + 1]

    async def expire(self, k, s):
        return True

    async def xadd(self, name, fields, **kw):
        self.streams.append((name, fields))


async def test_first_segment_latency_is_recorded_once_per_meeting():
    import time

    redis = TimingRedis()
    provider = FakeProvider()
    pub = SegmentPublisher(redis, lambda: provider.info)  # type: ignore[arg-type]
    pub.expect_first_segment("mid-1")
    now = time.time()
    seg = SpeechSegment(tone(1.0), now - 1.5, now - 0.5)  # реплика закончилась 0.5 с назад

    from app.inference import JobResult
    from app.providers.base import TranscriptionResult

    async def noop(_):
        return None

    job = Job("mid-1", "m-abc", "u-a", seg, noop)
    for _ in range(2):
        await pub.publish(JobResult(job, TranscriptionResult("привет", "ru"), 5, 100, 105))
    vals = redis.lists["timings:asr_first_segment_ms"]
    assert len(vals) == 1 and 400 <= float(vals[0]) <= 3000
    assert len(redis.streams) == 2


async def test_record_timing_ignores_garbage():
    redis = TimingRedis()
    pub = SegmentPublisher(redis, lambda: None)  # type: ignore[arg-type]
    await pub.record_timing("asr_join_ms", -5)
    await pub.record_timing("asr_join_ms", 10**9)
    assert redis.lists == {}


async def test_worker_awaits_stream_tasks_before_room_release():
    """Конвейеры (а с ними аудиопотоки LiveKit) завершаются ДО освобождения комнаты — иначе нативные ресурсы живут дольше владельца."""
    info = SessionInfo(meeting_id="mid", room_name="m-abc")
    w = RoomWorker(AsrSettings(), info, None, None, None)  # type: ignore[arg-type]
    order: list[str] = []

    async def pipeline():
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(0.05)  # aclose() аудиопотока занимает время
            order.append("stream_closed")

    t = asyncio.create_task(pipeline())
    await asyncio.sleep(0)
    w._streams["sid1"] = t  # noqa: SLF001
    w._stop_stream("sid1")  # callback track_unsubscribed: только отмена
    await w._drain_streams()  # noqa: SLF001
    order.append("room_released")
    assert order == ["stream_closed", "room_released"]
    assert not w._streams and not w._closing  # noqa: SLF001
