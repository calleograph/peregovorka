"""Внутри ОБРАЗА ASR: настоящий RoomWorker + настоящий Silero VAD (ONNX) + настоящая GigaAM. Участник публикует речь (selftest_ru.wav) в комнату LiveKit,
в «стенограмме» (поток asr:segments) должен появиться текст. Падает с кодом 1, если текста нет или конвейер в ошибке."""
import asyncio, difflib, os, re, sys, wave
import numpy as np
sys.path.insert(0, "/srv/asr")
from livekit import api, rtc
from app.audio.vad import silero_factory
from app.config import AsrSettings
from app.inference import InferenceQueue
from app.pipeline_health import HEALTH
from app.providers.gigaam import GigaAmProvider
from app.publisher import SegmentPublisher
from app.worker import RoomWorker, SessionInfo

URL, ROOM, ASSETS = os.environ.get("LK_URL", "ws://127.0.0.1:7880"), "m-e2e-asr", "/srv/asr/app/assets"


class Redis:
    def __init__(self): self.stream = []
    async def xadd(self, name, fields, **kw):
        self.stream.append(dict(fields)); return f"{len(self.stream)}-0"


async def main() -> int:
    s = AsrSettings(livekit_internal_url=URL, livekit_api_key="devkey", livekit_api_secret="s" * 40, recordings_dir="/tmp/rec")
    provider = GigaAmProvider("v3_e2e_rnnt", "/models", device="cpu", cpu_threads=4); provider.load()
    queue = InferenceQueue(provider, max_concurrent=1, queue_size=16, language="ru"); queue.start()
    redis = Redis()
    w = RoomWorker(s, SessionInfo(meeting_id="e2e", room_name=ROOM, transcribe=True), queue, SegmentPublisher(redis, lambda: provider.info), silero_factory())
    w.start()
    await asyncio.sleep(2)
    tok = api.AccessToken("devkey", "s" * 40).with_identity("u-speaker").with_name("Speaker").with_grants(api.VideoGrants(room_join=True, room=ROOM)).to_jwt()
    room = rtc.Room(); await room.connect(URL, tok)
    src = rtc.AudioSource(16000, 1); track = rtc.LocalAudioTrack.create_audio_track("mic", src)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    await asyncio.sleep(2)
    with wave.open(f"{ASSETS}/selftest_ru.wav") as f:
        assert f.getframerate() == 16000 and f.getnchannels() == 1
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    pcm = np.concatenate([np.zeros(16000, np.int16), pcm, np.zeros(16000 * 2, np.int16)])   # тишина до и после: VAD закрывает фразу
    for i in range(0, len(pcm), 160):
        frame = rtc.AudioFrame(pcm[i:i + 160].tobytes(), 16000, 1, len(pcm[i:i + 160]))
        await src.capture_frame(frame)
    await src.wait_for_playout()
    for _ in range(120):
        if redis.stream: break
        await asyncio.sleep(0.5)
    await asyncio.sleep(2)
    text = " ".join(str(x.get("text", "")) for x in redis.stream).strip()
    expect = open(f"{ASSETS}/selftest_ru.txt", encoding="utf-8").read()
    norm = lambda t: re.sub(r"[^а-яё ]", "", t.lower().replace("ё", "е")).split()
    sim = difflib.SequenceMatcher(None, norm(text), norm(expect)).ratio()
    print(f"ТЕКСТ В СТЕНОГРАММЕ: {text!r}\nсходство с эталоном: {sim:.2f}\nпайплайн: {HEALTH.snapshot()}")
    await room.disconnect(); await w.stop()
    ok = bool(text) and sim >= 0.6 and HEALTH.snapshot()["ready"]
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
