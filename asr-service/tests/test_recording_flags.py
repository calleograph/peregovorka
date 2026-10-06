from __future__ import annotations

import asyncio
import json

import numpy as np

from app.audio.segmenter import SegmenterConfig
from app.config import AsrSettings
from app.inference import InferenceQueue
from app.manager import SessionManager
from app.pipeline import Flags, ParticipantPipeline
from app.publisher import SegmentPublisher
from app.worker import SessionInfo

from .helpers import SR, EnergyVad, FakeProvider, FakeRedis, silence, tone
from .test_pipeline_and_queue import _FakeWorker, stream_of


def build(tmp_path, flags: Flags):
    provider, redis = FakeProvider(), FakeRedis()
    queue = InferenceQueue(provider, max_concurrent=1, queue_size=16, language="ru")
    queue.start()
    pub = SegmentPublisher(redis, lambda: provider.info)  # type: ignore[arg-type]
    pipe = ParticipantPipeline(meeting_id="m", room_name="m-abc", identity="u-alice", vad_factory=EnergyVad, seg_cfg=SegmenterConfig(),
                               queue=queue, publisher=pub, flags=flags, recordings_dir=str(tmp_path))
    return pipe, queue, redis


async def settle(redis, n):
    for _ in range(100):
        if len(redis.stream) >= n:
            return
        await asyncio.sleep(0.02)


async def test_audio_is_recorded_as_raw_pcm_per_participant_only_when_enabled(tmp_path):
    pipe, queue, _ = build(tmp_path, Flags(transcribe=False, record_audio=True))
    audio = np.concatenate([tone(1.0), silence(0.5)])
    await pipe.run(stream_of(audio, 100.0))
    await queue.stop()
    f = tmp_path / "m-abc" / "u-alice.pcm"
    assert f.stat().st_size == audio.size * 2
    assert np.frombuffer(f.read_bytes(), dtype="<i2")[1000] == audio[1000]


async def test_no_files_and_no_text_when_both_flags_off(tmp_path):
    pipe, queue, redis = build(tmp_path, Flags(transcribe=False, record_audio=False))
    await pipe.run(stream_of(np.concatenate([tone(1.0), silence(1.2)]), 100.0))
    await queue.stop()
    assert not (tmp_path / "m-abc").exists() and redis.stream == []


async def test_pausing_midway_flushes_started_utterance_and_discards_the_rest(tmp_path):
    flags = Flags(transcribe=True, record_audio=False)
    pipe, queue, redis = build(tmp_path, flags)

    async def frames():
        async for item in stream_of(np.concatenate([tone(1.0), silence(1.2)]), 100.0):
            yield item
        flags.transcribe = False  # «завершить запись»
        async for item in stream_of(np.concatenate([tone(1.0), silence(1.2)]), 110.0):
            yield item

    await pipe.run(frames())
    await settle(redis, 1)
    await asyncio.sleep(0.2)
    await queue.stop()
    assert len(redis.stream) == 1, "реплика после остановки записи не распознаётся"


async def test_manager_config_command_changes_running_worker_flags():
    mgr = SessionManager(AsrSettings(), FakeRedis(), None, None, EnergyVad, worker_cls=_FakeWorker)  # type: ignore[arg-type]
    _FakeWorker.flags_calls = []
    _FakeWorker.set_flags = lambda self, t, r: _FakeWorker.flags_calls.append((t, r))  # type: ignore[method-assign]
    await mgr.start_session(SessionInfo(meeting_id="m9", room_name="m-9"))
    await mgr._handle({"type": "config", "meeting_id": "m9", "payload": json.dumps({"room_name": "m-9", "transcribe": False, "record_audio": False})})
    assert _FakeWorker.flags_calls == [(False, False)]
    assert SR
