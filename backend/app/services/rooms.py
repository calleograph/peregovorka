"""Комнаты: проверка ACL и выборки."""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Room, RoomAcl, RoomModerator


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


def _peer_visible(room: Room, su: SessionUser) -> bool:
    """Равноправная комната по ссылке открыта любому вошедшему сотруднику, но в общем списке её видят только владелец, администратор и приглашённые."""
    ref = (su.ad_guid or "").lower()
    return su.is_admin or room.created_by_user_id == su.user_id or any(e.subject_type == "user" and e.subject_ref.lower() == ref for e in room.acl)


def acl_allows(room: Room, su: SessionUser) -> bool:
    """Пустой ACL = доступ только администраторам. Админ видит все включённые комнаты. Руководителю комнаты вход разрешён всегда."""
    if su.is_admin or _matches(room.moderators, su):
        return True
    if room.room_type == "peer" or room.scheduled_start is not None:
        return room.peer_access == "link" or _peer_visible(room, su)
    for entry in room.acl:
        if entry.subject_type == "group" and entry.subject_ref.lower() in su.groups:
            return True
        if entry.subject_type == "user" and entry.subject_ref.lower() == su.ad_guid.lower():
            return True
    return False


def _listed(r, su: SessionUser) -> bool:
    return acl_allows(r, su) and ((r.room_type != "peer" and r.scheduled_start is None) or _peer_visible(r, su))


class RoomView:
    """Комната только для чтения — строка таблицы без объекта ORM, поля те же, что у Room, плюс acl и moderators (строки своих таблиц).
    Для списка комнат, который опрашивает каждая открытая страница: объекты ORM обходились втрое дороже."""
    __slots__ = ("_row", "acl", "moderators")

    def __init__(self, row, acl: list, moderators: list):
        self._row, self.acl, self.moderators = row, acl, moderators

    def __getattr__(self, name: str):
        try:
            return self._row[name]
        except KeyError:
            raise AttributeError(name) from None


# Поля, которые читают список комнат (api.rooms.room_out, значок) и правило видимости (_listed): остальные ~40 столбцов (JSON, даты, SIP, LLM…)
# разбирать на каждый опрос списка незачем. Новое поле в room_out без этого списка — ошибка, её ловит test_room_list_without_orm_matches_orm_rule.
_LIST_COLUMNS = ("id", "slug", "name", "description", "max_participants", "password_hash", "transcription_enabled", "record_audio", "camera_allowed",
                 "screen_share_allowed", "board_allowed", "board_access", "room_type", "auto_record", "encrypt_storage", "e2ee_mode", "lifetime", "lifecycle",
                 "auto_close_at", "created_by_name", "created_by_user_id", "mute_on_join", "guest_token", "guest_access_enabled", "icon_kind", "icon_key",
                 "icon_rev", "scheduled_start", "peer_access", "live_enabled")


async def accessible_room_views(db: AsyncSession, su: SessionUser) -> list[RoomView]:
    """Каталог постоянных переговорок, которые человек видит (правило _listed), без объектов ORM: три запроса Core на весь список.
    Временные комнаты и конференции из календаря — в «Предстоящих встречах» (services.upcoming)."""
    t = Room.__table__
    live = (t.c.is_enabled.is_(True), t.c.lifecycle != "closed", t.c.lifetime == "permanent")
    rows = (await db.execute(select(*(t.c[n] for n in _LIST_COLUMNS)).where(*live).order_by(t.c.name))).mappings().all()
    acl: dict = {}
    mods: dict = {}
    for target, table in ((acl, RoomAcl.__table__), (mods, RoomModerator.__table__)):
        for e in (await db.execute(select(table).join(t, t.c.id == table.c.room_id).where(*live).order_by(table.c.id))).all():
            target.setdefault(e.room_id, []).append(e)
    views = [RoomView(r, acl.get(r["id"], []), mods.get(r["id"], [])) for r in rows]
    return [v for v in views if _listed(v, su)]


async def get_accessible_room(db: AsyncSession, room_id: uuid.UUID, su: SessionUser) -> Room:
    """Недоступная и несуществующая комната неразличимы (нет перечисления)."""
    room = await db.get(Room, room_id)
    if room is None or not room.is_enabled or room.lifecycle == "closed" or not acl_allows(room, su):
        raise RoomNotFound()
    return room
