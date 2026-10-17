"""Временные равноправные комнаты: создаются по специальной ссылке или запросом, без обязательного ведущего.

Адрес комнаты — `/p/<код>`, код из 20 знаков безопасного алфавита (≈100 бит): угадать чужой нельзя, а первый разрешённый переход по ссылке создаёт комнату, если
администратор разрешил создание по ссылке. Создатель — не руководитель комнаты, а владелец ссылки: он продлевает срок, меняет доступ (по ссылке или только приглашённые),
перевыпускает ссылку и закрывает комнату. Участники равноправны (микрофон, камера, экран, доска); записью и транскрибацией при отсутствии руководителей управляет любой участник.
Защита: комнаты создают только вошедшие пользователи, частота создания и число активных комнат ограничены, поиск по несуществующим кодам ограничен по частоте.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Room, RoomAcl, User, utcnow
from . import storage_encryption as se
from .rooms import slug_taken
from .settings import GeneralSettings
from .temp_rooms import TempRoomError

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"           # без похожих знаков
CODE_LEN = 20
PREFIX = "p-"
CODE_RE = re.compile(r"^[abcdefghjkmnpqrstuvwxyz23456789]{20,32}$")
ROOM_TYPE = "peer"


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def slug_of(code: str) -> str:
    return PREFIX + code


def is_peer(room: Room) -> bool:
    return room.room_type == ROOM_TYPE


async def active_count(db: AsyncSession, user_id: uuid.UUID | None = None) -> int:
    q = select(func.count()).select_from(Room).where(Room.room_type == ROOM_TYPE, Room.lifecycle != "closed")
    if user_id is not None:
        q = q.where(Room.created_by_user_id == user_id)
    return (await db.execute(q)).scalar_one()


async def policy(db: AsyncSession, cfg: GeneralSettings, su: SessionUser) -> dict:
    mine = await active_count(db, su.user_id)
    return {"enabled": cfg.peer_rooms_enabled, "create_on_link": cfg.peer_rooms_enabled and cfg.peer_create_on_link, "max_per_user": cfg.peer_room_max_per_user,
            "active_mine": mine, "can_create": cfg.peer_rooms_enabled and mine < cfg.peer_room_max_per_user, "ttl_hours": cfg.peer_room_ttl_hours}


async def _check_rate(redis, su: SessionUser, cfg: GeneralSettings) -> None:
    key = f"peer:create:{su.user_id}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 3600)
    if n > cfg.peer_create_per_hour:
        raise TempRoomError("rate_limited", f"Слишком много новых комнат за час (не более {cfg.peer_create_per_hour}). Повторите позже.", 429)


async def create(db: AsyncSession, redis, su: SessionUser, cfg: GeneralSettings, name: str = "", *, code: str | None = None,
                 default_text_days: int | None = None, default_audio_days: int | None = None) -> Room:
    """Создаёт комнату (с заданным кодом при переходе по ссылке или со случайным). Создатель получает технические права, но не роль руководителя."""
    if not cfg.peer_rooms_enabled:
        raise TempRoomError("disabled", "Временные равноправные комнаты отключены администратором.", 403)
    if code is not None and not cfg.peer_create_on_link:
        raise TempRoomError("link_disabled", "Создание комнат по ссылке отключено: комнату нужно создать кнопкой.", 403)
    if su.local and not su.is_admin:
        raise TempRoomError("forbidden", "Недостаточно прав.", 403)
    if await active_count(db, su.user_id) >= cfg.peer_room_max_per_user:
        raise TempRoomError("limit_user", f"У вас уже {cfg.peer_room_max_per_user} активных равноправных комнат — закройте одну из них.")
    if await active_count(db) >= cfg.peer_room_max_total:
        raise TempRoomError("limit_total", "Достигнут общий предел активных комнат. Повторите позже.", 429)
    await _check_rate(redis, su, cfg)
    name = (name or "").strip()[:200] or "Равноправная встреча"
    now = utcnow()
    for _ in range(8):
        c = code or new_code()
        if await slug_taken(db, slug_of(c)):
            if code is not None:
                raise TempRoomError("exists", "Комната с таким адресом уже есть.")
            continue
        room = Room(slug=slug_of(c), name=name, lifetime="temporary", lifecycle="active", room_type=ROOM_TYPE, history_access="participants", peer_access="link",
                    camera_allowed=True, screen_share_allowed=True, board_allowed=True, created_by_user_id=su.user_id, created_by_name=su.display_name, encrypt_storage=se.default_for_new(cfg),
                    text_retention_days=default_text_days, audio_retention_days=default_audio_days, auto_close_at=now + timedelta(hours=cfg.peer_room_ttl_hours))
        db.add(room)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            if code is not None:
                raise TempRoomError("exists", "Комната с таким адресом уже есть.") from None
            continue
        await db.commit()
        await db.refresh(room)
        return room
    raise TempRoomError("no_code", "Не удалось подобрать адрес комнаты, повторите.", 503)


def can_own(room: Room, su: SessionUser) -> bool:
    """Технические права на ссылку, срок и состав: создатель и администратор."""
    return is_peer(room) and (su.is_admin or room.created_by_user_id == su.user_id)


def link_access(room: Room, su: SessionUser) -> bool:
    """Вход в равноправную комнату: по ссылке — любой вошедший сотрудник; «только приглашённые» — владелец, администратор и перечисленные."""
    if su.is_admin or room.created_by_user_id == su.user_id:
        return True
    if room.peer_access == "link":
        return True
    ref = (su.ad_guid or "").lower()
    return any(e.subject_type == "user" and e.subject_ref.lower() == ref for e in room.acl)


async def set_invited(db: AsyncSession, room: Room, user_ids: list[uuid.UUID]) -> int:
    """Состав приглашённых заменяется списком (пользователи, которые уже входили в систему)."""
    users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all() if user_ids else []
    room.acl = [RoomAcl(subject_type="user", subject_ref=u.ad_guid.lower(), display_name=u.display_name) for u in users if u.ad_guid]
    return len(room.acl)


async def add_invited(db: AsyncSession, room: Room, user_ids: list[uuid.UUID]) -> int:
    """Добавляет приглашённых к уже имеющимся (повторы игнорируются); возвращает, сколько стало."""
    have = {e.subject_ref.lower() for e in room.acl if e.subject_type == "user"}
    users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all() if user_ids else []
    for u in users:
        if u.ad_guid and u.ad_guid.lower() not in have:
            room.acl.append(RoomAcl(subject_type="user", subject_ref=u.ad_guid.lower(), display_name=u.display_name))
    return len(room.acl)


def remove_invited(room: Room, refs: list[str]) -> int:
    drop = {r.lower() for r in refs}
    room.acl = [e for e in room.acl if not (e.subject_type == "user" and e.subject_ref.lower() in drop)]
    return len(room.acl)
