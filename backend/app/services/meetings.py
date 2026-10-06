"""Жизненный цикл встречи: вход, выход, завершение, сверка присутствия.

Встреча стартует при входе первого участника и завершается, когда в ней никого
не осталось дольше MEETING_END_GRACE_SECONDS, либо явно. Источники фактов
о присутствии: webhook LiveKit (быстрый путь) и периодическая сверка с LiveKit
(авторитетный путь, не зависит от доставки webhook).
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..config import Settings
from ..models import Meeting, MeetingParticipant, Room, User, utcnow
from ..security.passwords import verify_room_password
from . import events
from .access import grant_leases
from .asr_bridge import AsrBridge
from .livekit import (
    delete_livekit_room,
    issue_user_token,
    list_present_identities,
    meeting_room_name,
    user_identity,
)
from .rooms import RoomNotFound, get_accessible_room

log = logging.getLogger("app.meetings")


class JoinError(Exception):
    def __init__(self, code: str, message: str, status: int, retry_after: int | None = None):
        super().__init__(code)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


@dataclass
class JoinResult:
    meeting: Meeting
    room: Room
    token: str
    identity: str


class MeetingService:
    def __init__(self, settings: Settings, redis: Redis, bridge: AsrBridge):
        self._s = settings
        self._r = redis
        self._bridge = bridge
        self.on_ended: Callable[[uuid.UUID], None] | None = None  # запуск финализации (экспорт, протокол)
        self.settings_svc = None  # SettingsService (срок «аренды» доступа после завершения), назначается в main

    # ------------------------------------------------------------------ вход
    async def join(self, db: AsyncSession, room_id: uuid.UUID, su: SessionUser, password: str | None) -> JoinResult:
        try:
            room = await get_accessible_room(db, room_id, su)
        except RoomNotFound:
            raise JoinError("room_not_found", "Комната не найдена или недоступна.", 404) from None

        if room.password_hash:
            await self._check_room_password(room, su, password)

        meeting = await self._active_meeting(db, room.id)
        created = False
        if meeting is None:
            meeting = Meeting(room_id=room.id, livekit_room="pending", started_by_user_id=su.user_id,
                              transcription_enabled=room.transcription_enabled, record_audio=room.record_audio)
            meeting.id = uuid.uuid4()
            meeting.livekit_room = meeting_room_name(meeting.id)
            db.add(meeting)
            try:
                await db.commit()
                created = True
            except IntegrityError:  # гонка: другой участник создал встречу первым
                await db.rollback()
                meeting = await self._active_meeting(db, room.id)
                if meeting is None:
                    raise JoinError("meeting_race", "Не удалось начать встречу, повторите.", 409) from None

        # вместимость: считаем других участников без разрыва соединения
        present = (await db.execute(
            select(func.count(func.distinct(MeetingParticipant.user_id))).where(
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.left_at.is_(None),
                MeetingParticipant.user_id != su.user_id,
            ))).scalar_one()
        if present >= room.max_participants:
            raise JoinError("room_full", "В комнате нет свободных мест.", 409)

        part = (await db.execute(select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting.id, MeetingParticipant.user_id == su.user_id,
            MeetingParticipant.left_at.is_(None)))).scalars().first()
        if part is None:
            db.add(MeetingParticipant(meeting_id=meeting.id, user_id=su.user_id))
        meeting.empty_since = None
        await db.commit()

        if created:
            await self._bridge.start(meeting_id=str(meeting.id), room_name=meeting.livekit_room, room_id=str(room.id),
                                     transcribe=meeting.transcription_enabled, record_audio=meeting.record_audio)
            log.info("Встреча начата", extra={"meeting_id": str(meeting.id), "room": room.slug})
        await events.publish(self._r, meeting.id, {"type": "participant_joined", "user_id": str(su.user_id),
                                                   "display_name": su.display_name})

        token = issue_user_token(self._s, user_id=su.user_id, display_name=su.display_name,
                                 livekit_room=meeting.livekit_room, camera_allowed=room.camera_allowed,
                                 screen_share_allowed=room.screen_share_allowed)
        return JoinResult(meeting, room, token, user_identity(su.user_id))

    async def _check_room_password(self, room: Room, su: SessionUser, password: str | None) -> None:
        key = f"roompw:fail:{room.id}:{su.user_id}"
        fails = await self._r.get(key)
        if fails is not None and int(fails) >= self._s.room_password_max_failures:
            raise JoinError("throttled", "Слишком много неверных паролей. Повторите позже.", 429,
                            retry_after=max(1, await self._r.ttl(key)))
        if not password:  # запрос без пароля — не попытка подбора, в счётчик не входит
            raise JoinError("room_password_required", "Требуется пароль комнаты.", 403)
        if not verify_room_password(room.password_hash or "", password):
            n = await self._r.incr(key)
            if n == 1:
                await self._r.expire(key, self._s.room_password_failure_window_seconds)
            raise JoinError("room_password_invalid", "Неверный пароль комнаты.", 403)
        await self._r.delete(key)

    @staticmethod
    async def _active_meeting(db: AsyncSession, room_id: uuid.UUID) -> Meeting | None:
        return (await db.execute(select(Meeting).where(Meeting.room_id == room_id, Meeting.ended_at.is_(None)))).scalars().first()

    # ----------------------------------------------------------------- выход
    async def leave(self, db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._close_participant(db, meeting_id, user_id)

    async def _close_participant(self, db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID) -> None:
        now = utcnow()
        rows = (await db.execute(select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == user_id,
            MeetingParticipant.left_at.is_(None)))).scalars().all()
        for p in rows:
            p.left_at = now
        await db.commit()
        if rows:
            await events.publish(self._r, meeting_id, {"type": "participant_left", "user_id": str(user_id)})
        await self._update_emptiness(db, meeting_id)

    async def _update_emptiness(self, db: AsyncSession, meeting_id: uuid.UUID) -> None:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None or meeting.ended_at is not None:
            return
        open_count = (await db.execute(select(func.count()).select_from(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.left_at.is_(None)))).scalar_one()
        if open_count == 0 and meeting.empty_since is None:
            meeting.empty_since = utcnow()
        elif open_count > 0:
            meeting.empty_since = None
        await db.commit()

    # ------------------------------------------------------------ завершение
    async def end(self, db: AsyncSession, meeting: Meeting, reason: str, *, kick: bool = False) -> bool:
        if meeting.ended_at is not None:
            return False
        now = utcnow()
        online = [p.user_id for p in meeting.participants if p.left_at is None]
        meeting.ended_at = now
        meeting.end_reason = reason
        for p in meeting.participants:
            if p.left_at is None:
                p.left_at = now
        await db.commit()
        if online:  # кто был в комнате в момент завершения, остаётся «на странице встречи» и может сформировать протокол
            minutes = 120
            if self.settings_svc is not None:
                minutes = (await self.settings_svc.get(db, "general")).post_meeting_access_minutes
            await grant_leases(self._r, meeting.id, online, minutes)
        await self._bridge.stop(meeting_id=str(meeting.id), room_name=meeting.livekit_room)
        await events.publish(self._r, meeting.id, {"type": "meeting_ended", "reason": reason})
        if kick:
            await delete_livekit_room(self._s, meeting.livekit_room)
        if self.on_ended is not None:
            self.on_ended(meeting.id)
        log.info("Встреча завершена", extra={"meeting_id": str(meeting.id), "reason": reason})
        return True

    # ------------------------------------------------- присутствие (webhook)
    async def on_participant_joined(self, db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID) -> None:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None or meeting.ended_at is not None:
            return
        row = (await db.execute(select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == user_id,
            MeetingParticipant.left_at.is_(None)))).scalars().first()
        if row is None:
            # webhook раньше выдачи токена или токен выдан другим экземпляром — не теряем факт входа
            if await db.get(User, user_id) is None:
                return
            row = MeetingParticipant(meeting_id=meeting_id, user_id=user_id)
            db.add(row)
        row.connected_at = row.connected_at or utcnow()
        meeting.empty_since = None
        await db.commit()

    async def on_participant_left(self, db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._close_participant(db, meeting_id, user_id)

    # ------------------------------------------------------ сверка и reaper
    async def reconcile(self, db: AsyncSession, meeting: Meeting) -> None:
        """Сверка присутствия с LiveKit и завершение опустевшей встречи."""
        present = await list_present_identities(self._s, meeting.livekit_room)
        now = utcnow()
        if present is not None:
            connect_deadline = timedelta(seconds=self._s.livekit_token_ttl_seconds + 15)
            for p in list(meeting.participants):
                if p.left_at is not None:
                    continue
                here = user_identity(p.user_id) in present
                if here:
                    p.connected_at = p.connected_at or now
                elif p.connected_at is not None or now - p.joined_at > connect_deadline:
                    p.left_at = now  # был и пропал, либо так и не подключился
            await db.commit()
            await self._update_emptiness(db, meeting.id)
            await db.refresh(meeting)
        if (meeting.ended_at is None and meeting.empty_since is not None
                and now - meeting.empty_since >= timedelta(seconds=self._s.meeting_end_grace_seconds)):
            await self.end(db, meeting, "empty")

    async def reap_once(self, db: AsyncSession) -> int:
        active = (await db.execute(select(Meeting).where(Meeting.ended_at.is_(None)))).scalars().all()
        for m in active:
            try:
                await self.reconcile(db, m)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка сверки встречи", extra={"meeting_id": str(m.id)})
                await db.rollback()
        return len(active)

    # ----------------------------------------------------- запись (транскрибация + аудио)
    async def set_recording(self, db: AsyncSession, meeting: Meeting, enabled: bool) -> bool:
        """Начать/остановить запись внутри идущей встречи (кнопки «начать/завершить запись»).

        Включить можно только если комната допускает транскрибацию. Состояние видно всем участникам.
        """
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        if enabled and not meeting.room.transcription_enabled:
            raise JoinError("recording_forbidden", "В этой комнате запись отключена администратором.", 409)
        if meeting.transcription_enabled == enabled:
            return enabled
        meeting.transcription_enabled = enabled
        await db.commit()
        await self._bridge.configure(meeting_id=str(meeting.id), room_name=meeting.livekit_room, room_id=str(meeting.room_id),
                                     transcribe=enabled, record_audio=meeting.room.record_audio and enabled)
        await events.publish(self._r, meeting.id, {"type": "recording_changed", "enabled": enabled})
        log.info("Запись переключена", extra={"meeting_id": str(meeting.id), "enabled": enabled})
        return enabled
