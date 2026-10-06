from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import AuditLog, Meeting, Room, RoomAcl
from ..security.passwords import hash_room_password
from ..services.audit import write_audit
from .schemas import AclEntryIn, RoomAdminOut, RoomCreateIn, RoomPatchIn

router = APIRouter(prefix="/admin", tags=["admin"])

_PATCHABLE = ("name", "description", "is_enabled", "max_participants", "transcription_enabled", "record_audio",
              "camera_allowed", "screen_share_allowed", "text_retention_days", "audio_retention_days", "protocol_instructions")


def _acl_rows(entries: list[AclEntryIn]) -> list[RoomAcl]:
    seen: set[tuple[str, str]] = set()
    rows = []
    for e in entries:
        ref = e.subject_ref.strip().lower()  # DN групп и GUID сравниваются без учёта регистра
        key = (e.subject_type, ref)
        if key in seen:
            continue
        seen.add(key)
        rows.append(RoomAcl(subject_type=e.subject_type, subject_ref=ref, display_name=e.display_name))
    return rows


async def _out(db: AsyncSession, room: Room) -> RoomAdminOut:
    active = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).scalar_one_or_none()
    return RoomAdminOut(
        id=room.id, slug=room.slug, name=room.name, description=room.description, is_enabled=room.is_enabled,
        max_participants=room.max_participants, has_password=bool(room.password_hash),
        transcription_enabled=room.transcription_enabled, record_audio=room.record_audio,
        camera_allowed=room.camera_allowed, screen_share_allowed=room.screen_share_allowed,
        text_retention_days=room.text_retention_days, audio_retention_days=room.audio_retention_days,
        protocol_instructions=room.protocol_instructions,
        acl=[{"subject_type": a.subject_type, "subject_ref": a.subject_ref, "display_name": a.display_name} for a in room.acl],
        active_meeting_id=active,
    )


@router.get("/rooms", response_model=list[RoomAdminOut])
async def list_rooms(su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rooms = (await db.execute(select(Room).order_by(Room.name))).scalars().all()
    return [await _out(db, r) for r in rooms]


@router.post("/rooms", response_model=RoomAdminOut, status_code=201)
async def create_room(body: RoomCreateIn, request: Request, su: SessionUser = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    settings = request.app.state.settings
    room = Room(
        slug=body.slug, name=body.name, description=body.description, is_enabled=body.is_enabled,
        max_participants=body.max_participants,
        password_hash=hash_room_password(body.password) if body.password else None,
        transcription_enabled=body.transcription_enabled, record_audio=body.record_audio,
        camera_allowed=body.camera_allowed, screen_share_allowed=body.screen_share_allowed,
        text_retention_days=body.text_retention_days if "text_retention_days" in body.model_fields_set else settings.default_text_retention_days,
        audio_retention_days=body.audio_retention_days if "audio_retention_days" in body.model_fields_set else settings.default_audio_retention_days,
        protocol_instructions=body.protocol_instructions,
    )
    room.acl = _acl_rows(body.acl)
    db.add(room)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Комната с таким техническим идентификатором уже существует") from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.create", target_type="room",
                      target_id=str(room.id), ip=client_ip(request),
                      details={"slug": room.slug, "name": room.name, "has_password": bool(body.password), "acl": len(room.acl)})
    await db.commit()
    await db.refresh(room)
    return await _out(db, room)


@router.get("/rooms/{room_id}", response_model=RoomAdminOut)
async def get_room(room_id: uuid.UUID, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Комната не найдена")
    return await _out(db, room)


@router.patch("/rooms/{room_id}", response_model=RoomAdminOut)
async def patch_room(room_id: uuid.UUID, body: RoomPatchIn, request: Request, su: SessionUser = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Комната не найдена")
    fields = body.model_dump(exclude_unset=True)
    changed: dict = {}
    for name in _PATCHABLE:
        if name in fields and getattr(room, name) != fields[name]:
            changed[name] = {"from": getattr(room, name), "to": fields[name]}
            setattr(room, name, fields[name])
    if "password" in fields:
        pw = fields["password"]
        room.password_hash = hash_room_password(pw) if pw else None
        changed["password"] = "set" if pw else "cleared"  # значение пароля в аудит не попадает
    if body.acl is not None:
        room.acl.clear()
        await db.flush()
        room.acl.extend(_acl_rows(body.acl))
        changed["acl"] = [{"type": a.subject_type, "ref": a.subject_ref} for a in room.acl]
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.update", target_type="room",
                      target_id=str(room.id), ip=client_ip(request), details=changed)
    await db.commit()
    await db.refresh(room)
    return await _out(db, room)


@router.delete("/rooms/{room_id}", status_code=204)
async def delete_room(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Комната не найдена")
    active = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).first()
    if active:
        raise HTTPException(status_code=409, detail="В комнате идёт встреча — сначала завершите её")
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.delete", target_type="room",
                      target_id=str(room.id), ip=client_ip(request), details={"slug": room.slug, "name": room.name})
    await db.delete(room)
    await db.commit()


@router.get("/audit")
async def audit_log(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                    su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit).offset(offset))).scalars().all()
    return [{"id": r.id, "at": r.at, "actor": r.actor_name, "action": r.action, "target_type": r.target_type,
             "target_id": r.target_id, "details": r.details, "request_id": r.request_id, "ip": r.ip} for r in rows]
