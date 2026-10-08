"""Доступ к встрече, её стенограмме, протоколам и записям.

Правила (проверяются на backend при КАЖДОМ запросе; знание идентификатора/URL встречи доступа не даёт):
  * администратор — ко всем встречам;
  * идущая встреча — тем, кто в ней участвовал (есть запись участия);
  * завершённая встреча — только если выполнено одно из:
      1. «аренда» (lease): участник был в комнате в момент завершения и ещё не покинул страницу встречи
         (действует до явного выхода со страницы или до истечения post_meeting_access_minutes);
      2. политика комнаты history_access = 'participants' и пользователь участвовал во встрече;
      3. явное разрешение администратора (meeting_grants).
Обычный пользователь, покинувший страницу завершённой встречи, доступа не сохраняет.
"""
from __future__ import annotations

import uuid

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Meeting, MeetingGrant


def lease_key(meeting_id: uuid.UUID | str, user_id: uuid.UUID | str) -> str:
    return f"mtgview:{meeting_id}:{user_id}"


async def grant_leases(redis: Redis, meeting_id: uuid.UUID, user_ids: list[uuid.UUID], minutes: int) -> None:
    for uid in set(user_ids):
        await redis.set(lease_key(meeting_id, uid), "1", ex=max(60, minutes * 60))


async def release_lease(redis: Redis, meeting_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await redis.delete(lease_key(meeting_id, user_id))


async def can_access_meeting_actor(db: AsyncSession, redis: Redis, meeting: Meeting, actor) -> bool:
    """Доступ «актора» (сотрудника или гостя). Гость — только к ИДУЩЕЙ встрече, с которой связана его сессия;
    история завершённых встреч ему недоступна."""
    if actor.is_guest:
        return meeting.ended_at is None and str(meeting.id) == actor.guest.meeting_id
    return await can_access_meeting(db, redis, meeting, actor.user)


async def can_access_meeting(db: AsyncSession, redis: Redis, meeting: Meeting, su: SessionUser) -> bool:
    if su.is_admin:
        return True
    from . import roles  # noqa: PLC0415 — руководитель комнаты видит встречи своей комнаты (материалы, протоколы, рассылка)

    if roles.is_room_leader(meeting.room, su):
        return True
    participated = any(p.user_id == su.user_id for p in meeting.participants)
    if meeting.ended_at is None:
        return participated
    if await redis.exists(lease_key(meeting.id, su.user_id)):
        return True
    if participated and meeting.room.history_access == "participants":
        return True
    grant = (await db.execute(select(MeetingGrant.id).where(
        MeetingGrant.meeting_id == meeting.id, MeetingGrant.user_id == su.user_id))).first()
    return grant is not None
