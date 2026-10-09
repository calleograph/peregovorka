"""Временные переговорки: создание пользователем, лимиты, жизненный цикл active → grace_period → closed.

Комната не удаляется: после закрытия её строка остаётся (тип `lifetime=temporary`, `lifecycle=closed`, `closed_at`), поэтому встречи, записи,
стенограммы, чат, доска, протоколы и журнал остаются связанными с ней и доступны через «Историю» по обычным правилам доступа.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Meeting, Room, RoomAcl, RoomModerator, utcnow
from .rooms import slug_taken
from .settings import GeneralSettings

log = logging.getLogger("app.temp_rooms")

# без похожих знаков (0/o, 1/l/i): короткий адрес читается и диктуется без ошибок; 31^6 ≈ 8.9·10^8 вариантов — перебором не найти
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PREFIX = "tmp-"


class TempRoomError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(code)
        self.code, self.message, self.status = code, message, status


def new_code() -> str:
    return PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(6))


async def active_count(db: AsyncSession, user_id=None) -> int:
    q = select(func.count()).select_from(Room).where(Room.lifetime == "temporary", Room.lifecycle != "closed")
    if user_id is not None:
        q = q.where(Room.created_by_user_id == user_id)
    return (await db.execute(q)).scalar_one()


async def policy(db: AsyncSession, cfg: GeneralSettings, su: SessionUser) -> dict:
    mine = await active_count(db, su.user_id)
    return {"enabled": cfg.temp_rooms_enabled, "max_per_user": cfg.temp_room_max_per_user, "active_mine": mine,
            "can_create": cfg.temp_rooms_enabled and mine < cfg.temp_room_max_per_user, "grace_minutes": cfg.temp_room_grace_minutes}


async def create(db: AsyncSession, su: SessionUser, name: str, cfg: GeneralSettings, *, default_text_days: int | None, default_audio_days: int | None) -> Room:
    """Создаёт временную комнату; создатель — руководитель (владелец) и первый допущенный. Остальное — значения по умолчанию."""
    if not cfg.temp_rooms_enabled:
        raise TempRoomError("disabled", "Временные переговорки отключены администратором.", 403)
    name = (name or "").strip()
    if not 1 <= len(name) <= 200:
        raise TempRoomError("bad_name", "Укажите название встречи (до 200 знаков).", 422)
    if await active_count(db, su.user_id) >= cfg.temp_room_max_per_user:
        raise TempRoomError("limit_user", f"У вас уже {cfg.temp_room_max_per_user} активных временных переговорок — завершите одну из них.")
    if await active_count(db) >= cfg.temp_room_max_total:
        raise TempRoomError("limit_total", "Достигнут общий предел активных временных переговорок. Повторите позже.", 429)
    now = utcnow()
    for _ in range(12):
        code = new_code()
        if await slug_taken(db, code):
            continue
        room = Room(slug=code, name=name, lifetime="temporary", lifecycle="active", history_access="participants",
                    created_by_user_id=su.user_id, created_by_name=su.display_name, text_retention_days=default_text_days,
                    audio_retention_days=default_audio_days, auto_close_at=now + timedelta(minutes=cfg.temp_room_idle_minutes))
        if su.ad_guid and not su.local:      # локальный администратор и так руководит всеми комнатами и в списках не нуждается
            ref = su.ad_guid.lower()
            room.acl = [RoomAcl(subject_type="user", subject_ref=ref, display_name=su.display_name)]
            room.moderators = [RoomModerator(subject_type="user", subject_ref=ref, display_name=su.display_name)]
        db.add(room)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            continue
        await db.commit()
        await db.refresh(room)
        return room
    raise TempRoomError("no_code", "Не удалось подобрать адрес комнаты, повторите.", 503)


def sync_lifecycle(room: Room, meeting: Meeting | None, cfg: GeneralSettings | None) -> None:
    """Состояние временной комнаты по встрече: есть люди → active; все вышли → grace_period и срок закрытия. Закрытую не возвращает."""
    if room.lifetime != "temporary" or room.lifecycle == "closed":
        return
    grace = timedelta(minutes=(cfg.temp_room_grace_minutes if cfg else 5))
    if meeting is not None and meeting.ended_at is None and meeting.empty_since is not None:
        room.lifecycle, room.auto_close_at = "grace_period", meeting.empty_since + grace
    elif meeting is not None and meeting.ended_at is None:
        room.lifecycle = "active"
        room.auto_close_at = utcnow() + timedelta(hours=(cfg.temp_room_max_hours if cfg else 12))     # предельный срок жизни, пока идёт встреча


def close(room: Room, reason: str) -> bool:
    if room.lifetime != "temporary" or room.lifecycle == "closed":
        return False
    room.lifecycle, room.closed_at, room.auto_close_at = "closed", utcnow(), None
    log.info("Временная переговорка закрыта", extra={"room": room.slug, "reason": reason})
    return True
