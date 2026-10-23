"""«Предстоящие встречи» на главной странице: запланированные конференции из календаря Outlook и временные комнаты (не постоянные переговорки).

Кто видит встречу: организатор, приглашённые (по сотруднику или адресу почты), руководители комнаты, те, кому доступ выдан отдельно (список доступа
комнаты), и администратор — у чужих встреч у него признак `mine=false`, интерфейс по умолчанию показывает только свои. Наличие встречи в списке доступа
не даёт: вход по-прежнему решают правила комнаты (`acl_allows`). Список приглашённых видят организатор, руководители, администратор и сами приглашённые;
адреса почты — только организатор, руководители и администратор. Остальным — только число приглашённых.

Отсчёт времени считает браузер по меткам UTC и времени сервера (`server_time`): сервер опрашивается редко, а не каждую секунду.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Meeting, MeetingParticipant, Room, RoomAcl, RoomInvitee, RoomModerator, User
from . import outlook as ol
from . import peer_rooms
from . import recurrence as rc
from .rooms import RoomView, _listed, _matches, acl_allows

CANCELLED_SHOWN = timedelta(days=7)        # отменённая встреча видна со статусом «Отменена», пока не прошло её время (но не дольше недели после отмены)
PREVIEW = 3                                 # сколько имён приглашённых в карточке; остальные — «+N»
_COLS = ("id", "slug", "name", "room_type", "lifetime", "lifecycle", "created_by_user_id", "created_by_name", "created_at", "peer_access", "password_hash", "guest_token", "guest_access_enabled",
         "scheduled_start", "scheduled_end", "join_opens_at", "recurring", "cancelled_at", "outlook_sync_state", "outlook_synced_at", "outlook_recurrence", "outlook_tz")


async def _my_email(db: AsyncSession, su: SessionUser) -> str:
    return ((await db.execute(select(User.email).where(User.id == su.user_id))).scalar_one_or_none() or "").strip().lower()


async def _invited(db: AsyncSession, su: SessionUser, room_ids: list) -> set:
    """Комнаты, куда человек приглашён: как сотрудник (найден по адресу при сохранении) или по адресу почты (сотрудник вошёл впервые позже)."""
    if not room_ids:
        return set()
    inv = RoomInvitee.__table__
    email = await _my_email(db, su)
    who = or_(inv.c.user_id == su.user_id, inv.c.email == email) if email else inv.c.user_id == su.user_id
    return set((await db.execute(select(inv.c.room_id).where(inv.c.room_id.in_(room_ids), who))).scalars().all())


def _roles(v, su: SessionUser, invited: set) -> tuple[bool, bool, bool, bool]:
    """(видит, «моя», видит список приглашённых, видит адреса почты)."""
    organizer = v.created_by_user_id == su.user_id
    leader = _matches(v.moderators, su)
    mine = organizer or v.id in invited or leader or _matches(v.acl, su)
    visible = (mine or su.is_admin) if v.scheduled_start is not None else _listed(v, su)
    names = organizer or leader or su.is_admin or v.id in invited
    emails = organizer or leader or su.is_admin
    return visible, mine, names, emails


async def _views(db: AsyncSession, cond) -> list[RoomView]:
    t = Room.__table__
    rows = (await db.execute(select(*(t.c[n] for n in _COLS)).where(*cond))).mappings().all()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    acl: dict = {}
    mods: dict = {}
    for target, table in ((acl, RoomAcl.__table__), (mods, RoomModerator.__table__)):
        for e in (await db.execute(select(table).where(table.c.room_id.in_(ids)).order_by(table.c.id))).all():
            target.setdefault(e.room_id, []).append(e)
    return [RoomView(r, acl.get(r["id"], []), mods.get(r["id"], [])) for r in rows]


def _url(v) -> str:
    if v.scheduled_start is not None:
        return "/m/" + v.slug.removeprefix(ol.PREFIX)          # страница конференции: до открытия — обратный отсчёт, после — сразу в комнату
    if v.room_type == peer_rooms.ROOM_TYPE:
        return "/p/" + v.slug.removeprefix(peer_rooms.PREFIX)
    return "/rooms/" + v.slug


async def upcoming(db: AsyncSession, su: SessionUser, now: datetime) -> list[dict]:
    t = Room.__table__
    cond = (t.c.is_enabled.is_(True), t.c.lifetime == "temporary",
            or_(t.c.lifecycle != "closed", and_(t.c.cancelled_at.is_not(None), t.c.cancelled_at > now - CANCELLED_SHOWN, t.c.scheduled_end > now)))
    views = await _views(db, cond)
    invited = await _invited(db, su, [v.id for v in views])
    shown = []
    for v in views:
        visible, mine, names, emails = _roles(v, su, invited)
        if visible:
            shown.append((v, mine, names, emails))
    if not shown:
        return []
    ids = [v.id for v, *_ in shown]
    live = {r.room_id: r for r in (await db.execute(select(Meeting.id, Meeting.room_id, Meeting.started_at).where(Meeting.room_id.in_(ids), Meeting.ended_at.is_(None)))).all()}
    people: dict = {}
    if live:
        people = dict((await db.execute(select(MeetingParticipant.meeting_id, func.count(func.distinct(MeetingParticipant.user_id)))
                                        .where(MeetingParticipant.meeting_id.in_([m.id for m in live.values()]), MeetingParticipant.left_at.is_(None))
                                        .group_by(MeetingParticipant.meeting_id))).all())
    last_end = dict((await db.execute(select(Meeting.room_id, func.max(Meeting.ended_at)).where(Meeting.room_id.in_(ids), Meeting.ended_at.is_not(None))
                                      .group_by(Meeting.room_id))).all())
    inv = RoomInvitee.__table__
    counts = dict((await db.execute(select(inv.c.room_id, func.count()).where(inv.c.room_id.in_(ids)).group_by(inv.c.room_id))).all())
    preview = await _preview(db, [v.id for v, _m, names, _e in shown if names and counts.get(v.id)])
    out = []
    for v, mine, names, emails in shown:
        m = live.get(v.id)
        scheduled = v.scheduled_start is not None
        start, end, opens = v.scheduled_start or v.created_at, v.scheduled_end, v.join_opens_at
        if v.recurring:              # серия: ближайшее занятие по шаблону (вход у серии открыт всегда); шаблона нет — время первого занятия без окончания
            nxt = rc.next_occurrence(v.outlook_recurrence, v.outlook_tz, now)
            start, end, opens = (nxt[0], nxt[1], None) if nxt else (start, None, None)
        out.append({
            "id": str(v.id), "slug": v.slug, "name": v.name,
            "kind": "scheduled" if scheduled else ("peer" if v.room_type == peer_rooms.ROOM_TYPE else "temporary"),
            "start": start, "end": end, "opens_at": opens, "recurring": bool(v.recurring),
            "organizer": v.created_by_name, "cancelled": v.cancelled_at is not None,
            "live": {"started_at": m.started_at, "participants": people.get(m.id, 0)} if m is not None else None,
            "last_ended_at": last_end.get(v.id),
            "invitees": {"count": counts.get(v.id, 0), "names": [n or (e if emails else "Без имени") for n, e in preview.get(v.id, [])] if names else None, "can_list": names},
            "mine": mine, "can_join": v.cancelled_at is None and acl_allows(v, su), "can_manage": emails, "has_password": bool(v.password_hash),
            "guest_token": v.guest_token if emails and v.guest_access_enabled and not scheduled else None,      # у конференции из календаря гости входят по той же ссылке /m/
            "url": _url(v), "sync": {"state": v.outlook_sync_state, "at": v.outlook_synced_at} if scheduled and v.outlook_sync_state else None,
        })
    # ближайшие первыми: идущие — по фактическому началу, серии — по ближайшему занятию; серия без шаблона (занятие неизвестно) — после остальных
    out.sort(key=lambda x: (x["recurring"] and x["live"] is None and x["end"] is None, x["live"]["started_at"] if x["live"] else x["start"]))
    return out


async def _preview(db: AsyncSession, room_ids: list) -> dict[uuid.UUID, list[tuple[str | None, str]]]:
    """Первые PREVIEW имён приглашённых по каждой комнате одним запросом (оконная функция): обязательные — раньше, с именем — раньше безымянных, затем по алфавиту."""
    if not room_ids:
        return {}
    inv, u = RoomInvitee.__table__, User.__table__
    label = func.coalesce(inv.c.name, u.c.display_name)
    rn = func.row_number().over(partition_by=inv.c.room_id, order_by=(inv.c.optional, label.is_(None), func.lower(func.coalesce(label, inv.c.email)))).label("rn")
    sub = select(inv.c.room_id, label.label("name"), inv.c.email, rn).select_from(inv.outerjoin(u, u.c.id == inv.c.user_id)).where(inv.c.room_id.in_(room_ids)).subquery()
    out: dict = {}
    for rid, name, email in (await db.execute(select(sub.c.room_id, sub.c.name, sub.c.email).where(sub.c.rn <= PREVIEW).order_by(sub.c.room_id, sub.c.rn))).all():
        out.setdefault(rid, []).append((name, email))
    return out


async def invitees(db: AsyncSession, su: SessionUser, room_id: uuid.UUID) -> list[dict] | None:
    """Полный список приглашённых одной встречи. None — встречи нет или человеку о ней знать не положено (404); PermissionError — встречу видит,
    но список ему не показывается (403)."""
    t = Room.__table__
    views = await _views(db, (t.c.id == room_id, t.c.is_enabled.is_(True), t.c.lifetime == "temporary"))
    if not views:
        return None
    v = views[0]
    visible, _mine, names, emails = _roles(v, su, await _invited(db, su, [v.id]))
    if not visible:
        return None
    if not names:
        raise PermissionError
    inv, u = RoomInvitee.__table__, User.__table__
    rows = (await db.execute(select(inv.c.email, inv.c.name, inv.c.optional, inv.c.user_id, u.c.display_name).select_from(inv.outerjoin(u, u.c.id == inv.c.user_id))
                             .where(inv.c.room_id == v.id))).all()
    out = [{"name": r.name or r.display_name, "email": r.email if emails else None, "optional": bool(r.optional),
            "employee": r.user_id is not None} for r in rows]
    out.sort(key=lambda x: (x["optional"], x["name"] is None, (x["name"] or x["email"] or "").lower()))
    return out
