"""Участник испытания: входит в комнату LiveKit, публикует звук (тон) и видео (кадры) либо принимает их; печатает JSON:
кандидаты ICE, которые прислал сервер (remote_candidate), выбранную пару (адрес, протокол) и число принятых кадров звука/видео."""
import argparse
import asyncio
import json
import math
import time

from livekit import api, rtc

p = argparse.ArgumentParser()
p.add_argument("--url"); p.add_argument("--room", default="mn"); p.add_argument("--identity"); p.add_argument("--role", choices=["pub", "sub"])
p.add_argument("--seconds", type=float, default=12); p.add_argument("--key", default="devkey"); p.add_argument("--secret", default="s" * 40)
a = p.parse_args()


async def main():
    tok = api.AccessToken(a.key, a.secret).with_identity(a.identity).with_grants(api.VideoGrants(room_join=True, room=a.room)).to_jwt()
    room = rtc.Room()
    got = {"audio": 0, "video": 0}

    async def drain(stream, kind):
        async for _ in stream:
            got[kind] += 1

    @room.on("track_subscribed")
    def _sub(track, *_):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.ensure_future(drain(rtc.AudioStream(track), "audio"))
        else:
            asyncio.ensure_future(drain(rtc.VideoStream(track), "video"))

    t0 = time.monotonic()
    err = None
    try:
        await room.connect(a.url, tok, rtc.RoomOptions(connect_timeout=30))
    except Exception as e:  # noqa: BLE001 — отчёт об ошибке подключения — часть результата
        err = repr(e)
    tasks = []
    if not err and a.role == "pub":
        asrc = rtc.AudioSource(48000, 1); vsrc = rtc.VideoSource(320, 240)
        await room.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("tone", asrc))
        await room.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track("frames", vsrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))

        async def audio():
            n = 0
            while True:
                fr = rtc.AudioFrame.create(48000, 1, 480)
                d = fr.data
                for i in range(480):
                    d[i] = int(8000 * math.sin(2 * math.pi * 440 * (n + i) / 48000))
                n += 480
                await asrc.capture_frame(fr)

        async def video():
            i = 0
            while True:
                buf = bytearray([(i * 7) % 256, 80, 160, 255]) * (320 * 240)
                vsrc.capture_frame(rtc.VideoFrame(320, 240, rtc.VideoBufferType.RGBA, buf)); i += 1
                await asyncio.sleep(1 / 15)
        tasks = [asyncio.ensure_future(audio()), asyncio.ensure_future(video())]
    await asyncio.sleep(a.seconds if not err else 0)
    out = {"identity": a.identity, "role": a.role, "error": err, "connect_s": round(time.monotonic() - t0, 1), "received": got}
    if not err:
        st = await room.get_rtc_stats()
        for side, lst in (("publisher", st.publisher_stats), ("subscriber", st.subscriber_stats)):
            cands, locs, pairs = {}, {}, []
            for s in lst:
                k = s.WhichOneof("stats")
                if k == "remote_candidate":
                    c = s.remote_candidate.candidate; cands[s.remote_candidate.rtc.id] = f"{c.protocol} {c.address}:{c.port} {c.candidate_type}"
                elif k == "local_candidate":
                    c = s.local_candidate.candidate; locs[s.local_candidate.rtc.id] = f"{c.protocol} {c.address}:{c.port} {c.candidate_type}"
                elif k == "candidate_pair":
                    pairs.append(s.candidate_pair.candidate_pair)
            sel = [f"{locs.get(x.local_candidate_id, '?')} -> {cands.get(x.remote_candidate_id, '?')} ({x.bytes_received} B in)"
                   for x in pairs if x.state == 4 and (x.nominated or x.bytes_received > 0)]  # 4 = PAIR_SUCCEEDED
            out[side] = {"server_candidates": sorted(set(cands.values())), "selected": sel}
        await room.disconnect()
    for t in tasks:
        t.cancel()
    print(json.dumps(out, ensure_ascii=False))

asyncio.run(main())
