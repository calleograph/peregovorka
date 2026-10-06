"""Воркер одной LiveKit-комнаты (= встречи): скрытый служебный участник.

 * входит в комнату с identity asr-<meeting> и грантом hidden (в списке участников невидим);
 * auto_subscribe выключен: подписка вручную ТОЛЬКО на микрофонные аудиотреки пользователей;
 * на каждый трек — отдельный rtc.AudioStream и отдельный ParticipantPipeline
   (поэтому «кто говорит» = identity трека, диаризация микса не нужна).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np

from .audio.segmenter import SegmenterConfig
from .audio.vad import VadFactory
from .config import AsrSettings
from .inference import InferenceQueue
from .pipeline import Flags, ParticipantPipeline, is_user_microphone_track
from .publisher import SegmentPublisher

log = logging.getLogger("asr.worker")


@dataclass(frozen=True)
class SessionInfo:
    meeting_id: str
    room_name: str
    room_id: str = ""
    transcribe: bool = True
    record_audio: bool = False


class RoomWorker:
    def __init__(self, settings: AsrSettings, info: SessionInfo, queue: InferenceQueue,
                 publisher: SegmentPublisher, vad_factory: VadFactory):
        self._s = settings
        self.info = info
        self._queue = queue
        self._publisher = publisher
        self._vad_factory = vad_factory
        self._cfg = SegmenterConfig(
            threshold=settings.asr_vad_threshold, end_silence_ms=settings.asr_vad_end_silence_ms,
            min_speech_ms=settings.asr_vad_min_speech_ms, pad_ms=settings.asr_vad_pad_ms,
            max_segment_s=settings.asr_max_segment_seconds)
        self.flags = Flags(transcribe=info.transcribe, record_audio=info.record_audio)
        self._room = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._streams: dict[str, asyncio.Task] = {}  # track sid -> задача конвейера
        self.connected = False

    def set_flags(self, transcribe: bool, record_audio: bool) -> None:
        """Пауза/возобновление записи без переподключения; при включении — подписка на уже опубликованные микрофоны."""
        self.flags.transcribe, self.flags.record_audio = transcribe, record_audio
        room = self._room
        if room is not None and self.flags.any and self.connected:
            from livekit import rtc  # noqa: PLC0415

            for p in room.remote_participants.values():
                for pub in p.track_publications.values():
                    self._maybe_subscribe(rtc, pub, p)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"room-{self.info.room_name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- подключение
    def _token(self) -> str:
        from livekit import api  # noqa: PLC0415

        return (
            api.AccessToken(self._s.livekit_api_key, self._s.livekit_api_secret)
            .with_identity(f"asr-{self.info.room_name}"[:60])
            .with_name("ASR")
            .with_grants(api.VideoGrants(room_join=True, room=self.info.room_name, can_subscribe=True,
                                         can_publish=False, can_publish_data=False, hidden=True))
            .to_jwt()
        )

    async def _run(self) -> None:
        from livekit import rtc  # noqa: PLC0415

        backoff = 2.0
        while not self._stop.is_set():
            room = rtc.Room()
            self._room = room
            disconnected = asyncio.Event()

            @room.on("track_published")
            def _on_published(publication, participant):  # noqa: ANN001
                self._maybe_subscribe(rtc, publication, participant)

            @room.on("track_subscribed")
            def _on_subscribed(track, publication, participant):  # noqa: ANN001
                if is_user_microphone_track(publication.kind, publication.source, participant.identity,
                                            audio_kind=rtc.TrackKind.KIND_AUDIO, mic_source=rtc.TrackSource.SOURCE_MICROPHONE):
                    self._start_stream(rtc, track, publication.sid, participant.identity)

            @room.on("track_unsubscribed")
            def _on_unsubscribed(track, publication, participant):  # noqa: ANN001
                self._stop_stream(publication.sid)

            @room.on("participant_disconnected")
            def _on_pd(participant):  # noqa: ANN001
                for pub in list(participant.track_publications.values()):
                    self._stop_stream(pub.sid)

            @room.on("disconnected")
            def _on_disc(reason=None):  # noqa: ANN001
                disconnected.set()

            try:
                await room.connect(self._s.livekit_internal_url, self._token(),
                                   options=rtc.RoomOptions(auto_subscribe=False))
                self.connected = True
                backoff = 2.0
                log.info("ASR-воркер вошёл в комнату", extra={"room": self.info.room_name})
                for p in room.remote_participants.values():  # кто уже в комнате
                    for pub in p.track_publications.values():
                        self._maybe_subscribe(rtc, pub, p)
                await disconnected.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Ошибка комнаты LiveKit, повтор", extra={"room": self.info.room_name, "error": type(exc).__name__})
            finally:
                self.connected = False
                for sid in list(self._streams):
                    self._stop_stream(sid)
                try:
                    await room.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    # ----------------------------------------------------------------- подписки
    def _maybe_subscribe(self, rtc, publication, participant) -> None:  # noqa: ANN001
        if self.flags.any and is_user_microphone_track(
                publication.kind, publication.source, participant.identity,
                audio_kind=rtc.TrackKind.KIND_AUDIO, mic_source=rtc.TrackSource.SOURCE_MICROPHONE):
            publication.set_subscribed(True)

    def _start_stream(self, rtc, track, sid: str, identity: str) -> None:  # noqa: ANN001
        if sid in self._streams:
            return
        pipeline = ParticipantPipeline(
            meeting_id=self.info.meeting_id, room_name=self.info.room_name, identity=identity,
            vad_factory=self._vad_factory, seg_cfg=self._cfg, queue=self._queue, publisher=self._publisher,
            flags=self.flags, recordings_dir=self._s.recordings_dir)

        async def frames() -> AsyncIterator[tuple[np.ndarray, float]]:
            stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
            try:
                async for ev in stream:
                    yield np.frombuffer(ev.frame.data, dtype=np.int16).copy(), time.time()
            finally:
                await stream.aclose()

        self._streams[sid] = asyncio.create_task(pipeline.run(frames()), name=f"track-{sid}")
        log.info("Начата транскрибация трека", extra={"room": self.info.room_name, "identity": identity})

    def _stop_stream(self, sid: str) -> None:
        task = self._streams.pop(sid, None)
        if task:
            task.cancel()
