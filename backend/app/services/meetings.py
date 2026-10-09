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
from ..models import GuestParticipant, Meeting, MeetingParticipant, Room, User, utcnow
from ..security.passwords import verify_room_password
from . import events, roles, temp_rooms
from .access import grant_leases
from .asr_bridge import AsrBridge
from .livekit import (
    delete_livekit_room,
    guest_identity,
    issue_guest_token,
    issue_user_token,
    list_present_identities,
    meeting_room_name,
    parse_guest_identity,
    parse_phone_identity,
    parse_user_identity,
    remove_participant,
    set_publish_permission,
    user_identity,
)
from .rooms import RoomNotFound, acl_allows, get_accessible_room

log = logging.getLogger("app.meetings")

GUEST_REVOKE_TTL = 24 * 3600
STATE_TTL = 3 * 24 * 3600      # состояние встречи в Redis (слово, привилегированные); сбрасывается и при завершении встречи
KICK_BLOCK_SECONDS = 600       # удалённый руководителем сотрудник не может сразу вернуться


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
    guest: GuestParticipant | None = None
    sources: list[str] | None = None   # что участник может публиковать сейчас (пусто — слушатель)
    privileged: bool = False           # руководитель комнаты или администратор
    floor: bool = False                # ему дано слово (презентационная комната)


