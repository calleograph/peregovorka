"""Комнаты: проверка ACL и выборки."""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Room


class RoomNotFound(Exception):
    pass


# Технический идентификатор (адрес) комнаты в ссылке /rooms/<идентификатор>: латиница, цифры, «-» и «_», регистр не важен (хранится строчным).
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


def normalize_slug(value: str) -> str:
    return (value or "").strip().lower()


async def slug_taken(db: AsyncSession, slug: str, *, except_id: uuid.UUID | None = None) -> bool:
    """Занят ли адрес: нынешним адресом любой комнаты или прежним (по прежним работают переадресации, поэтому повторно их не выдаём)."""
    cur = (await db.execute(select(Room.id).where(Room.slug == slug))).scalars().first()
    if cur is not None and cur != except_id:
        return True
    for rid, hist in (await db.execute(select(Room.id, Room.slug_history).where(Room.lifetime == "permanent"))).all():
        if rid != except_id and isinstance(hist, list) and slug in hist:
            return True
    return False


async def find_by_ref(db: AsyncSession, ref: str) -> tuple[Room | None, bool]:
    """Комната по ссылке: UUID (прежние ссылки), нынешний адрес или прежний адрес. Второй элемент — это нынешний адрес (иначе ссылку надо
    заменить переадресацией)."""
    ref = (ref or "").strip()
    try:
        room = await db.get(Room, uuid.UUID(ref))
        return room, False
    except ValueError:
        pass
    slug = normalize_slug(ref)
    room = (await db.execute(select(Room).where(Room.slug == slug))).scalars().first()
    if room is not None:
        return room, ref == room.slug
    for r in (await db.execute(select(Room).where(Room.lifetime == "permanent"))).scalars().all():
        if isinstance(r.slug_history, list) and slug in r.slug_history:
            return r, False
    return None, False


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
    rooms = (await db.execute(select(Room).where(Room.is_enabled.is_(True), Room.lifecycle != "closed").order_by(Room.name))).scalars().all()
    return [r for r in rooms if acl_allows(r, su)]


async def get_accessible_room(db: AsyncSession, room_id: uuid.UUID, su: SessionUser) -> Room:
    """Недоступная и несуществующая комната неразличимы (нет перечисления)."""
    room = await db.get(Room, room_id)
    if room is None or not room.is_enabled or room.lifecycle == "closed" or not acl_allows(room, su):
        raise RoomNotFound()
    return room
