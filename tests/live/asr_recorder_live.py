"""Настоящий ASR-воркер комнаты (asr-service/app/worker.py) для живых проверок записи: тот же код подписки на микрофоны LiveKit и тот же PcmRecorder,
но без модели распознавания (подставной провайдер и энергетический VAD). Запуск из venv ASR с установленным livekit:
    python asr_recorder_live.py <livekit_room> <каталог_записей> <файл-остановки>
Работает, пока не появится файл-остановки; после этого корректно закрывает записи."""
import asyncio, os, sys

ASR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "asr-service"))
sys.path.insert(0, ASR)
from app.audio.segmenter import SegmenterConfig  # noqa: E402
from app.config import AsrSettings  # noqa: E402
from app.inference import InferenceQueue  # noqa: E402
from app.publisher import SegmentPublisher  # noqa: E402
from app.worker import RoomWorker, SessionInfo  # noqa: E402
from tests.helpers import EnergyVad, FakeProvider, FakeRedis  # noqa: E402


async def main(room: str, rec_dir: str, stop_file: str) -> None:
    s = AsrSettings(livekit_internal_url=os.environ.get("LK_URL", "ws://127.0.0.1:7880"), livekit_api_key="devkey", livekit_api_secret="s" * 40, recordings_dir=rec_dir)
    provider = FakeProvider()
    queue = InferenceQueue(provider, max_concurrent=1, queue_size=16, language="ru")
    queue.start()
    pub = SegmentPublisher(FakeRedis(), lambda: provider.info)  # type: ignore[arg-type]
    w = RoomWorker(s, SessionInfo(meeting_id="live", room_name=room, transcribe=False, record_audio=True), queue, pub, EnergyVad)
    w.start()
    print("worker started", flush=True)
    while not os.path.exists(stop_file):
        await asyncio.sleep(0.3)
    await w.stop()
    await asyncio.sleep(1.5)                              # дать потокам записи дописать файлы
    print("worker stopped", flush=True)


asyncio.run(main(*sys.argv[1:4]))
