"""Второй участник длинного звонка на эмуляторе (LongCallTest): «компьютер», который показывает экран и говорит.

Показ экрана публикуется кодеком VP9, как у веб-клиента Peregovorka по умолчанию (frontend/src/screenShare.ts); звук — тон 440 Гц. Участник слушает
приложение и считает пришедшие от него звуковые кадры: так проверяется звук в обе стороны. Счётчики — по HTTP (GET /stats), их читает тест с эмулятора
по адресу 10.0.2.2. Ключи — сервера LiveKit в режиме разработки (devkey/secret).

Запуск: python3 peer.py --room long-room --port 7999 --seconds 3000"""
import argparse, asyncio, json, math, struct, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from livekit import api, rtc

W, H, FPS = 1280, 720, 5
stats = {"connected": False, "audio_frames": {}, "since": time.time()}


def pattern() -> bytes:
    """Крупная сетка с тонкими линиями — кадр «экрана» с деталями (как текст на демонстрации)."""
    row_cache = {}
    out = bytearray()
    for y in range(H):
        cy = y * 6 // H
        key = (cy, y % 120 < 2)
        if key not in row_cache:
            row = bytearray()
            for x in range(W):
                line = key[1] or x % 160 < 2
                row += bytes((235, 235, 235, 255)) if line else bytes((30 + x * 8 // W * 25, 60 + cy * 30, 160, 255))
            row_cache[key] = bytes(row)
        out += row_cache[key]
    return bytes(out)


class Stats(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(stats).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


async def count_audio(track: rtc.Track, identity: str):
    async for _ in rtc.AudioStream(track):
        stats["audio_frames"][identity] = stats["audio_frames"].get(identity, 0) + 1


async def main(a):
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Stats)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    token = api.AccessToken(a.key, a.secret).with_identity(a.identity).with_name(a.name).with_grants(api.VideoGrants(room_join=True, room=a.room)).to_jwt()
    room = rtc.Room()

    @room.on("track_subscribed")
    def _sub(track, _pub, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.ensure_future(count_audio(track, participant.identity))
            print(f"{time.strftime('%T')} слушаю {participant.identity}", flush=True)

    @room.on("participant_disconnected")
    def _gone(p):
        print(f"{time.strftime('%T')} ушёл {p.identity}", flush=True)

    await room.connect(a.url, token)
    stats["connected"] = True
    src = rtc.VideoSource(W, H)
    await room.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track("screen", src),
                                               rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE, video_codec=rtc.VideoCodec.VP8 if a.codec == "vp8" else rtc.VideoCodec.VP9))
    asrc = rtc.AudioSource(48000, 1, queue_size_ms=100)
    await room.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("mic", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    print(f"{time.strftime('%T')} PEER READY room={a.room} codec={a.codec}", flush=True)
    frame = pattern()

    async def video():
        while True:
            src.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, frame)); await asyncio.sleep(1 / FPS)

    async def audio():
        k = 0
        while True:
            pcm = b"".join(struct.pack("<h", int(2500 * math.sin(2 * math.pi * 440 * (k + i) / 48000))) for i in range(480)); k += 480
            await asrc.capture_frame(rtc.AudioFrame(pcm, 48000, 1, 480))

    tasks = [asyncio.create_task(video()), asyncio.create_task(audio())]
    await asyncio.sleep(a.seconds)
    for t in tasks:
        t.cancel()
    await room.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="ws://127.0.0.1:7880"); p.add_argument("--key", default="devkey"); p.add_argument("--secret", default="secret")
    p.add_argument("--room", default="long-room"); p.add_argument("--port", type=int, default=7999); p.add_argument("--seconds", type=int, default=3000); p.add_argument("--codec", choices=["vp9", "vp8"], default="vp9")
    p.add_argument("--identity", default="pc"); p.add_argument("--name", default="Компьютер")
    asyncio.run(main(p.parse_args()))
