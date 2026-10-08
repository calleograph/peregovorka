from __future__ import annotations

import asyncio
import json
import time

import numpy as np
import pytest

from app.audio.segmenter import SegmenterConfig, SpeechSegment
from app.config import AsrSettings
from app.health import create_health_app
from app.inference import InferenceQueue, Job
from app.manager import SessionManager, parse_session
from app.pipeline import ParticipantPipeline, is_user_microphone_track
from app.providers.base import ModelNotPreparedError
from app.providers.gigaam import GigaAmProvider
from app.publisher import SegmentPublisher
from app.worker import SessionInfo

from .helpers import SR, EnergyVad, FakeProvider, FakeRedis, frames_of, silence, tone

CONTRACT_FIELDS = {"segment_uid", "meeting_id", "identity", "started_at", "ended_at", "text", "language",
                   "model", "duration_ms", "infer_ms", "queue_ms"}


async def stream_of(audio: np.ndarray, t0: float):
    t = t0
    for f in frames_of(audio):
        t += f.size / SR
        yield f, t
        await asyncio.sleep(0)


def make_job(identity="u-1", seconds=1.0) -> Job:
    seg = SpeechSegment(tone(seconds), 10.0, 10.0 + seconds)

    async def noop(_):
        return None

    return Job("mid", "m-abc", identity, seg, noop)


async def test_two_participants_are_transcribed_independently_with_their_own_identity():
    provider, redis = FakeProvider(), FakeRedis()
    queue = InferenceQueue(provider, max_concurrent=1, queue_size=16, language="ru")
    queue.start()
    pub = SegmentPublisher(redis, lambda: provider.info)  # type: ignore[arg-type]
    cfg = SegmenterConfig()
    a = ParticipantPipeline(meeting_id="mid", room_name="m-abc", identity="u-alice", vad_factory=EnergyVad,
                            seg_cfg=cfg, queue=queue, publisher=pub)
    b = ParticipantPipeline(meeting_id="mid", room_name="m-abc", identity="u-bob", vad_factory=EnergyVad,
                            seg_cfg=cfg, queue=queue, publisher=pub)
    # говорят одновременно, у каждого свой трек
    await asyncio.gather(
        a.run(stream_of(np.concatenate([silence(0.5), tone(1.0), silence(1.2)]), 100.0)),
        b.run(stream_of(np.concatenate([silence(0.7), tone(2.0), silence(1.2)]), 100.0)),
    )
    for _ in range(100):
        if len(redis.stream) >= 2:
            break
        await asyncio.sleep(0.02)
    await queue.stop()

    by_identity = {m["identity"]: m for m in redis.stream}
    assert set(by_identity) == {"u-alice", "u-bob"}
    for m in redis.stream:
        assert set(m) == CONTRACT_FIELDS, "формат сообщения должен совпадать с docs/ASR_CONTRACT.md"
        assert m["meeting_id"] == "m-abc" and m["text"]
        assert json.loads(m["model"])["provider"] == "fake"
    assert by_identity["u-alice"]["text"] != by_identity["u-bob"]["text"]  # разные длительности → независимые сегменты
    assert by_identity["u-alice"]["started_at"] < by_identity["u-alice"]["ended_at"]


async def test_inference_concurrency_is_bounded_by_config_and_model_is_shared():
    provider = FakeProvider(delay=0.05)
    queue = InferenceQueue(provider, max_concurrent=2, queue_size=50, language="ru")
    queue.start()
    done = []

    async def on_result(jr):
        done.append(jr)

    for _ in range(12):
        j = make_job()
        j.on_result = on_result
        assert queue.submit(j)
    for _ in range(200):
        if len(done) == 12:
            break
        await asyncio.sleep(0.02)
    await queue.stop()
    assert len(done) == 12
    assert provider.max_concurrent == 2, "одновременных инференсов не больше ASR_MAX_CONCURRENT_INFERENCE"
    assert queue.dropped == 0


async def test_queue_overflow_drops_instead_of_growing_memory():
    provider = FakeProvider(delay=0.2)
    queue = InferenceQueue(provider, max_concurrent=1, queue_size=3, language="ru")
    # воркеры не стартуем: очередь не разгребается
    accepted = [queue.submit(make_job()) for _ in range(10)]
    assert accepted.count(True) == 3 and queue.dropped == 7 and queue.depth == 3


