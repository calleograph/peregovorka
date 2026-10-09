"""Настройки КОНКРЕТНОЙ встречи поверх настроек комнаты: рассылка материалов и языковая модель.

Цепочка наследования: системные значения по умолчанию → настройка комнаты → настройка встречи. Руководитель комнаты (или администратор) может изменить
рассылку и модель только для этой встречи, не трогая комнату; «Как в комнате» (null) возвращает значения комнаты. Рассылку можно менять, пока встреча идёт
(после завершения письма уже поставлены в очередь); модель — и после завершения (для ручного создания протокола).
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import Meeting
from ..services import roles
from ..services.audit import write_audit
from ..services.llm_choice import clean_choice, describe, llm_options, resolve_llm, room_choice
from ..services.mail_delivery import clean_spec, effective_delivery

router = APIRouter(prefix="/meetings/{meeting_id}/settings", tags=["meeting-settings"])


async def _meeting(db: AsyncSession, meeting_id: uuid.UUID, su: SessionUser) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or not roles.can_manage_room(meeting.room, su):
        raise HTTPException(status_code=404, detail="Встреча не найдена или доступ закрыт")
    return meeting


def _safe_spec(raw: dict | None) -> dict:
    try:
        return clean_spec(raw)
    except ValueError:
        return clean_spec(None)


async def _out(request: Request, db: AsyncSession, meeting: Meeting) -> dict:
    st = request.app.state
    choice = await resolve_llm(st.protocols.profiles, st.local_llm, db, meeting.room, meeting)
    choice_s = await resolve_llm(st.protocols.profiles, st.local_llm, db, meeting.room, meeting, "summary")
    return {
        "ended": meeting.ended_at is not None,
        "delivery": {"effective": _safe_spec(effective_delivery(meeting)), "room": _safe_spec(meeting.room.mail_delivery), "override": meeting.delivery_override is not None},
        "llm": {"effective": describe(choice), "room": room_choice(meeting.room), "override": meeting.llm_override},
        "llm_summary": {"effective": describe(choice_s), "room": room_choice(meeting.room, "summary"), "override": meeting.llm_summary_override},
        "llm_options": await llm_options(st.protocols.profiles, st.local_llm, db),
    }


@router.get("")
async def get_settings(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await _out(request, db, await _meeting(db, meeting_id, su))


@router.put("")
async def put_settings(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """body: delivery — настройки рассылки для этой встречи (null — как в комнате); llm — выбор модели (null — как в комнате). Передайте только то, что меняете."""
    meeting = await _meeting(db, meeting_id, su)
    changed: dict = {}
    try:
        if "delivery" in body:
            if meeting.ended_at is not None:
                raise HTTPException(status_code=409, detail="Встреча уже завершена — рассылка для неё определена. Отправить материалы можно вручную со страницы встречи.")
            new = None if body["delivery"] is None else clean_spec(body["delivery"])
            meeting.delivery_override = new
            changed["delivery"] = "как в комнате" if new is None else {"enabled": new["enabled"], "materials": new["materials"], "archive": new["archive"]}
        if "llm" in body:
            new_llm = clean_choice(body["llm"])
            meeting.llm_override = new_llm
            changed["llm"] = "как в комнате" if new_llm is None else new_llm
        if "llm_summary" in body:
            new_s = clean_choice(body["llm_summary"])
            meeting.llm_summary_override = new_s
            changed["llm_summary"] = "как в комнате" if new_s is None else new_s
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if changed:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.settings.update", target_type="meeting", target_id=str(meeting.id),
                          ip=client_ip(request), details={"room": meeting.room.slug, **changed})
    await db.commit()
    await db.refresh(meeting)
    return await _out(request, db, meeting)
