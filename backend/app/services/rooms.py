"""Комнаты: проверка ACL и выборки."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Room


class RoomNotFound(Exception):
    pass


def _matches(entries, su: SessionUser) -> bool:
    for entry in entries:
        if entry.subject_type == "group" and entry.subject_ref.lower() in su.groups:
            return True
        if entry.subject_type == "user" and entry.subject_ref.lower() == su.ad_guid.lower():
            return True
    return False


def is_moderator(room: Room, su: SessionUser) -> bool:
    """Администратор сервера — руководитель любой комнаты; остальные — по списку руководителей комнаты (группа AD или пользователь)."""
    return su.is_admin or _matches(room.moderators, su)


def acl_allows(room: Room, su: SessionUser) -> bool:
    """Пустой ACL = доступ только администраторам. Админ видит все включённые комнаты. Руководителю комнаты вход разрешён всегда."""
    if su.is_admin or _matches(room.moderators, su):
        return True
    for entry in room.acl:
        if entry.subject_type == "group" and entry.subject_ref.lower() in su.groups:
            return True
        if entry.subject_type == "user" and entry.subject_ref.lower() == su.ad_guid.lower():
            return True
    return False


async def list_accessible_rooms(db: AsyncSession, su: SessionUser) -> list[Room]:
    rooms = (await db.execute(select(Room).where(Room.is_enabled.is_(True)).order_by(Room.name))).scalars().all()
    return [r for r in rooms if acl_allows(r, su)]


async def get_accessible_room(db: AsyncSession, room_id: uuid.UUID, su: SessionUser) -> Room:
    """Недоступная и несуществующая комната неразличимы (нет перечисления)."""
    room = await db.get(Room, room_id)
    if room is None or not room.is_enabled or not acl_allows(room, su):
        raise RoomNotFound()
    return room
