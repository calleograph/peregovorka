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
from sqlalchemy import and_, func, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Meeting, MeetingGrant, MeetingParticipant, Room, RoomModerator


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
    if "participants" in inspect(meeting).unloaded:      # частые запросы (чат, стенограмма) не грузят всех участников — во встрече их бывают сотни
        participated = (await db.execute(select(MeetingParticipant.id).where(
            MeetingParticipant.meeting_id == meeting.id, MeetingParticipant.user_id == su.user_id).limit(1))).first() is not None
    else:
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


async def led_room_ids(db: AsyncSession, su: SessionUser) -> set[uuid.UUID]:
    """Комнаты, где человек — руководитель (то же правило, что rooms._matches), одним запросом вместо загрузки всех комнат."""
    ref = func.lower(RoomModerator.subject_ref)
    return set((await db.execute(select(RoomModerator.room_id).where(or_(
        and_(RoomModerator.subject_type == "group", ref.in_(sorted(su.groups))),
        and_(RoomModerator.subject_type == "user", ref == (su.ad_guid or "").lower()))))).scalars())


async def accessible_history(db: AsyncSession, redis: Redis, su: SessionUser, *, room_id: uuid.UUID | None = None, cap: int = 400) -> list[uuid.UUID]:
    """Встречи для «Истории» обычного пользователя (новые сверху), не больше cap кандидатов: те же правила, что can_access_meeting, но пакетно —
    несколько запросов на всю страницу вместо загрузки каждой встречи со всеми участниками и запросов по каждой встрече."""
    led = await led_room_ids(db, su)
    mine = select(MeetingParticipant.meeting_id).where(MeetingParticipant.user_id == su.user_id)
    granted_q = select(MeetingGrant.meeting_id).where(MeetingGrant.user_id == su.user_id)
    cond = Meeting.id.in_(mine) | Meeting.id.in_(granted_q)
    if led:
        cond = cond | Meeting.room_id.in_(led)
    stmt = select(Meeting.id, Meeting.room_id, Meeting.ended_at, Room.history_access).join(Room, Room.id == Meeting.room_id).where(cond)
    if room_id:
        stmt = stmt.where(Meeting.room_id == room_id)
    cand = (await db.execute(stmt.order_by(Meeting.started_at.desc()).limit(cap))).all()
    if not cand:
        return []
    ids = [c.id for c in cand]
    participated = set((await db.execute(select(MeetingParticipant.meeting_id).where(
        MeetingParticipant.user_id == su.user_id, MeetingParticipant.meeting_id.in_(ids)))).scalars())
    grants = set((await db.execute(select(MeetingGrant.meeting_id).where(MeetingGrant.user_id == su.user_id, MeetingGrant.meeting_id.in_(ids)))).scalars())
    ok: dict[uuid.UUID, bool] = {}
    need_lease = []
    for c in cand:
        p = c.id in participated
        if c.room_id in led:
            ok[c.id] = True
        elif c.ended_at is None:
            ok[c.id] = p
        elif (p and c.history_access == "participants") or c.id in grants:
            ok[c.id] = True
        else:
            need_lease.append(c.id)
    if need_lease:
        leases = await redis.mget([lease_key(mid, su.user_id) for mid in need_lease])
        ok.update({mid: v is not None for mid, v in zip(need_lease, leases)})
    return [mid for mid in ids if ok[mid]]