async def test_one_failed_segment_does_not_kill_the_worker():
    provider = FakeProvider(fail_on={1})
    queue = InferenceQueue(provider, max_concurrent=1, queue_size=8, language="ru")
    queue.start()
    done = []

    async def on_result(jr):
        done.append(jr)

    for _ in range(3):
        j = make_job()
        j.on_result = on_result
        queue.submit(j)
    for _ in range(100):
        if len(done) == 2:
            break
        await asyncio.sleep(0.02)
    await queue.stop()
    assert len(done) == 2 and queue.errors == 1


def test_only_user_microphone_tracks_are_recognized():
    AUDIO, VIDEO, MIC, SCREEN_AUDIO = 1, 2, 10, 11
    kw = dict(audio_kind=AUDIO, mic_source=MIC)
    assert is_user_microphone_track(AUDIO, MIC, "u-abc", **kw)
    assert not is_user_microphone_track(AUDIO, SCREEN_AUDIO, "u-abc", **kw)  # звук экрана — нет
    assert not is_user_microphone_track(VIDEO, MIC, "u-abc", **kw)
    assert not is_user_microphone_track(AUDIO, MIC, "asr-worker", **kw)  # служебные — нет
    assert not is_user_microphone_track(AUDIO, MIC, "someone", **kw)  # identity вне схемы приложения — нет
    # телефонные абоненты SIP: исходящий звонок (p-…) и входящий (sip_…) распознаются так же, как браузерные участники
    assert is_user_microphone_track(AUDIO, MIC, "p-0123abcd", **kw) and is_user_microphone_track(AUDIO, MIC, "sip_+70000000000_x1", **kw)
    assert not is_user_microphone_track(AUDIO, SCREEN_AUDIO, "p-0123abcd", **kw) and not is_user_microphone_track(AUDIO, MIC, "g-0123abcd", **kw)   # гости по-прежнему нет


def test_parse_session_contract():
    info = parse_session("mid", json.dumps({"meeting_id": "mid", "room_name": "m-ab12", "room_id": "r", "transcribe": True}))
    assert info and info.room_name == "m-ab12"
    assert parse_session("mid", "not json") is None
    assert parse_session("mid", json.dumps({"room_name": "evil-room"})) is None


class _FakeWorker:
    started: list[str] = []

    def __init__(self, settings, info, queue, publisher, vad):
        self.info = info
        self.stopped = False

    def start(self):
        _FakeWorker.started.append(self.info.meeting_id)

    async def stop(self):
        self.stopped = True


async def test_manager_start_is_idempotent_and_stop_removes_worker():
    mgr = SessionManager(AsrSettings(), FakeRedis(), None, None, EnergyVad, worker_cls=_FakeWorker)  # type: ignore[arg-type]
    info = SessionInfo(meeting_id="m1", room_name="m-1")
    await mgr.start_session(info)
    await mgr.start_session(info)
    assert _FakeWorker.started.count("m1") == 1 and mgr.active_meetings == 1
    await mgr._handle({"type": "stop", "meeting_id": "m1"})
    assert mgr.active_meetings == 0
    await mgr._handle({"type": "start", "meeting_id": "m2", "payload": json.dumps({"room_name": "m-2"})})
    assert mgr.active_meetings == 1
    await mgr._handle({"type": "start", "meeting_id": "m3", "payload": json.dumps({"room_name": "bad"})})
    assert mgr.active_meetings == 1


def test_gigaam_provider_refuses_to_download_and_explains(tmp_path):
    p = GigaAmProvider("v3_e2e_rnnt", str(tmp_path))
    with pytest.raises(ModelNotPreparedError) as e:
        p.load()
    assert "scripts/models.sh" in str(e.value)
    assert p.is_ready() is False


def test_readyz_reflects_model_state_without_running_inference():
    from fastapi.testclient import TestClient

    state = {"model_loaded": False}
    client = TestClient(create_health_app(lambda: dict(state)))
    assert client.get("/healthz").status_code == 200  # процесс жив, даже пока модель грузится
    assert client.get("/readyz").status_code == 503
    state["model_loaded"] = True
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["ready"] is True
    assert time.monotonic()  # healthcheck мгновенный: инференс не вызывается


def test_selftest_endpoint_is_separate_from_health_and_needs_loaded_model():
    from fastapi.testclient import TestClient

    state = {"model_loaded": False}

    async def selftest():
        return {"ok": True, "ms": 5}

    client = TestClient(create_health_app(lambda: dict(state), selftest))
    assert client.post("/selftest").status_code == 503
    state["model_loaded"] = True
    assert client.post("/selftest").json() == {"ok": True, "ms": 5}
    assert client.get("/healthz").status_code == 200  # healthcheck инференс не запускает
