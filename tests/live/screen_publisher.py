"""Участник-«компьютер», который показывает экран: входит под учётной записью стенда через API, публикует демонстрацию с крупной сеткой и подписями
(по ним на снимках видно приближение и сдвиг) и тон в микрофон. Используется в проверках мобильного просмотра демонстрации (scenario_mobile_screen.py).

Запуск: PEREGOVORKA_URL=http://127.0.0.1:5180 python3 tests/live/screen_publisher.py <логин> <пароль> <slug комнаты> [секунд=600]"""
import asyncio, json, math, os, struct, sys, urllib.request
from livekit import rtc

URL = os.environ.get("PEREGOVORKA_URL", "http://127.0.0.1:5180")
W, H = 1920, 1080


def call(method, path, body=None, cookie="", csrf=""):
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (screen-publisher)"}
    if cookie: h["Cookie"] = cookie
    if csrf: h["X-CSRF-Token"] = csrf
    req = urllib.request.Request(URL + "/api/v1" + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        ck = next((c.split(";", 1)[0] for c in (r.headers.get_all("Set-Cookie") or []) if c.startswith("vm_session=")), "")
        return json.loads(r.read() or b"null"), ck


def pattern() -> bytes:
    """Сетка 8×6 клеток с разными оттенками и тонкими линиями: мелкие детали видны только при приближении."""
    rows = []
    for y in range(H):
        row = bytearray(W * 4)
        cy = y * 6 // H
        for x in range(W):
            cx = x * 8 // W
            line = x % 240 < 3 or y % 180 < 3 or (x % 24 == 0 and y % 18 < 9)
            r, g, b = ((30 + cx * 25, 60 + cy * 30, 160) if not line else (240, 240, 240))
            row[x * 4:x * 4 + 4] = bytes((r, g, b, 255))
        rows.append(bytes(row))
    return b"".join(rows)


async def main(login, password, slug, seconds):
    me, cookie = call("POST", "/auth/login", {"login": login, "password": password})
    rooms, _ = call("GET", "/rooms", cookie=cookie)
    rid = next(r["id"] for r in rooms if r["slug"] == slug)
    join, _ = call("POST", f"/rooms/{rid}/join", {}, cookie=cookie, csrf=me.get("csrf_token", ""))
    room = rtc.Room()
    await room.connect(join["livekit_url"].replace("localhost", "127.0.0.1"), join["token"])
    src = rtc.VideoSource(W, H)
    await room.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track("screen", src), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE))
    asrc = rtc.AudioSource(48000, 1, queue_size_ms=100)
    await room.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("mic", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    buf = pattern()
    print("PUBLISHING", join["identity"], flush=True)

    async def video():
        while True:
            src.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, buf)); await asyncio.sleep(0.2)

    async def audio():
        k = 0
        while True:
            pcm = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * (k + i) / 48000))) for i in range(480)); k += 480
            await asrc.capture_frame(rtc.AudioFrame(pcm, 48000, 1, 480))

    tasks = [asyncio.create_task(video()), asyncio.create_task(audio())]
    await asyncio.sleep(seconds)
    for t in tasks: t.cancel()
    await room.disconnect()


if __name__ == "__main__":
    a = sys.argv[1:]
    asyncio.run(main(a[0], a[1], a[2], int(a[3]) if len(a) > 3 else 600))
