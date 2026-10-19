"""Управление доступом к отдельной встрече («Управление доступом» в истории): руководитель комнаты и администратор выдают и отзывают право ПРОСМОТРА встречи и её материалов
конкретным людям из каталога (поиск по ФИО, логину, почте; по группам — с отдельным подтверждением массовой выдачи).

Это ровно тот же механизм, что и явные разрешения администратора (`meeting_grants`, проверка в `services/access.can_access_meeting`): параллельной системы прав нет. Выданное право:
  * относится только к этой встрече и не открывает остальную историю комнаты;
  * даёт просмотр и скачивание материалов, но НЕ даёт ни прав руководителя, ни права выдавать доступ дальше (эти методы требуют руководителя комнаты или администратора);
  * действует и после завершения встречи; отзыв действует сразу (следующий же запрос получает 404).
Рассылка материалов по почте доступа к остальным материалам встречи не открывает.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.directory import DirectoryError
from ..auth.throttle import within_limit
from ..models import Meeting, MeetingGrant, User
from ..services import roles
from ..services.access import release_lease
from ..services.audit import write_audit

router = APIRouter(prefix="/meetings/{meeting_id}/access", tags=["meeting-access"])
MAX_GROUP = 300


async def _meeting(db: AsyncSession, meeting_id: uuid.UUID, su: SessionUser) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or not roles.can_manage_room(meeting.room, su):          # руководитель этой комнаты или администратор; остальным встреча не раскрывается
        raise HTTPException(status_code=404, detail="Встреча не найдена или доступ закрыт")
    return meeting


def _directory_error(exc: DirectoryError) -> HTTPException:
    return HTTPException(status_code=503, detail="Каталог не подключён. Администратор добавляет подключение в разделе «LDAP и доступ»." if exc.code == "not_configured"
                         else f"Каталог недоступен ({exc.code})")


async def _grants(db: AsyncSession, meeting_id: uuid.UUID) -> list[MeetingGrant]:
    return list((await db.execute(select(MeetingGrant).where(MeetingGrant.meeting_id == meeting_id).order_by(MeetingGrant.created_at))).scalars().unique())


def _audit_details(meeting: Meeting, **extra) -> dict:
    return {"room_id": str(meeting.room_id), "meeting_id": str(meeting.id), **extra}


@router.get("")
async def list_access(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Кто уже имеет доступ: явные разрешения (их можно отозвать), участники встречи (доступ по политике комнаты) и руководители комнаты."""
    meeting = await _meeting(db, meeting_id, su)
    grants = await _grants(db, meeting_id)
    seen = {g.user_id for g in grants}
    participants = []
    for p in sorted(meeting.participants, key=lambda x: x.joined_at):
        if p.user_id in seen or p.user is None:
            continue
        seen.add(p.user_id)
        participants.append({"user_id": str(p.user_id), "display_name": p.user.display_name, "sam_account_name": p.user.sam_account_name})
    return {"history_access": meeting.room.history_access, "ended": meeting.ended_at is not None,
            "grants": [{"user_id": str(g.user_id), "display_name": g.user.display_name, "sam_account_name": g.user.sam_account_name, "email": g.user.email, "granted_by": g.granted_by,
                        "created_at": g.created_at} for g in grants],
            "participants": participants,
            "leaders": [m.display_name or m.subject_ref for m in meeting.room.moderators],
            "note": "Выданное право — только просмотр и скачивание материалов этой встречи. Руководителем комнаты и выдавать доступ дальше этот человек не становится."}


@router.get("/search")
async def search(meeting_id: uuid.UUID, request: Request, kind: str = Query(pattern="^(group|user)$"), q: str = Query(min_length=2, max_length=100),
                 su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Поиск в каталоге: пользователи — по ФИО, логину, почте; группы — по названию. У пользователей отмечено, у кого доступ к этой встрече уже есть."""
    meeting = await _meeting(db, meeting_id, su)
    if not await within_limit(request.app.state.redis, f"rl:accesssearch:{su.user_id}", 120, 600):
        raise HTTPException(status_code=429, detail="Слишком много запросов поиска. Повторите через несколько минут.", headers={"Retry-After": "60"})
    try:
        hits = await asyncio.to_thread(request.app.state.directory.search, kind, q, 20)
    except DirectoryError as exc:
        raise _directory_error(exc) from None
    if kind == "user":
        have = {u.ad_guid.lower() for u in (await db.execute(select(User).where(User.id.in_([g.user_id for g in await _grants(db, meeting_id)] or [uuid.UUID(int=0)])))).scalars()}
        have |= {p.user.ad_guid.lower() for p in meeting.participants if p.user is not None}
        for h in hits:
            h["has_access"] = h["ref"].lower() in have
    return hits


async def _create_from_directory(request: Request, db: AsyncSession, ref: str, *, hint: dict | None = None) -> User:
    found = hint
    if found is None or not found.get("sam"):
        raise HTTPException(status_code=404, detail="Пользователь не найден в каталоге")
    user = User(ad_guid=ref, sam_account_name=found["sam"], display_name=found.get("name") or found["sam"], email=found.get("email") or None, is_active=True)
    db.add(user)
    await db.flush()
    return user


@router.post("", status_code=201)
async def grant_user(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Выдать доступ одному человеку. body: ref — идентификатор из результатов поиска. Данные человека проверяются по каталогу."""
    meeting = await _meeting(db, meeting_id, su)
    ref = str(body.get("ref") or "").strip()
    if not ref or len(ref) > 64:
        raise HTTPException(status_code=422, detail="Выберите человека из результатов поиска")
    user = (await db.execute(select(User).where(User.ad_guid == ref))).scalar_one_or_none()
    if user is None:
        hint = None
        try:
            for h in await asyncio.to_thread(request.app.state.directory.search, "user", str(body.get("sam") or body.get("name") or ""), 20):
                if h["ref"].lower() == ref.lower():
                    hint = h
                    break
        except DirectoryError as exc:
            raise _directory_error(exc) from None
        user = await _create_from_directory(request, db, ref, hint=hint)
    if not user.is_active:
        raise HTTPException(status_code=409, detail="Учётная запись отключена администратором системы")
    n = await _add_grants(db, meeting, [user], su)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.grant", target_type="meeting", target_id=str(meeting_id), ip=client_ip(request),
                      details=_audit_details(meeting, user=user.sam_account_name, user_id=str(user.id), via="user", by="leader" if not su.is_admin else "admin", new=bool(n),
                                             rights="view"))
    await db.commit()
    _journal(request, su, meeting, "access_grant", f"Доступ к встрече выдан: {user.display_name}")
    return {"ok": True, "added": n}


