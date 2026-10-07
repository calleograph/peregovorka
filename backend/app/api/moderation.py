"""Руководство встречей: выключить микрофон у всех участников или у одного. Доступно администратору сервера и руководителям комнаты."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import Meeting
from ..services.audit import write_audit
from ..services.livekit import mute_microphones, parse_user_identity, user_identity
from ..services.rooms import is_moderator

router = APIRouter(prefix="/meetings", tags=["moderation"])


async def _meeting_for_moderator(request: Request, db: AsyncSession, meeting_id: uuid.UUID, su: SessionUser) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or meeting.ended_at is not None:
        raise HTTPException(status_code=404, detail="Встреча не найдена или уже завершена")
    if not is_moderator(meeting.room, su):
        raise HTTPException(status_code=403, detail="Выключать микрофоны могут администраторы и руководители этой комнаты")
    return meeting


@router.post("/{meeting_id}/moderation/mute-all")
async def mute_all(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Выключить микрофоны у всех участников, кроме самого руководителя."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    done = await mute_microphones(request.app.state.settings, meeting.livekit_room, exclude={user_identity(su.user_id)})
    if done is None:
        raise HTTPException(status_code=503, detail="Сервер звонков недоступен — микрофоны не выключены")
    request.app.state.journal.emit("room", "moderator_mute_all", user=su.sam_account_name, room=meeting.room.name, meeting_id=str(meeting.id),
                                   ip=client_ip(request), message=f"выключено микрофонов: {len(done)}", data={"muted": len(done)})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.mute_all", target_type="meeting",
                      target_id=str(meeting.id), ip=client_ip(request), details={"muted": len(done), "room": meeting.room.slug})
    await db.commit()
    return {"muted": len(done)}


@router.post("/{meeting_id}/moderation/mute")
async def mute_one(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                   db: AsyncSession = Depends(get_db)):
    """Выключить микрофон у одного участника (identity из списка участников комнаты)."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    identity = body.get("identity")
    uid = parse_user_identity(identity) if isinstance(identity, str) else None
    if uid is None:
        raise HTTPException(status_code=422, detail="identity: идентификатор участника")
    done = await mute_microphones(request.app.state.settings, meeting.livekit_room, only={identity})
    if done is None:
        raise HTTPException(status_code=503, detail="Сервер звонков недоступен — микрофон не выключен")
    request.app.state.journal.emit("room", "moderator_mute", user=su.sam_account_name, room=meeting.room.name, meeting_id=str(meeting.id),
                                   ip=client_ip(request), message=f"участник {identity[:12]}: {'выключен' if done else 'микрофон уже был выключен'}",
                                   data={"target": identity, "muted": bool(done)})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.mute", target_type="meeting",
                      target_id=str(meeting.id), ip=client_ip(request), details={"target": identity, "muted": bool(done), "room": meeting.room.slug})
    await db.commit()
    return {"muted": len(done)}
