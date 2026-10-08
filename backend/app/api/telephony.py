"""Телефония в комнате: кнопка «Позвонить» (исходящий вызов в идущую встречу) и отключение телефонного участника. Только руководители комнаты и администраторы.

Исходящий вызов создаёт телефонного участника через LiveKit SIP (CreateSIPParticipant): он появляется среди участников как «Телефон: +7…», а его звук попадает в
стенограмму и запись так же, как звук браузерного участника. Номер проверяется по списку допустимых номеров SIP-профиля; вызовы пишутся в аудит (номер частично скрыт).
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import GuestParticipant, Meeting, Room
from ..services import roles
from ..services.audit import write_audit
from ..services.sip import SipError, mask_number, normalize_number

router = APIRouter(prefix="/rooms/{room_id}/phone", tags=["telephony"])


async def _room(db: AsyncSession, room_id: uuid.UUID, su: SessionUser) -> Room:
    room = await db.get(Room, room_id)
    if room is None or not roles.can_manage_room(room, su):
        raise HTTPException(status_code=404, detail="Комната не найдена или нет прав")
    return room


async def _active(db: AsyncSession, room: Room) -> Meeting | None:
    return (await db.execute(select(Meeting).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).scalars().first()


@router.get("")
async def phone_state(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Что показывать в комнате: доступны ли звонки, сохранённые номера, телефонные участники текущей встречи."""
    room = await _room(db, room_id, su)
    st = request.app.state
    profile = await st.sip.for_room(db, room, "outbound") if room.sip_mode != "off" else None
    meeting = await _active(db, room)
    phones = []
    if meeting is not None:
        rows = (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id == meeting.id, GuestParticipant.participant_type == "phone",
                                                                GuestParticipant.left_at.is_(None)))).scalars().all()
        phones = [{"guest_id": str(g.id), "display_name": g.display_name} for g in rows]
    enabled = str(st.settings.sip_enabled).strip().lower() in ("yes", "true", "1", "on")
    reason = None
    if room.sip_mode == "off":
        reason = "Телефония для комнаты отключена (Настройки комнаты → Телефония)."
    elif not room.sip_allow_outbound:
        reason = "Исходящие звонки в этой комнате не разрешены."
    elif not enabled:
        reason = "Телефония не включена на сервере — обратитесь к администратору (Администрирование → SIP-телефония)."
    elif profile is None or not profile.lk_outbound_trunk_id:
        reason = "Не выбран исходящий SIP-профиль или он не синхронизирован с LiveKit."
    return {"can_call": reason is None, "reason": reason, "profile": profile.name if profile else None, "contacts": list(room.sip_contacts or []),
            "extension": room.sip_extension, "allow_inbound": room.sip_allow_inbound, "active_meeting_id": str(meeting.id) if meeting else None, "phones": phones,
            "allowed_prefixes": list(profile.allowed_numbers or []) if profile else []}


@router.post("/call")
async def phone_call(room_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room(db, room_id, su)
    meeting = await _active(db, room)
    if meeting is None:
        raise HTTPException(status_code=409, detail="Встреча не идёт — позвонить можно из комнаты во время встречи.")
    raw = body.get("number")
    if not raw and isinstance(body.get("contact"), int):
        contacts = list(room.sip_contacts or [])
        raw = contacts[body["contact"]]["number"] if 0 <= body["contact"] < len(contacts) else None
    if not raw:
        raise HTTPException(status_code=422, detail="Введите номер или выберите сохранённый.")
    try:
        number = normalize_number(str(raw))
        res = await request.app.state.sip_routing.call(db, room, meeting, number, by=su.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except SipError as exc:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.phone.call_failed", target_type="room", target_id=str(room.id), ip=client_ip(request),
                          details={"room": room.slug, "number": mask_number(number), "sip_status": exc.sip_status, "reason": exc.message[:200]})
        await db.commit()
        raise HTTPException(status_code=exc.status if exc.status in (403, 409, 422) else 502, detail=exc.message) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.phone.call", target_type="room", target_id=str(room.id), ip=client_ip(request),
                      details={"room": room.slug, "meeting_id": str(meeting.id), "number": mask_number(number), "profile": res["profile"]})
    await db.commit()
    from ..services import events  # noqa: PLC0415

    await events.publish(request.app.state.redis, meeting.id, {"type": "participant_joined", "guest_id": res["guest_id"], "display_name": res["display_name"], "participant_type": "phone"})
    return res


@router.post("/hangup")
async def phone_hangup(room_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room(db, room_id, su)
    meeting = await _active(db, room)
    try:
        gid = uuid.UUID(str(body.get("guest_id")))
    except ValueError:
        raise HTTPException(status_code=422, detail="guest_id") from None
    if meeting is None or not await request.app.state.sip_routing.hangup(db, room, meeting, gid):
        raise HTTPException(status_code=404, detail="Телефонный участник не найден")
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.phone.hangup", target_type="room", target_id=str(room.id), ip=client_ip(request),
                      details={"room": room.slug, "meeting_id": str(meeting.id)})
    await db.commit()
    from ..services import events  # noqa: PLC0415

    await events.publish(request.app.state.redis, meeting.id, {"type": "participant_left", "guest_id": str(gid), "participant_type": "phone"})
    return {"ok": True}
