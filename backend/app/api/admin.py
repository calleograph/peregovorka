from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import AuditLog, Meeting, Room, RoomAcl, RoomModerator
from ..security.passwords import hash_room_password
from ..services.audit import write_audit
from ..services.rooms import slug_taken
from .schemas import AclEntryIn, RoomAdminOut, RoomCreateIn, RoomPatchIn

router = APIRouter(prefix="/admin", tags=["admin"])

_PATCHABLE = ("name", "description", "is_enabled", "max_participants", "transcription_enabled", "record_audio",
              "camera_allowed", "screen_share_allowed", "text_retention_days", "audio_retention_days", "protocol_instructions", "history_access",
              "anonymize_mode", "llm_profile_id", "anonymizer_profile_id", "mute_on_join", "welcome_message",
              "guest_access_enabled", "room_type", "auto_record", "board_allowed", "board_access")


def new_guest_token() -> str:
    return secrets.token_urlsafe(24)


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


def _mod_rows(entries: list[AclEntryIn]) -> list[RoomModerator]:
    seen: set[tuple[str, str]] = set()
    rows = []
    for e in entries:
        ref = e.subject_ref.strip().lower()
        if (e.subject_type, ref) in seen:
            continue
        seen.add((e.subject_type, ref))
        rows.append(RoomModerator(subject_type=e.subject_type, subject_ref=ref, display_name=e.display_name))
    return rows


async def _out(db: AsyncSession, room: Room) -> RoomAdminOut:
    active = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).scalar_one_or_none()
    return RoomAdminOut(
        id=room.id, slug=room.slug, name=room.name, description=room.description, is_enabled=room.is_enabled,
        max_participants=room.max_participants, has_password=bool(room.password_hash),
        transcription_enabled=room.transcription_enabled, record_audio=room.record_audio,
        camera_allowed=room.camera_allowed, screen_share_allowed=room.screen_share_allowed,
        text_retention_days=room.text_retention_days, audio_retention_days=room.audio_retention_days,
        protocol_instructions=room.protocol_instructions, history_access=room.history_access,
        anonymize_mode=room.anonymize_mode, llm_profile_id=room.llm_profile_id, anonymizer_profile_id=room.anonymizer_profile_id,
        mute_on_join=room.mute_on_join, welcome_message=room.welcome_message,
        guest_access_enabled=room.guest_access_enabled, guest_token=room.guest_token,
        room_type=room.room_type, auto_record=room.auto_record, board_allowed=room.board_allowed, board_access=room.board_access,
        slug_history=list(room.slug_history or []), lifetime=room.lifetime, lifecycle=room.lifecycle, closed_at=room.closed_at, created_by_name=room.created_by_name,
        moderators=[{"subject_type": m.subject_type, "subject_ref": m.subject_ref, "display_name": m.display_name} for m in room.moderators],
        acl=[{"subject_type": a.subject_type, "subject_ref": a.subject_ref, "display_name": a.display_name} for a in room.acl],
        active_meeting_id=active,
    )