@router.get("/group-members")
async def group_members(meeting_id: uuid.UUID, request: Request, dn: str = Query(min_length=3, max_length=500), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Состав группы (для подтверждения массовой выдачи): число людей и первые имена. Сам по себе просмотр группы доступа не выдаёт."""
    await _meeting(db, meeting_id, su)
    try:
        members = await asyncio.to_thread(request.app.state.directory.members, dn, MAX_GROUP + 1)
    except DirectoryError as exc:
        raise _directory_error(exc) from None
    return {"count": min(len(members), MAX_GROUP), "too_many": len(members) > MAX_GROUP, "max": MAX_GROUP, "sample": [m.get("name") or m.get("sam") for m in members[:15]]}


@router.post("/group", status_code=201)
async def grant_group(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Массовая выдача доступа членам группы. Требует ЯВНОГО подтверждения: `confirm_count` должно совпасть с числом членов группы, показанным пользователю."""
    meeting = await _meeting(db, meeting_id, su)
    dn = str(body.get("dn") or "").strip()
    if not dn or len(dn) > 500:
        raise HTTPException(status_code=422, detail="Выберите группу из результатов поиска")
    try:
        members = await asyncio.to_thread(request.app.state.directory.members, dn, MAX_GROUP + 1)
    except DirectoryError as exc:
        raise _directory_error(exc) from None
    if len(members) > MAX_GROUP:
        raise HTTPException(status_code=422, detail=f"В группе больше {MAX_GROUP} человек: выдайте доступ по одному или обратитесь к администратору.")
    if body.get("confirm_count") != len(members):
        raise HTTPException(status_code=409, detail={"code": "confirm_required", "message": f"Подтвердите выдачу доступа {len(members)} сотрудникам группы.", "count": len(members)})
    users: list[User] = []
    for m in members:
        ref = str(m.get("ref") or "")
        if not ref:
            continue
        u = (await db.execute(select(User).where(User.ad_guid == ref))).scalar_one_or_none()
        if u is None:
            u = await _create_from_directory(request, db, ref, hint=m)
        if u.is_active:
            users.append(u)
    n = await _add_grants(db, meeting, users, su)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.grant_group", target_type="meeting", target_id=str(meeting_id), ip=client_ip(request),
                      details=_audit_details(meeting, group=dn[:200], members=len(users), added=n, via="group", by="leader" if not su.is_admin else "admin", rights="view"))
    await db.commit()
    _journal(request, su, meeting, "access_grant_group", f"Доступ к встрече выдан группе ({n} чел.)")
    return {"ok": True, "added": n, "members": len(users)}


@router.delete("/{user_id}", status_code=204)
async def revoke(meeting_id: uuid.UUID, user_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Отозвать доступ: действует сразу — материалы, скачивание и страница встречи перестают открываться у этого человека; «аренда» просмотра сразу после встречи тоже сбрасывается."""
    meeting = await _meeting(db, meeting_id, su)
    user = await db.get(User, user_id)
    res = await db.execute(delete(MeetingGrant).where(MeetingGrant.meeting_id == meeting_id, MeetingGrant.user_id == user_id))
    await release_lease(request.app.state.redis, meeting_id, user_id)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.revoke", target_type="meeting", target_id=str(meeting_id), ip=client_ip(request),
                      details=_audit_details(meeting, user=user.sam_account_name if user else None, user_id=str(user_id), removed=res.rowcount, by="leader" if not su.is_admin else "admin"))
    await db.commit()
    _journal(request, su, meeting, "access_revoke", f"Доступ к встрече отозван: {user.display_name if user else user_id}")


async def _add_grants(db: AsyncSession, meeting: Meeting, users: list[User], su: SessionUser) -> int:
    have = {g.user_id for g in await _grants(db, meeting.id)}
    n = 0
    for u in users:
        if u.id in have:
            continue
        db.add(MeetingGrant(meeting_id=meeting.id, user_id=u.id, granted_by=su.display_name))
        have.add(u.id)
        n += 1
    return n


def _journal(request: Request, su: SessionUser, meeting: Meeting, event: str, message: str) -> None:
    request.app.state.journal.emit("room", event, user=su.sam_account_name, room=meeting.room.name, meeting_id=str(meeting.id), ip=client_ip(request), message=message)
