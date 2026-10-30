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
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..config import Settings
from ..models import MEETING_LITE, GuestParticipant, Meeting, MeetingParticipant, Room, User, utcnow
from ..security.passwords import verify_room_password
from . import events, roles, temp_rooms
from . import e2ee as e2ee_svc
from . import outlook as outlook_svc
from .access import grant_leases
from .asr_bridge import AsrBridge
from ..auth.devices import LEGACY, device_tag, session_tag
from .livekit import (
    delete_livekit_room,
    enforce_sources,
    guest_identity,
    permission_sources,
    issue_guest_token,
    issue_user_token,
    list_present_identities,
    meeting_room_name,
    mute_sources,
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
        self.video = None                                                   # VideoRecorder (видеозапись через Egress); задаётся при запуске приложения
        self.live = None                                                    # LiveBroadcaster (трансляции: HLS и внешние площадки); задаётся при запуске приложения
        self.on_started: Callable[[uuid.UUID], None] | None = None      # встреча началась (например, подготовка входящих телефонных звонков)
        self.on_ended_extra: Callable[[uuid.UUID], None] | None = None  # встреча закончилась (например, снять правило входящих звонков)
        self.on_event: Callable[[str, Meeting], None] | None = None     # события для публичного API: ("meeting.started" | "meeting.ended", встреча)
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

    async def join(self, db: AsyncSession, room_id: uuid.UUID, su: SessionUser, password: str | None, *, client_id: str = "", device_kind: str = "") -> JoinResult:
        closed = await db.get(Room, room_id, options=(joinedload(Room.acl), joinedload(Room.moderators)))     # ACL и руководители — тем же запросом
        if closed is not None and closed.lifecycle == "closed" and acl_allows(closed, su):
            raise JoinError("room_closed", "Временная переговорка закрыта. Материалы встречи — в «Истории».", 410)
        try:
            room = await get_accessible_room(db, room_id, su)
        except RoomNotFound:
            raise JoinError("room_not_found", "Комната не найдена или недоступна.", 404) from None
        if outlook_svc.is_too_early(room):            # запланированная конференция из Outlook: вход открывается за N минут до начала
            raise JoinError("too_early", outlook_svc.gate_message(room), 425)

        if room.password_hash and not (room.room_type == "broadcast" and roles.can_manage_room(room, su)):
            # трансляционная комната: пароль выступающего нужен остальным; владелец, руководители и администраторы входят по своим правам (пароль им не выдаётся и не заменяет права)
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
                await db.rollback()                                  # rollback «протухает» все загруженные объекты (room): обращение к их полям — скрытый запрос и MissingGreenlet (500 при одновременном входе)
                room = await get_accessible_room(db, room_id, su)
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
            e2ee = await e2ee_svc.begin(self._r, room, meeting.id)         # ключ сквозного шифрования этой встречи (если у комнаты включено E2EE)
            if e2ee_svc.blocks_server_features(e2ee) and (meeting.transcription_enabled or meeting.record_audio):
                meeting.transcription_enabled = meeting.record_audio = False      # полное E2EE: у сервера нет ключа — распознавание и запись звука невозможны
                await db.commit()
            await self._bridge.start(meeting_id=str(meeting.id), room_name=meeting.livekit_room, room_id=str(room.id),
                                     transcribe=meeting.transcription_enabled, record_audio=meeting.record_audio, e2ee_key=e2ee_svc.asr_key(e2ee))
            log.info("Встреча начата", extra={"meeting_id": str(meeting.id), "room": room.slug})
            if self.on_started is not None:
                self.on_started(meeting.id)
            self._notify("meeting.started", meeting)
        # Устройство/вкладка: первое подключение пользователя к встрече — основной identity, остальные — `u-<hex>~<метка>`; та же вкладка после обрыва получает тот же identity.
        # Основное подключение (`u-<hex>`) — у того, кто пришёл первым: клиент с идентификатором (по метке) или без него (LEGACY). Остальные получают свою метку.
        prim_key = f"mprim:{meeting.id}:{su.user_id}"
        tag = device_tag(su.session_id, client_id)
        mine = tag or LEGACY
        owner = mine if await self._r.set(prim_key, mine, nx=True, ex=STATE_TTL) else await self._r.get(prim_key)
        if owner in (mine, None):
            device = ""
        else:
            device = tag or session_tag(su.session_id)          # клиент без идентификатора, а основное занято: метка по сеансу, а не общий u-<hex>
        identity = user_identity(su.user_id, device)
        devs = f"mdev:{meeting.id}:{su.user_id}"
        async with self._r.pipeline(transaction=False) as p:          # одно обращение к Redis вместо двух: вход выполняет каждый
            p.sadd(devs, identity)
            p.expire(devs, STATE_TTL)
            await p.execute()
        privileged = roles.can_manage_room(room, su)
        if privileged or not roles.is_presentation(room):
            # В презентационной комнате приход рядового зрителя никому не рассылается: при сотне и тысяче зрителей это сотни тысяч сообщений
            # и запросов (каждый зритель перечитывал бы состояние слова на каждый вход). Список и число зрителей клиенты берут у сервера звонков.
            await events.publish(self._r, meeting.id, {"type": "participant_joined", "user_id": str(su.user_id), "display_name": su.display_name})
        floor = await self.floor_has(meeting.id, identity)
        sources = roles.publish_sources(room, su, has_floor=floor)
        if not roles.can_manage_room(room, su) and await self.share_blocked(meeting.id, identity):
            sources = self.without_share(sources)          # ведущий запретил этому участнику показ экрана до конца встречи — действует и после переподключения
        if privileged:
            async with self._r.pipeline(transaction=False) as p:
                p.sadd(self._priv_key(meeting.id), identity)
                p.expire(self._priv_key(meeting.id), STATE_TTL)
                await p.execute()
        # Токен рядового участника презентации ВСЕГДА без права публикации (даже если у него сейчас есть слово): права выступающего ему выдаёт
        # сервер звонков по запросу backend (`update_participant`) уже у подключённого участника. Тогда токен, выданный до отзыва слова, прав не даёт:
        # окна «старый токен позволил опубликовать звук» нет. Права после (пере)подключения определяются заново — webhook и `refresh_rights`.
        token_sources = sources if (privileged or not roles.is_presentation(room)) else []
        token = issue_user_token(self._s, user_id=su.user_id, display_name=su.display_name, livekit_room=meeting.livekit_room, sources=token_sources,
                                 device=device, name_suffix=f" ({device_kind})" if device and device_kind else (" (другое устройство)" if device else ""))
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
        if outlook_svc.is_too_early(room):
            raise JoinError("too_early", outlook_svc.gate_message(room), 425)
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
        if not roles.is_presentation(room):
            await events.publish(self._r, meeting.id, {"type": "participant_joined", "guest_id": str(guest.id),
                                                       "display_name": f"{display_name} (гость)", "participant_type": "guest"})
        return self._guest_result(meeting, room, guest)

    def _guest_result(self, meeting: Meeting, room: Room, guest: GuestParticipant, *, floor: bool = False) -> JoinResult:
        sources = roles.publish_sources(room, None, guest=True, has_floor=floor)
        token = issue_guest_token(self._s, guest_id=guest.id, display_name=guest.display_name, livekit_room=meeting.livekit_room,
                                  sources=[] if roles.is_presentation(room) else sources)      # презентация: токен гостя без публикации, слово расширяет права у подключённого
        return JoinResult(meeting, room, token, guest_identity(guest.id), guest, sources=sources, floor=floor)

    async def rejoin_guest(self, db: AsyncSession, guest: GuestParticipant) -> JoinResult:
        """Повторная выдача токена гостю с действующей сессией (обновление страницы, обрыв сети)."""
        meeting = await db.get(Meeting, guest.meeting_id, options=MEETING_LITE)
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
            if not await self._quiet_presence(db, meeting_id, guest_identity(guest_id)):
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
        meeting = await db.get(Meeting, meeting_id, options=MEETING_LITE)
        guest = await db.get(GuestParticipant, guest_id)
        if meeting is None or meeting.ended_at is not None or guest is None or guest.meeting_id != meeting_id:
            return
        guest.connected_at = guest.connected_at or utcnow()
        meeting.empty_since = None
        await db.commit()
        if self.video is not None:
            await self.video.on_join(db, meeting)
        if self.live is not None:
            await self.live.on_join(db, meeting)

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
    async def _active_meeting(db: AsyncSession, room_id: uuid.UUID, *, participants: bool = False) -> Meeting | None:
        """participants=False: список участников не загружается (обращение к нему — ошибка). Вход выполняет каждый участник, а во встрече их бывают сотни."""
        stmt = select(Meeting).where(Meeting.room_id == room_id, Meeting.ended_at.is_(None))
        return (await db.execute(stmt if participants else stmt.options(*MEETING_LITE))).unique().scalars().one_or_none()     # активная встреча комнаты одна (уникальный индекс)

    # ----------------------------------------------------------------- выход
    async def leave(self, db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID, *, session_id: str = "", client_id: str = "") -> None:
        """Выход одного устройства: участником во встрече пользователь остаётся, пока подключено хотя бы одно его устройство."""
        if session_id:
            ident = await self._device_identity(meeting_id, user_id, device_tag(session_id, client_id), session_id)
            if await self._other_devices(meeting_id, user_id, ident):
                return
        await self._close_participant(db, meeting_id, user_id)

    async def _device_identity(self, meeting_id: uuid.UUID, user_id: uuid.UUID, tag: str, session_id: str = "") -> str:
        mine = tag or LEGACY
        owner = await self._r.get(f"mprim:{meeting_id}:{user_id}")
        return user_identity(user_id, "" if owner in (mine, None) else (tag or session_tag(session_id)))

    async def _other_devices(self, meeting_id: uuid.UUID, user_id: uuid.UUID, leaving: str) -> bool:
        """Убирает identity уходящего устройства; True — у пользователя во встрече остались другие подключённые устройства."""
        key = f"mdev:{meeting_id}:{user_id}"
        if not await self._r.srem(key, leaving):                         # устройство неизвестно (например, пользователь перелогинился): если оно единственное — как раньше, иначе другие остаются
            return await self._r.scard(key) > 1
        return bool(await self._r.scard(key))

    async def _close_participant(self, db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._r.delete(f"mdev:{meeting_id}:{user_id}", f"mprim:{meeting_id}:{user_id}")
        now = utcnow()
        rows = (await db.execute(select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == user_id,
            MeetingParticipant.left_at.is_(None)))).scalars().all()
        for p in rows:
            p.left_at = now
        await db.commit()
        if rows and not await self._quiet_presence(db, meeting_id, user_identity(user_id)):
            await events.publish(self._r, meeting_id, {"type": "participant_left", "user_id": str(user_id)})
        await self._update_emptiness(db, meeting_id)

    async def _quiet_presence(self, db: AsyncSession, meeting_id: uuid.UUID, identity: str) -> bool:
        """Приход/уход рядового зрителя презентационной комнаты не рассылается (см. join); руководителей это не касается."""
        meeting = await db.get(Meeting, meeting_id, options=MEETING_LITE)
        return meeting is not None and roles.is_presentation(meeting.room) and not await self.is_privileged(meeting_id, identity)

    async def _update_emptiness(self, db: AsyncSession, meeting_id: uuid.UUID) -> None:
        meeting = await db.get(Meeting, meeting_id, options=MEETING_LITE)     # уход каждого участника: список всех не нужен
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
        if open_count == 0 and self.video is not None:
            await self.video.on_empty(db, meeting)                # пустую комнату не записываем
        await self._sync_room(db, await db.get(Room, meeting.room_id), meeting)

    # ------------------------------------------------------------ завершение
    def _notify(self, event: str, meeting: Meeting) -> None:
        """Событие для подписчиков публичного API; сбой подписчиков не должен влиять на встречу."""
        if self.on_event is None:
            return
        try:
            self.on_event(event, meeting)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось передать событие встречи", extra={"event": event})

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
        if self.video is not None and meeting.record_video:
            try:
                await self.video.stop(db, meeting, by="meeting_end")        # файл закрывает Egress; итог — webhook или сверка
            except Exception:  # noqa: BLE001 — завершение встречи не должно зависеть от записи
                log.exception("Не удалось остановить видеозапись при завершении встречи", extra={"meeting_id": str(meeting.id)})
        if self.live is not None:
            await self.live.on_meeting_end(db, meeting)
        await self._bridge.stop(meeting_id=str(meeting.id), room_name=meeting.livekit_room)
        await e2ee_svc.drop(self._r, meeting.id)         # ключ сквозного шифрования действует одну встречу
        await self._clear_state(meeting.id)   # «слово» и список руководителей — состояние встречи, после неё сбрасывается
        await events.publish(self._r, meeting.id, {"type": "meeting_ended", "reason": reason})
        if kick:
            await delete_livekit_room(self._s, meeting.livekit_room)
        room = await db.get(Room, meeting.room_id)
        # временная переговорка живёт, пока идёт встреча; материалы остаются. Конференцию из календаря закрывает расписание (или отмена): до планового
        # окончания в неё можно вернуться, а у серии каждая следующая встреча — в той же комнате
        if room is not None and room.scheduled_start is None and temp_rooms.close(room, reason):
            await db.commit()
        if self.on_ended is not None:
            self.on_ended(meeting.id)
        if self.on_ended_extra is not None:
            self.on_ended_extra(meeting.id)
        self._notify("meeting.ended", meeting)
        log.info("Встреча завершена", extra={"meeting_id": str(meeting.id), "reason": reason})
        return True

    # ------------------------------------------------- присутствие (webhook)
    async def on_participant_joined(self, db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID) -> None:
        meeting = await db.get(Meeting, meeting_id, options=MEETING_LITE)          # приходит на подключение каждого участника
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
        if self.video is not None:
            await self.video.on_join(db, meeting)              # режим комнаты «автоматически»
        if self.live is not None:
            await self.live.on_join(db, meeting)               # трансляция в режиме «автоматически»

    async def sync_rights_on_join(self, db: AsyncSession, meeting_id: uuid.UUID, identity: str, permission) -> bool:
        """Права только что подключившегося участника презентации приводятся к ТЕКУЩЕМУ состоянию слова — в обе стороны.

        Токен рядового участника презентации не даёт публикации, поэтому тот, у кого есть слово, после переподключения получает права здесь (webhook
        `participant_joined`, дополнительно клиент вызывает `refresh_rights`); тот, у кого слова нет, не получит их ни при каком токене. Если права шире
        нужных (токен выдан прежней версией приложения, показ экрана запрещён позже) — урезаются и опубликованные дорожки снимаются (`enforce_sources`).
        Руководители пропускаются. Возвращает True, если права пришлось изменить."""
        meeting = await db.get(Meeting, meeting_id, options=MEETING_LITE)
        if meeting is None or meeting.ended_at is not None or not roles.is_presentation(meeting.room) or await self.is_privileged(meeting_id, identity):
            return False
        granted = permission_sources(permission)
        sources = roles.publish_sources(meeting.room, None, guest=identity.startswith("g-"), has_floor=await self.floor_has(meeting_id, identity))
        if await self.share_blocked(meeting_id, identity):
            sources = self.without_share(sources)
        if granted == set(sources):
            return False
        await set_publish_permission(self._s, meeting.livekit_room, identity, sources)
        if granted - set(sources):
            await enforce_sources(self._s, meeting.livekit_room, identity, sources)
            log.warning("Права подключившегося урезаны до текущих", extra={"meeting_id": str(meeting_id), "identity": identity[:8]})
        return True

    async def refresh_rights(self, db: AsyncSession, meeting: Meeting, identity: str, kind: str) -> dict:
        """Участник просит сервер заново определить его права в звонке (после переподключения): backend считает их по текущему состоянию и применяет."""
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        sources = await self._sources_now(db, meeting, identity, kind)
        applied = await set_publish_permission(self._s, meeting.livekit_room, identity, sources)
        return {"sources": sources, "applied": applied}

    async def on_participant_left(self, db: AsyncSession, meeting_id: uuid.UUID, user_id: uuid.UUID, identity: str = "") -> None:
        if identity and await self._other_devices(meeting_id, user_id, identity):
            return                                                   # отключилось одно устройство, другое ещё во встрече
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
                here = any(parse_user_identity(i) == p.user_id for i in present)          # любое устройство пользователя
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
            return await self.purge_scheduled(db, utcnow())
        cfg = await self.settings_svc.get(db, "general")
        now, n = utcnow(), 0
        for room in rooms:
            meeting = await self._active_meeting(db, room.id, participants=True)       # end() проходит по участникам
            if meeting is not None:
                if now - meeting.started_at >= timedelta(hours=cfg.temp_room_max_hours):      # type: ignore[attr-defined]
                    await self.end(db, meeting, "temp_ttl", kick=True)
                    n += 1
            elif (room.room_type != "peer" and room.scheduled_start is None and now - room.created_at >= timedelta(minutes=cfg.temp_room_idle_minutes)) or (room.auto_close_at and now >= room.auto_close_at):  # type: ignore[attr-defined]  # равноправные комнаты живут по своему сроку (`peer_room_ttl_hours`), а не по получасовому «простою»
                if temp_rooms.close(room, "idle"):
                    await db.commit()
                    n += 1
        return n + await self.purge_scheduled(db, now)

    async def purge_scheduled(self, db: AsyncSession, now) -> int:
        """Завершённые конференции из Outlook, в которых так и не было встреч, удаляются через `closed_retention_days`; комнаты со встречами остаются навсегда."""
        cfg = await self.settings_svc.get(db, "outlook")
        cutoff = now - timedelta(days=cfg.closed_retention_days)       # type: ignore[attr-defined]
        used = select(Meeting.id).where(Meeting.room_id == Room.id).exists()
        old = (await db.execute(select(Room).where(Room.scheduled_start.is_not(None), Room.lifecycle == "closed", Room.closed_at.is_not(None), Room.closed_at < cutoff, ~used))).scalars().all()
        for room in old:
            await db.delete(room)
        if old:
            await db.commit()
        return len(old)

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
        await self._r.delete(self._floor_key(meeting_id), self._priv_key(meeting_id), f"hands:{meeting_id}", self._share_block_key(meeting_id), f"stage:{meeting_id}")

    # ------------------------------------------------------------ показ экрана и камера: модерация (сцена ведущего — services/stage.py)
    @staticmethod
    def _share_block_key(meeting_id: uuid.UUID | str) -> str:
        return f"share_block:{meeting_id}"

    async def share_blocked(self, meeting_id: uuid.UUID, identity: str) -> bool:
        return bool(await self._r.sismember(self._share_block_key(meeting_id), identity))

    @staticmethod
    def without_share(sources: list[str]) -> list[str]:
        return [s for s in sources if s not in ("screen_share", "screen_share_audio")]

    async def _sources_now(self, db: AsyncSession, meeting: Meeting, identity: str, kind: str) -> list[str]:
        """Права публикации участника сейчас: по комнате, слову и запрету показа экрана (руководителям — всё)."""
        if await self.is_privileged(meeting.id, identity):
            from .roles import ALL_SOURCES  # noqa: PLC0415

            sources = list(ALL_SOURCES)
        else:
            sources = roles.publish_sources(meeting.room, None, guest=(kind == "guest"), has_floor=await self.floor_has(meeting.id, identity))
        return self.without_share(sources) if await self.share_blocked(meeting.id, identity) else sources

    async def stop_share(self, db: AsyncSession, meeting: Meeting, identity: str, *, block: bool, by: str) -> dict:
        """Остановить показ экрана одного участника. Остальные показы не затрагиваются.
        Без `block` — дорожка выключается на сервере звонков, а владелец получает событие и сам прекращает трансляцию (может начать снова).
        С `block` — право показывать экран снимается до конца встречи: сервер звонков сам прекращает дорожку, повторный показ невозможен."""
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        kind, _ = await self._member_check(db, meeting, identity)
        if block and await self.is_privileged(meeting.id, identity):
            raise JoinError("privileged", "Руководителю комнаты запретить показ экрана нельзя — можно только остановить текущий показ.", 409)
        muted = await mute_sources(self._s, meeting.livekit_room, {"screen_share", "screen_share_audio"}, only={identity})
        if muted is None:
            raise JoinError("livekit_unavailable", "Сервер звонков недоступен — показ не остановлен. Повторите.", 503)
        if block:
            await self._r.sadd(self._share_block_key(meeting.id), identity)
            await self._r.expire(self._share_block_key(meeting.id), STATE_TTL)
            if not await set_publish_permission(self._s, meeting.livekit_room, identity, await self._sources_now(db, meeting, identity, kind)):
                raise JoinError("livekit_unavailable", "Сервер звонков недоступен — запрет показа не применён. Повторите.", 503)
        await events.publish(self._r, meeting.id, {"type": "share_stopped", "identity": identity, "by": by, "blocked": block, "was_sharing": bool(muted)})
        return {"identity": identity, "stopped": bool(muted), "blocked": block}

    async def allow_share(self, db: AsyncSession, meeting: Meeting, identity: str, *, by: str) -> dict:
        """Снять запрет показа экрана (вернуть право, которое даёт комната)."""
        kind, _ = await self._member_check(db, meeting, identity)
        await self._r.srem(self._share_block_key(meeting.id), identity)
        if not await set_publish_permission(self._s, meeting.livekit_room, identity, await self._sources_now(db, meeting, identity, kind)):
            raise JoinError("livekit_unavailable", "Сервер звонков недоступен — право не возвращено. Повторите.", 503)
        await events.publish(self._r, meeting.id, {"type": "share_permission", "identity": identity, "allowed": True, "by": by})
        return {"identity": identity, "blocked": False}

    async def stop_camera(self, db: AsyncSession, meeting: Meeting, identity: str, *, by: str) -> dict:
        """Выключить камеру участника на сервере звонков (он может включить её снова — как и с микрофоном)."""
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        await self._member_check(db, meeting, identity)
        muted = await mute_sources(self._s, meeting.livekit_room, {"camera"}, only={identity})
        if muted is None:
            raise JoinError("livekit_unavailable", "Сервер звонков недоступен — камера не выключена. Повторите.", 503)
        await events.publish(self._r, meeting.id, {"type": "camera_stopped", "identity": identity, "by": by})
        return {"identity": identity, "stopped": bool(muted)}

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
        if await self.share_blocked(meeting.id, identity):
            sources = self.without_share(sources)
        # сначала меняем права на сервере звонков: если он недоступен — состояние не меняется и руководитель получает понятную ошибку
        if not await set_publish_permission(self._s, meeting.livekit_room, identity, sources):
            raise JoinError("livekit_unavailable", "Сервер звонков недоступен — право не изменено. Повторите.", 503)
        if granted:
            await self._r.sadd(self._floor_key(meeting.id), identity)
            await self._r.expire(self._floor_key(meeting.id), STATE_TTL)
        else:
            await self._r.srem(self._floor_key(meeting.id), identity)
        await events.publish(self._r, meeting.id, {"type": "floor_changed", "identity": identity, "granted": granted, "by": by})
        if not granted:
            # Отзыв применяется на сервере, а не «надеждой на клиента»: запрещённые дорожки (микрофон, камера, показ экрана) должны исчезнуть;
            # если участник их не снял (изменённый клиент), он отключается и вернуться с этими правами не может (токен выдаётся по текущему слову).
            res = await enforce_sources(self._s, meeting.livekit_room, identity, sources)
            if res.get("removed"):
                await events.publish(self._r, meeting.id, {"type": "floor_revoke_enforced", "identity": identity})
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
                                     transcribe=meeting.transcription_enabled, record_audio=meeting.record_audio,
                                     e2ee_key=e2ee_svc.asr_key(await e2ee_svc.get(self._r, meeting.id)))

    async def set_transcription(self, db: AsyncSession, meeting: Meeting, enabled: bool) -> bool:
        """«Остановить / возобновить транскрибацию». Звонок и запись аудио не затрагиваются."""
        if meeting.ended_at is not None:
            raise JoinError("meeting_ended", "Встреча уже завершена.", 409)
        if enabled and e2ee_svc.blocks_server_features(await e2ee_svc.get(self._r, meeting.id)):
            raise JoinError("e2ee_full", "В комнате включено полное сквозное шифрование: у сервера нет ключа, поэтому транскрибация недоступна.", 409)
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
        if enabled and e2ee_svc.blocks_server_features(await e2ee_svc.get(self._r, meeting.id)):
            raise JoinError("e2ee_full", "В комнате включено полное сквозное шифрование: у сервера нет ключа, поэтому запись звука недоступна.", 409)
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
