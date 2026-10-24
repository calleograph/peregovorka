"""Диктовка сообщений чата: звук из Redis (base64 PCM) → те же VAD-нарезка и очередь, что у встреч → фрагменты с номерами и «done»;
встречи важнее (занятая очередь — «busy», а не ожидание), предел одновременных диктовок, звук нигде не остаётся."""
from __future__ import annotations

import asyncio
import base64

import fakeredis
import numpy as np

from app.audio.segmenter import SegmenterConfig, SpeechSegment
from app.dictation import CONTROL, DictationManager, DictationPipeline, _Out, control_message, in_key, out_key
from app.inference import InferenceQueue, Job
from app.pipeline import Flags

from .helpers import EnergyVad, FakeProvider, silence, tone

CFG = SegmenterConfig(end_silence_ms=300, min_speech_ms=100, pad_ms=50)


async def _feed(r, sid: str, audio: np.ndarray, chunk: int = 3200) -> None:
    for i in range(0, audio.size, chunk):
        await r.xadd(in_key(sid), {"pcm": base64.b64encode(audio[i:i + chunk].tobytes()).decode()})
    await r.xadd(in_key(sid), {"eof": "1"})


async def _results(r, sid: str, timeout: float = 10.0) -> list[dict]:
    out, last = [], "0-0"
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        res = await r.xread({out_key(sid): last}, count=50)
        for _s, items in res or []:
            for mid, f in items:
                last = mid
                out.append(f)
                if f.get("type") in ("done", "error"):
                    return out
        await asyncio.sleep(0.02)
    return out


async def test_dictation_returns_numbered_fragments_then_done_and_leaves_no_audio():
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    queue = InferenceQueue(FakeProvider(), max_concurrent=1, queue_size=16, language="ru")
    queue.start()
    dm = DictationManager(r, queue, EnergyVad, CFG, queue_size=16)
    task = asyncio.create_task(dm.run())
    try:
        await asyncio.sleep(0.05)
        await r.xadd(CONTROL, control_message("s1abc", 60))
        await _feed(r, "s1abc", np.concatenate([tone(1.0), silence(0.8), tone(0.7), silence(0.8)]))
        res = await _results(r, "s1abc")
        finals = [x for x in res if x["type"] == "final"]
        assert [x["seq"] for x in finals] == ["1", "2"] and all(x["text"] for x in finals), res
        assert res[-1]["type"] == "done" and res[-1]["segments"] == "2" and res[-1]["busy"] == "0"
        assert not await r.exists(in_key("s1abc")), "звук диктовки удаляется из Redis"
        assert not dm.active
    finally:
        task.cancel()
        await queue.stop()


async def test_meetings_have_priority_busy_queue_skips_dictation_fragment():
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    queue = InferenceQueue(FakeProvider(), max_concurrent=1, queue_size=4, language="ru")     # не запущена: задания встреч «висят» в очереди
    for _ in range(2):
        queue.submit(Job("m", "m-x", "u-1", SpeechSegment(tone(0.5), 0, 0.5), lambda _jr: asyncio.sleep(0)))
    pipe = DictationPipeline(out=_Out(r, "s2"), queue=queue, queue_size=4, meeting_id="dictation", room_name="d-s2", identity="dictation", vad_factory=EnergyVad,
                             seg_cfg=CFG, flags=Flags(transcribe=True, record_audio=False), recordings_dir=None)
    pipe._submit(SpeechSegment(tone(0.5), 0, 0.5))
    await asyncio.sleep(0.05)
    res = await r.xread({out_key("s2"): "0-0"})
    assert res and res[0][1][0][1] == {"type": "busy", "seq": "1"} and pipe.pending == 0 and queue.depth == 2, "фрагмент диктовки не вытесняет встречи"


async def test_concurrent_dictations_are_limited():
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    queue = InferenceQueue(FakeProvider(), max_concurrent=1, queue_size=16, language="ru")
    dm = DictationManager(r, queue, EnergyVad, CFG, queue_size=16, max_sessions=1)
    await dm.start("a1", 30)
    await dm.start("b2", 30)
    res = await r.xread({out_key("b2"): "0-0"})
    assert res[0][1][0][1]["type"] == "error" and res[0][1][0][1]["code"] == "busy"
    assert list(dm.active) == ["a1"]
    await dm.stop_all()