@router.get("/rooms", response_model=list[RoomAdminOut])
async def list_rooms(include_closed: bool = False, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Список комнат. Закрытые временные (их сотни за год; материалы остаются в «Истории») показываются только по `include_closed`."""
    stmt = select(Room).order_by(Room.name)
    if not include_closed:
        stmt = stmt.where(Room.lifecycle != "closed")
    rooms = (await db.execute(stmt)).scalars().all()
    return [await _out(db, r) for r in rooms]


@router.post("/rooms", response_model=RoomAdminOut, status_code=201)
async def create_room(body: RoomCreateIn, request: Request, su: SessionUser = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    settings = request.app.state.settings
    if await slug_taken(db, body.slug):
        raise HTTPException(status_code=409, detail="Адрес комнаты уже занят (текущим или прежним адресом другой комнаты)")
    room = Room(
        slug=body.slug, name=body.name, description=body.description, is_enabled=body.is_enabled,
        max_participants=body.max_participants,
        password_hash=hash_room_password(body.password) if body.password else None,
        transcription_enabled=body.transcription_enabled, record_audio=body.record_audio,
        camera_allowed=body.camera_allowed, screen_share_allowed=body.screen_share_allowed,
        text_retention_days=body.text_retention_days if "text_retention_days" in body.model_fields_set else settings.default_text_retention_days,
        audio_retention_days=body.audio_retention_days if "audio_retention_days" in body.model_fields_set else settings.default_audio_retention_days,
        protocol_instructions=body.protocol_instructions, history_access=body.history_access,
        anonymize_mode=body.anonymize_mode, llm_profile_id=body.llm_profile_id, anonymizer_profile_id=body.anonymizer_profile_id,
        mute_on_join=body.mute_on_join, welcome_message=body.welcome_message,
        guest_access_enabled=body.guest_access_enabled, guest_token=new_guest_token() if body.guest_access_enabled else None,
        room_type=body.room_type, auto_record=body.auto_record, board_allowed=body.board_allowed, board_access=body.board_access,
    )
    if room.auto_record:
        room.record_audio = True  # автоматическая запись предполагает, что запись аудио разрешена
    room.acl = _acl_rows(body.acl)
    room.moderators = _mod_rows(body.moderators)
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
    new_slug = fields.pop("slug", None)
    if new_slug and new_slug != room.slug:
        if await slug_taken(db, new_slug, except_id=room.id):
            raise HTTPException(status_code=409, detail="Адрес комнаты уже занят (текущим или прежним адресом другой комнаты)")
        # прежний адрес сохраняется: ссылка со старым адресом откроет комнату по новому. Гостевая ссылка от адреса не зависит.
        room.slug_history = [*([s for s in (room.slug_history or []) if s != new_slug]), room.slug][-50:]
        changed["slug"] = {"from": room.slug, "to": new_slug}
        room.slug = new_slug
    for name in _PATCHABLE:
        if name in fields and getattr(room, name) != fields[name]:
            changed[name] = {"from": str(getattr(room, name)) if isinstance(getattr(room, name), uuid.UUID) else getattr(room, name),
                             "to": str(fields[name]) if isinstance(fields[name], uuid.UUID) else fields[name]}
            setattr(room, name, fields[name])
    if room.auto_record and not room.record_audio:
        room.record_audio = True   # автоматическая запись предполагает, что запись аудио разрешена
        changed["record_audio"] = {"from": False, "to": True}
    if "guest_access_enabled" in changed:
        if room.guest_access_enabled and not room.guest_token:
            room.guest_token = new_guest_token()  # ссылка выпускается при первом включении; повторное включение возвращает прежнюю
        if not room.guest_access_enabled:
            await db.flush()
            changed["guests_disconnected"] = await request.app.state.meetings.kick_guests(db, room.id)
    if "password" in fields:
        pw = fields["password"]
        room.password_hash = hash_room_password(pw) if pw else None
        changed["password"] = "set" if pw else "cleared"  # значение пароля в аудит не попадает
    if body.acl is not None:
        room.acl.clear()
        await db.flush()
        room.acl.extend(_acl_rows(body.acl))
        changed["acl"] = [{"type": a.subject_type, "ref": a.subject_ref} for a in room.acl]
    if body.moderators is not None:
        room.moderators.clear()
        await db.flush()
        room.moderators.extend(_mod_rows(body.moderators))
        changed["moderators"] = [{"type": m.subject_type, "ref": m.subject_ref} for m in room.moderators]
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.update", target_type="room",
                      target_id=str(room.id), ip=client_ip(request), details=changed)
    await db.commit()
    await db.refresh(room)
    return await _out(db, room)


@router.post("/rooms/{room_id}/guest-link/{action}", response_model=RoomAdminOut)
async def guest_link_action(room_id: uuid.UUID, action: str, request: Request, su: SessionUser = Depends(require_admin),
                            db: AsyncSession = Depends(get_db)):
    """`rotate` — выпустить новую гостевую ссылку (старая перестаёт работать); `revoke` — отозвать ссылку совсем (гостевой доступ выключается).
    В обоих случаях гости идущей встречи отключаются, сотрудники остаются."""
    if action not in ("rotate", "revoke"):
        raise HTTPException(status_code=404, detail="Неизвестное действие")
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Комната не найдена")
    if action == "rotate":
        room.guest_token, room.guest_access_enabled = new_guest_token(), True
    else:
        room.guest_token, room.guest_access_enabled = None, False
    await db.flush()
    kicked = await request.app.state.meetings.kick_guests(db, room.id)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=f"room.guest_link.{action}", target_type="room",
                      target_id=str(room.id), ip=client_ip(request), details={"room": room.slug, "guests_disconnected": kicked})
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
async def audit_log(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), q: str = Query("", max_length=100),
                    actor: str = Query("", max_length=100), action: str = Query("", max_length=80),
                    su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Журнал аудита: от новых к старым; фильтры по исполнителю, действию и тексту (в имени, действии, объекте, IP)."""
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit).offset(offset)

    def like(col, v):
        esc = v.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return func.lower(col).like(f"%{esc}%", escape="\\")
    if actor.strip():
        stmt = stmt.where(like(AuditLog.actor_name, actor.strip()))
    if action.strip():
        stmt = stmt.where(like(AuditLog.action, action.strip()))
    if q.strip():
        stmt = stmt.where(or_(like(AuditLog.actor_name, q.strip()), like(AuditLog.action, q.strip()), like(AuditLog.target_id, q.strip()),
                              like(func.coalesce(AuditLog.ip, ""), q.strip())))
    rows = (await db.execute(stmt)).scalars().all()
    return [{"id": r.id, "at": r.at, "actor": r.actor_name, "action": r.action, "target_type": r.target_type,
             "target_id": r.target_id, "details": r.details, "request_id": r.request_id, "ip": r.ip} for r in rows]