class MeetingService:
    def __init__(self, settings: Settings, redis: Redis, bridge: AsrBridge):
        self._s = settings
        self._r = redis
        self._bridge = bridge
        self.on_ended: Callable[[uuid.UUID], None] | None = None  # запуск финализации (экспорт, протокол)
        self.on_started: Callable[[uuid.UUID], None] | None = None      # встреча началась (например, подготовка входящих телефонных звонков)
        self.on_ended_extra: Callable[[uuid.UUID], None] | None = None  # встреча закончилась (например, снять правило входящих звонков)
        self.settings_svc = None  # SettingsService (срок «аренды» доступа после завершения), назначается в main

    # ------------------------------------------------------------------ вход
    async def _sync_room(self, db: AsyncSession, room: Room | None, meeting: Meeting | None) -> None:
        """Временная комната: «в ней кто-то есть» ↔ «все вышли, ждём возврата» (срок закрытия); для постоянной ничего не делает."""
        if room is None or room.lifetime != "temporary":
            return
        cfg = await self.settings_svc.get(db, "general") if self.settings_svc is not None else None
        temp_rooms.sync_lifecycle(room, meeting, cfg)    # type: ignore[arg-type]
        await db.commit()

    async def _temp_grace_seconds(self, db: AsyncSession, room: Room | None) -> float | None:
        if room is None or room.lifetime != "temporary" or self.settings_svc is None:
            return None
        return (await self.settings_svc.get(db, "general")).temp_room_grace_minutes * 60      # type: ignore[attr-defined]

    async def join(self, db: AsyncSession, room_id: uuid.UUID, su: SessionUser, password: str | None) -> JoinResult:
        closed = await db.get(Room, room_id)
        if closed is not None and closed.lifecycle == "closed" and acl_allows(closed, su):
            raise JoinError("room_closed", "Временная переговорка закрыта. Материалы встречи — в «Истории».", 410)
        try:
            room = await get_accessible_room(db, room_id, su)
        except RoomNotFound:
            raise JoinError("room_not_found", "Комната не найдена или недоступна.", 404) from None

        if room.password_hash:
            await self._check_room_password(room, su, password)

        if await self._r.exists(self._kick_key(room.id, su.user_id)):
            raise JoinError("removed_from_meeting", "Руководитель встречи удалил вас из комнаты. Попробуйте войти позже.", 403)
        meeting = await self._active_meeting(db, room.id)
        created = False
        if meeting is None:
            # Транскрибация идёт с первой секунды (если комната её допускает); запись аудио — сразу только при «автоматической записи»
            meeting = Meeting(room_id=room.id, livekit_room="pending", started_by_user_id=su.user_id,
                              transcription_enabled=room.transcription_enabled, record_audio=bool(room.record_audio and room.auto_record))
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
        present += await self._open_guests(db, meeting.id)
        if present >= room.max_participants:
            raise JoinError("room_full", "В комнате нет свободных мест.", 409)

        part = (await db.execute(select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting.id, MeetingParticipant.user_id == su.user_id,
            MeetingParticipant.left_at.is_(None)))).scalars().first()
        if part is None:
            db.add(MeetingParticipant(meeting_id=meeting.id, user_id=su.user_id))
        meeting.empty_since = None
        await db.commit()
        await self._sync_room(db, room, meeting)

        if created:
            await self._bridge.start(meeting_id=str(meeting.id), room_name=meeting.livekit_room, room_id=str(room.id),
                                     transcribe=meeting.transcription_enabled, record_audio=meeting.record_audio)
            log.info("Встреча начата", extra={"meeting_id": str(meeting.id), "room": room.slug})
            if self.on_started is not None:
                self.on_started(meeting.id)
        await events.publish(self._r, meeting.id, {"type": "participant_joined", "user_id": str(su.user_id),
                                                   "display_name": su.display_name})

        identity = user_identity(su.user_id)
        privileged = roles.can_manage_room(room, su)
        floor = await self.floor_has(meeting.id, identity)
        sources = roles.publish_sources(room, su, has_floor=floor)
        if privileged:
            await self._r.sadd(self._priv_key(meeting.id), identity)
            await self._r.expire(self._priv_key(meeting.id), STATE_TTL)
        token = issue_user_token(self._s, user_id=su.user_id, display_name=su.display_name, livekit_room=meeting.livekit_room, sources=sources)
        return JoinResult(meeting, room, token, identity, sources=sources, privileged=privileged, floor=floor)

    # ------------------------------------------------------------ гость
    @staticmethod
    async def _open_guests(db: AsyncSession, meeting_id: uuid.UUID) -> int:
        return (await db.execute(select(func.count()).select_from(GuestParticipant).where(
            GuestParticipant.meeting_id == meeting_id, GuestParticipant.left_at.is_(None)))).scalar_one()

    async def join_guest(self, db: AsyncSession, room: Room, display_name: str, *, ip: str | None, client: str | None) -> JoinResult:
        """Вход гостя по гостевой ссылке. Гость НЕ начинает встречу: она должна быть уже активна (иначе незнакомый человек
        запускал бы запись и ASR в пустой комнате). Права минимальные: без демонстрации экрана и без управления."""
        if room.lifecycle == "closed":
            raise JoinError("room_closed", "Эта временная переговорка закрыта.", 410)
        meeting = await self._active_meeting(db, room.id)
        if meeting is None:
            raise JoinError("meeting_not_active", "Встреча ещё не началась. Дождитесь, пока её откроет сотрудник.", 409)
        users = (await db.execute(select(func.count(func.distinct(MeetingParticipant.user_id))).where(
            MeetingParticipant.meeting_id == meeting.id, MeetingParticipant.left_at.is_(None)))).scalar_one()
        if users + await self._open_guests(db, meeting.id) >= room.max_participants:
            raise JoinError("room_full", "В комнате нет свободных мест.", 409)
        guest = GuestParticipant(meeting_id=meeting.id, room_id=room.id, display_name=display_name, ip=ip, client=client)
        guest.id = uuid.uuid4()
        db.add(guest)
        meeting.empty_since = None
        await db.commit()
        await self._sync_room(db, room, meeting)
        await events.publish(self._r, meeting.id, {"type": "participant_joined", "guest_id": str(guest.id),
                                                   "display_name": f"{display_name} (гость)", "participant_type": "guest"})
        return self._guest_result(meeting, room, guest)

    def _guest_result(self, meeting: Meeting, room: Room, guest: GuestParticipant, *, floor: bool = False) -> JoinResult:
        sources = roles.publish_sources(room, None, guest=True, has_floor=floor)
        token = issue_guest_token(self._s, guest_id=guest.id, display_name=guest.display_name, livekit_room=meeting.livekit_room, sources=sources)
        return JoinResult(meeting, room, token, guest_identity(guest.id), guest, sources=sources, floor=floor)

    async def rejoin_guest(self, db: AsyncSession, guest: GuestParticipant) -> JoinResult:
        """Повторная выдача токена гостю с действующей сессией (обновление страницы, обрыв сети)."""
        meeting = await db.get(Meeting, guest.meeting_id)
        if meeting is None or meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        if guest.left_at is not None:
            guest.left_at = None
            guest.connected_at = None
            guest.joined_at = utcnow()
        meeting.empty_since = None
        await db.commit()
        return self._guest_result(meeting, meeting.room, guest, floor=await self.floor_has(meeting.id, guest_identity(guest.id)))

    async def leave_guest(self, db: AsyncSession, meeting_id: uuid.UUID, guest_id: uuid.UUID) -> None:
        guest = await db.get(GuestParticipant, guest_id)
        if guest is None or guest.meeting_id != meeting_id:
            return
        if guest.left_at is None:
            guest.left_at = utcnow()
            await db.commit()
            await events.publish(self._r, meeting_id, {"type": "participant_left", "guest_id": str(guest_id), "participant_type": "phone" if guest.is_phone else "guest"})
        await self._update_emptiness(db, meeting_id)

    async def kick_guests(self, db: AsyncSession, room_id: uuid.UUID) -> int:
        """Гостевая ссылка отозвана / гостевой доступ выключен: гости активной встречи комнаты отключаются, сотрудники остаются."""
        meeting = await self._active_meeting(db, room_id)
        if meeting is None:
            return 0
        guests = (await db.execute(select(GuestParticipant).where(
            GuestParticipant.meeting_id == meeting.id, GuestParticipant.left_at.is_(None), GuestParticipant.participant_type == "guest"))).scalars().all()   # телефонные участники не гости
        now = utcnow()
        for g in guests:
            g.left_at = now
            await self._r.set(f"guest:revoked:{g.id}", "1", ex=GUEST_REVOKE_TTL)
        await db.commit()
        for g in guests:
            await remove_participant(self._s, meeting.livekit_room, guest_identity(g.id))
            await events.publish(self._r, meeting.id, {"type": "participant_left", "guest_id": str(g.id), "participant_type": "guest"})
        if guests:
            await self._update_emptiness(db, meeting.id)
        return len(guests)

    async def on_guest_joined(self, db: AsyncSession, meeting_id: uuid.UUID, guest_id: uuid.UUID) -> None:
        meeting = await db.get(Meeting, meeting_id)
        guest = await db.get(GuestParticipant, guest_id)
        if meeting is None or meeting.ended_at is not None or guest is None or guest.meeting_id != meeting_id:
            return
        guest.connected_at = guest.connected_at or utcnow()
        meeting.empty_since = None
        await db.commit()

    async def on_guest_left(self, db: AsyncSession, meeting_id: uuid.UUID, guest_id: uuid.UUID) -> None:
        await self.leave_guest(db, meeting_id, guest_id)

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
        open_count += await self._open_guests(db, meeting_id)
        if open_count == 0 and meeting.empty_since is None:
            meeting.empty_since = utcnow()
        elif open_count > 0:
            meeting.empty_since = None
        await db.commit()
        await self._sync_room(db, await db.get(Room, meeting.room_id), meeting)

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
        for g in (await db.execute(select(GuestParticipant).where(
                GuestParticipant.meeting_id == meeting.id, GuestParticipant.left_at.is_(None)))).scalars().all():
            g.left_at = now
        await db.commit()
        if online:  # кто был в комнате в момент завершения, остаётся «на странице встречи» и может сформировать протокол
            minutes = 120
            if self.settings_svc is not None:
                minutes = (await self.settings_svc.get(db, "general")).post_meeting_access_minutes
            await grant_leases(self._r, meeting.id, online, minutes)
        await self._bridge.stop(meeting_id=str(meeting.id), room_name=meeting.livekit_room)
        await self._clear_state(meeting.id)   # «слово» и список руководителей — состояние встречи, после неё сбрасывается
        await events.publish(self._r, meeting.id, {"type": "meeting_ended", "reason": reason})
        if kick:
            await delete_livekit_room(self._s, meeting.livekit_room)
        room = await db.get(Room, meeting.room_id)
        if room is not None and temp_rooms.close(room, reason):      # временная переговорка живёт, пока идёт встреча; материалы остаются
            await db.commit()
        if self.on_ended is not None:
            self.on_ended(meeting.id)
        if self.on_ended_extra is not None:
            self.on_ended_extra(meeting.id)
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
        await self._sync_room(db, await db.get(Room, meeting.room_id), meeting)

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
            for g in (await db.execute(select(GuestParticipant).where(
                    GuestParticipant.meeting_id == meeting.id, GuestParticipant.left_at.is_(None)))).scalars().all():
                if g.livekit_identity in present:
                    g.connected_at = g.connected_at or now
                elif g.connected_at is not None or now - g.joined_at > connect_deadline:
                    g.left_at = now
            await db.commit()
            await self._update_emptiness(db, meeting.id)
            await db.refresh(meeting)
        grace = await self._temp_grace_seconds(db, await db.get(Room, meeting.room_id))
        if grace is None:
            grace = self._s.meeting_end_grace_seconds
        if (meeting.ended_at is None and meeting.empty_since is not None
                and now - meeting.empty_since >= timedelta(seconds=grace)):
            await self.end(db, meeting, "empty")

    async def reap_once(self, db: AsyncSession) -> int:
        active = (await db.execute(select(Meeting).where(Meeting.ended_at.is_(None)))).scalars().all()
        for m in active:
            try:
                await self.reconcile(db, m)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка сверки встречи", extra={"meeting_id": str(m.id)})
                await db.rollback()
        try:
            await self.cleanup_temporary(db)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очистки временных переговорок")
            await db.rollback()
        return len(active)

    async def cleanup_temporary(self, db: AsyncSession) -> int:
        """Зависшие временные комнаты: встреча дольше предельного срока завершается принудительно; комната, в которую никто так и не вошёл, закрывается."""
        if self.settings_svc is None:
            return 0
        rooms = (await db.execute(select(Room).where(Room.lifetime == "temporary", Room.lifecycle != "closed"))).scalars().all()
        if not rooms:
            return 0
        cfg = await self.settings_svc.get(db, "general")
        now, n = utcnow(), 0
        for room in rooms:
            meeting = await self._active_meeting(db, room.id)
            if meeting is not None:
                if now - meeting.started_at >= timedelta(hours=cfg.temp_room_max_hours):      # type: ignore[attr-defined]
                    await self.end(db, meeting, "temp_ttl", kick=True)
                    n += 1
            elif now - room.created_at >= timedelta(minutes=cfg.temp_room_idle_minutes) or (room.auto_close_at and now >= room.auto_close_at):  # type: ignore[attr-defined]
                if temp_rooms.close(room, "idle"):
                    await db.commit()
                    n += 1
        return n

    # ----------------------------------------------------- состояние встречи в Redis: слово и привилегированные
    @staticmethod
    def _floor_key(meeting_id: uuid.UUID | str) -> str:
        return f"floor:{meeting_id}"

    @staticmethod
    def _priv_key(meeting_id: uuid.UUID | str) -> str:
        return f"priv:{meeting_id}"

    @staticmethod
    def _kick_key(room_id: uuid.UUID | str, user_id: uuid.UUID | str) -> str:
        return f"kicked:{room_id}:{user_id}"

    async def floor_has(self, meeting_id: uuid.UUID, identity: str) -> bool:
        return bool(await self._r.sismember(self._floor_key(meeting_id), identity))

    async def floor_list(self, meeting_id: uuid.UUID) -> list[str]:
        return sorted(await self._r.smembers(self._floor_key(meeting_id)))

    async def is_privileged(self, meeting_id: uuid.UUID, identity: str) -> bool:
        return bool(await self._r.sismember(self._priv_key(meeting_id), identity))

    async def _clear_state(self, meeting_id: uuid.UUID) -> None:
        await self._r.delete(self._floor_key(meeting_id), self._priv_key(meeting_id))

    async def _member_check(self, db: AsyncSession, meeting: Meeting, identity: str) -> tuple[str, uuid.UUID]:
        """Идентичность должна принадлежать участнику ЭТОЙ встречи (человек или гость); иначе — отказ (чужие/выдуманные identity не принимаются)."""
        uid = parse_user_identity(identity)
        if uid is not None:
            row = (await db.execute(select(MeetingParticipant.id).where(
                MeetingParticipant.meeting_id == meeting.id, MeetingParticipant.user_id == uid, MeetingParticipant.left_at.is_(None)))).first()
            if row:
                return "user", uid
        gid = parse_guest_identity(identity) or parse_phone_identity(identity)
        g = await db.get(GuestParticipant, gid) if gid is not None else None
        if g is None and identity.startswith("sip_"):    # входящий звонок: identity выдал LiveKit SIP
            g = (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id == meeting.id, GuestParticipant.lk_identity == identity))).scalars().first()
        if g is not None and g.meeting_id == meeting.id and g.left_at is None:
            return "guest", g.id
        raise JoinError("participant_not_found", "Участник не найден среди присутствующих на встрече.", 404)

    async def set_floor(self, db: AsyncSession, meeting: Meeting, identity: str, granted: bool, *, by: str) -> bool:
        """«Дать слово» / «Забрать слово»: временное право участника публиковать звук, видео и экран и править доску в рамках ТЕКУЩЕЙ встречи.
        Работает в презентационной комнате; руководители и администраторы всегда имеют полные права, их слово не меняется."""
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        if not roles.is_presentation(meeting.room):
            raise JoinError("not_presentation", "Слово даётся только в презентационной комнате (в обычной все могут говорить по правам комнаты).", 409)
        kind, _ = await self._member_check(db, meeting, identity)
        if await self.is_privileged(meeting.id, identity):
            raise JoinError("already_privileged", "У руководителя комнаты права уже полные — слово ему не требуется.", 409)
        had = await self.floor_has(meeting.id, identity)
        if had == granted:
            return granted
        sources = roles.publish_sources(meeting.room, None, guest=(kind == "guest"), has_floor=granted)
        # сначала меняем права на сервере звонков: если он недоступен — состояние не меняется и руководитель получает понятную ошибку
        if not await set_publish_permission(self._s, meeting.livekit_room, identity, sources):
            raise JoinError("livekit_unavailable", "Сервер звонков недоступен — право не изменено. Повторите.", 503)
        if granted:
            await self._r.sadd(self._floor_key(meeting.id), identity)
            await self._r.expire(self._floor_key(meeting.id), STATE_TTL)
        else:
            await self._r.srem(self._floor_key(meeting.id), identity)
        await events.publish(self._r, meeting.id, {"type": "floor_changed", "identity": identity, "granted": granted, "by": by})
        log.info("Слово изменено", extra={"meeting_id": str(meeting.id), "granted": granted})
        return granted

    async def kick_participant(self, db: AsyncSession, meeting: Meeting, identity: str) -> str:
        """Удалить участника из идущей встречи (руководитель). Сотрудник не может вернуться ~10 минут; гость отключается насовсем."""
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        kind, ref = await self._member_check(db, meeting, identity)
        if await self.is_privileged(meeting.id, identity):
            raise JoinError("privileged", "Руководителя комнаты удалить нельзя.", 409)
        if kind == "guest":
            g = await db.get(GuestParticipant, ref)
            await self._r.set(f"guest:revoked:{ref}", "1", ex=GUEST_REVOKE_TTL)
            if g is not None:
                g.left_at = utcnow()
                await db.commit()
        else:
            await self._r.set(self._kick_key(meeting.room_id, ref), "1", ex=KICK_BLOCK_SECONDS)
            await self._close_participant(db, meeting.id, ref)
        await remove_participant(self._s, meeting.livekit_room, identity)
        await self._r.srem(self._floor_key(meeting.id), identity)
        await events.publish(self._r, meeting.id, {"type": "participant_left", "identity": identity, "removed": True,
                                                   **({"guest_id": str(ref), "participant_type": "guest"} if kind == "guest" else {"user_id": str(ref)})})
        await self._update_emptiness(db, meeting.id)
        return kind

    # ----------------------------------------------- транскрибация и запись аудио (раздельно)
    async def _push_flags(self, meeting: Meeting) -> None:
        await self._bridge.configure(meeting_id=str(meeting.id), room_name=meeting.livekit_room, room_id=str(meeting.room_id),
                                     transcribe=meeting.transcription_enabled, record_audio=meeting.record_audio)

    async def set_transcription(self, db: AsyncSession, meeting: Meeting, enabled: bool) -> bool:
        """«Остановить / возобновить транскрибацию». Звонок и запись аудио не затрагиваются."""
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        if enabled and not meeting.room.transcription_enabled:
            raise JoinError("transcription_forbidden", "В этой комнате транскрибация отключена администратором.", 409)
        if meeting.transcription_enabled == enabled:
            return enabled
        meeting.transcription_enabled = enabled
        await db.commit()
        await self._push_flags(meeting)
        await events.publish(self._r, meeting.id, {"type": "transcription_changed", "enabled": enabled})
        log.info("Транскрибация переключена", extra={"meeting_id": str(meeting.id), "enabled": enabled})
        return enabled

    async def set_recording(self, db: AsyncSession, meeting: Meeting, enabled: bool) -> bool:
        """«Начать / остановить запись» — запись АУДИО встречи (стенограмма от неё не зависит). Включить можно, если комната допускает запись аудио."""
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        if enabled and not meeting.room.record_audio:
            raise JoinError("recording_forbidden", "В этой комнате запись аудио не разрешена (включается в настройках комнаты).", 409)
        if meeting.record_audio == enabled:
            return enabled
        meeting.record_audio = enabled
        await db.commit()
        await self._push_flags(meeting)
        await events.publish(self._r, meeting.id, {"type": "recording_changed", "enabled": enabled})
        log.info("Запись аудио переключена", extra={"meeting_id": str(meeting.id), "enabled": enabled})
        return enabled
