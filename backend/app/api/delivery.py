"""Ручная отправка материалов завершённой встречи: «Отправить материалы» → экран предварительного просмотра (получатели и документы) → подтверждение.

Доступно руководителям комнаты и администраторам. Повторная отправка допускается и каждый раз пишется в аудит. SMTP-реквизиты здесь не видны.
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
from ..services.mail import valid_email
from ..services.mail_delivery import MATERIALS, Recipient, clean_spec

router = APIRouter(prefix="/meetings/{meeting_id}/delivery", tags=["delivery"])


async def _meeting(request: Request, db: AsyncSession, meeting_id: uuid.UUID, su: SessionUser) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    # руководителю комнаты доступ к материалам её встреч не требует участия в них; остальным встреча не раскрывается
    if meeting is None or not roles.can_manage_room(meeting.room, su):
        raise HTTPException(status_code=404, detail="Встреча не найдена или доступ закрыт")
    if meeting.ended_at is None:
        raise HTTPException(status_code=409, detail="Встреча ещё идёт — материалы можно отправить после её завершения")
    return meeting


def _spec(meeting: Meeting) -> dict:
    try:
        return clean_spec(meeting.room.mail_delivery or {"enabled": False, "materials": [k for k in ("protocol", "summary")], "recipients": {"leaders": True, "participants": True}})
    except ValueError:
        return clean_spec(None)


@router.get("")
async def preview(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Что и кому будет отправлено (по настройкам комнаты) + что отправляли раньше."""
    meeting = await _meeting(request, db, meeting_id, su)
    spec = _spec(meeting)
    if not spec["materials"]:
        spec["materials"] = ["protocol", "summary"]
    plan = await request.app.state.delivery.plan(db, meeting, spec)
    plan["available_kinds"] = [{"kind": k, "label": d.label, "describe": d.describe} for k, d in MATERIALS.items()]
    plan["selected"] = spec["materials"]
    return plan


@router.post("/send")
async def send_materials(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """body: kinds — какие материалы; emails — кому (из списка предпросмотра и/или свои адреса)."""
    meeting = await _meeting(request, db, meeting_id, su)
    delivery = request.app.state.delivery
    if await request.app.state.mail.active(db) is None:
        raise HTTPException(status_code=409, detail="Исходящая почта не настроена. Обратитесь к администратору системы.")
    kinds = [k for k in (body.get("kinds") or []) if k in MATERIALS]
    if not kinds:
        raise HTTPException(status_code=422, detail="Выберите хотя бы один материал.")
    spec = _spec(meeting)
    spec["recipients"]["emails"] = []
    full = {r.email.lower(): r for r in await delivery.resolve(db, meeting.room, meeting, {**spec, "recipients": {**spec["recipients"], "leaders": True, "participants": True}})
            if r.email}
    # получатели по настройкам комнаты, плюс те, кого видно в предпросмотре
    chosen: list[Recipient] = []
    seen: set[str] = set()
    pol = await delivery.policy(db)
    domains = pol.domains()
    for raw in body.get("emails") or []:
        e = str(raw).strip()
        if not valid_email(e):
            raise HTTPException(status_code=422, detail=f"Некорректный адрес: {e[:80]}")
        if e.lower() in seen:
            continue
        seen.add(e.lower())
        known = full.get(e.lower())
        problem = "domain_not_allowed" if domains and e.rsplit("@", 1)[-1].lower() not in domains else None
        chosen.append(Recipient(e, known.name if known else e, known.source if known else "manual", problem))
    if not chosen:
        raise HTTPException(status_code=422, detail="Выберите хотя бы одного получателя.")
    if len(chosen) > 300:
        raise HTTPException(status_code=422, detail="Слишком много получателей (не больше 300).")
    available = [k for k in kinds if await MATERIALS[k].load(delivery, db, meeting) is not None]
    if not available:
        raise HTTPException(status_code=409, detail="Выбранные материалы ещё не сформированы.")
    res = await delivery.enqueue(db, meeting, available, chosen, trigger="manual", by=su.display_name)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.materials.send", target_type="meeting", target_id=str(meeting.id),
                      ip=client_ip(request), details={"room": meeting.room.slug, "kinds": available, "queued": res["queued"], "skipped": len(res["skipped"]),
                                                      "unavailable": [k for k in kinds if k not in available]})
    await db.commit()
    return {**res, "kinds": available, "unavailable": [k for k in kinds if k not in available]}
