"""Карта разговора встречи: состояние, формирование в фоне, правки тем, учёт выгрузки HTML. Доступ — как к самой встрече (знание id ничего не даёт)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin, require_user
from ..services import roles
from ..services.audit import write_audit
from ..services.conv_map import CATEGORIES, apply_edits, clean_edit
from ..services.llm_choice import resolve_llm
from .meetings import get_meeting_for_user

router = APIRouter(prefix="/meetings/{meeting_id}/map", tags=["maps"])


async def _plan(request: Request, db: AsyncSession, meeting) -> dict:
    """Какая модель будет строить карту и готова ли она."""
    ps = request.app.state.protocols
    ch = await resolve_llm(ps.profiles, ps.local_llm, db, meeting.room, meeting, "map")
    eff, is_local = ps.local_llm.effective(ch.settings)
    lm = ps.local_llm.limits(ch.settings)
    ready, reason = bool(eff.enabled) and ch.available, ch.reason
    if ready and lm is not None:
        if not ps.local_llm.model_enabled(lm):
            ready, reason = False, f"Локальная модель {lm.title} не включена на сервере"
        else:
            import asyncio  # noqa: PLC0415

            fs = await asyncio.to_thread(ps.local_llm.file_state, lm)
            if fs["state"] != "ok":
                ready, reason = False, "Локальная языковая модель не загружена или повреждена"
    return {"ready": ready, "reason": None if ready else (reason or "Языковая модель для карты разговора не настроена"),
            "model": (lm.title if lm else eff.model) if eff.enabled else None, "profile": ch.name, "local": is_local, "source": ch.source}


def _state(rec, meeting, plan: dict, can_edit: bool) -> dict:
    out: dict[str, Any] = {"status": rec.status if rec else "none", "plan": plan, "can_edit": can_edit, "categories": [{"id": i, "label": label} for i, label, _d in CATEGORIES],
                           "finished": meeting.ended_at is not None}
    if rec is not None:
        out.update(meta=rec.meta, error=rec.error, created_by=rec.created_by, updated_at=rec.updated_at.isoformat())
        if rec.status == "ready" and rec.data:
            out["data"] = apply_edits(rec.data, rec.edits)
    return out


@router.get("")
async def get_map(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    maps = request.app.state.maps
    rec = await maps.refresh(db, await maps.get(db, meeting_id))
    return _state(rec, meeting, await _plan(request, db, meeting), roles.can_manage_room(meeting.room, su))


@router.post("", status_code=202)
async def create_map(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Сформировать (или пересоздать) карту. Выполняется в фоне и в очереди: клиент опрашивает состояние."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if meeting.ended_at is None:
        raise HTTPException(status_code=409, detail="Карта разговора формируется после завершения встречи")
    ps = request.app.state.protocols
    if not await ps.has_materials(meeting_id):
        raise HTTPException(status_code=409, detail="В встрече нет реплик — строить карту не из чего")
    plan = await _plan(request, db, meeting)
    if not plan["ready"]:
        raise HTTPException(status_code=409, detail=plan["reason"])
    maps = request.app.state.maps
    cur = await maps.refresh(db, await maps.get(db, meeting_id))
    if cur is not None and cur.status in ("pending", "running"):
        return {"status": cur.status}                    # уже идёт: повторное нажатие ничего не запускает
    again = cur is not None
    rec = await maps.request(db, meeting_id, su.display_name)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="map.recreate" if again else "map.create", target_type="meeting",
                      target_id=str(meeting_id), ip=client_ip(request), details={"model": plan["model"], "model_source": plan["source"]})
    await db.commit()
    maps.start(rec.id)
    return {"status": "pending"}


@router.patch("/topics/{topic_id}")
async def edit_topic(meeting_id: uuid.UUID, topic_id: str, request: Request, body: dict[str, Any] = Body(...),
                     su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Правка темы руководителем комнаты или администратором: название, категория, заметка. Хранится отдельно от результата модели."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not roles.can_manage_room(meeting.room, su):
        raise HTTPException(status_code=403, detail="Править карту могут руководители комнаты и администраторы")
    rec = await request.app.state.maps.get(db, meeting_id)
    if rec is None or rec.status != "ready" or not rec.data or topic_id not in {t["id"] for t in rec.data.get("topics", [])}:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    try:
        patch = clean_edit(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    edits = dict(rec.edits or {})
    cur = {**edits.get(topic_id, {}), **patch}
    cur = {k: v for k, v in cur.items() if v}
    if cur:
        edits[topic_id] = cur
    else:
        edits.pop(topic_id, None)
    rec.edits = edits                                    # новый объект: изменение JSON-колонки замечается
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="map.edit_topic", target_type="meeting", target_id=str(meeting_id),
                      ip=client_ip(request), details={"topic_id": topic_id, "fields": sorted(patch)})
    await db.commit()
    return _state(rec, meeting, await _plan(request, db, meeting), True)


@router.post("/export", status_code=204)
async def log_export(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """HTML-файл собирает браузер из уже полученных данных; здесь только запись в журнал аудита (кто и когда выгрузил карту)."""
    await get_meeting_for_user(request, db, meeting_id, su)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="map.export_html", target_type="meeting", target_id=str(meeting_id),
                      ip=client_ip(request))
    await db.commit()
    return Response(status_code=204)


@router.delete("", status_code=204)
async def delete_map(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rec = await request.app.state.maps.get(db, meeting_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Карты нет")
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="map.delete", target_type="meeting", target_id=str(meeting_id), ip=client_ip(request))
    await db.delete(rec)
    await db.commit()
    return Response(status_code=204)
