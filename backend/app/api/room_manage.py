"""Управление комнатой руководителем: «Настройки комнаты» в самой комнате, без доступа в системную админку.

Руководитель (или администратор) может: название и описание, режим (обычная / презентационная), автозапись и запись аудио, права участников
(камера, показ экрана, доска), число мест, пароль, приветствие, «микрофон при входе выключен», гостевой доступ и ссылку, доступ (пользователи и группы
AD), список руководителей. НЕ может: LLM и обезличивание, хранилища, сроки хранения, технический идентификатор, включение/выключение комнаты —
это системные настройки, они остаются у администратора сервера (раздел «Администрирование»).
Права проверяются здесь, на backend; интерфейс лишь показывает кнопку «Настройки комнаты» тем, кому она разрешена.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.directory import DirectoryError
from ..models import Meeting, Room
from ..security.passwords import hash_room_password
from ..services import roles
from ..services.audit import write_audit
from ..services.rooms import acl_allows
from .admin import _acl_rows, _mod_rows, new_guest_token
from .schemas import AclEntryIn, AclEntryOut

router = APIRouter(prefix="/rooms/{room_id}/manage", tags=["room-manage"])

# поля, которые руководитель может менять; всё остальное — только администратор
LEADER_FIELDS = ("name", "description", "max_participants", "camera_allowed", "screen_share_allowed", "board_allowed", "room_type", "auto_record",
                 "record_audio", "mute_on_join", "welcome_message", "guest_access_enabled")
# изменение этих полей отражается на токенах: у уже вошедших участников вступает в силу при следующем входе
TOKEN_FIELDS = {"camera_allowed", "screen_share_allowed", "room_type"}


class RoomManageOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    is_enabled: bool
    max_participants: int
    has_password: bool
    camera_allowed: bool
    screen_share_allowed: bool
    board_allowed: bool
    room_type: str
    auto_record: bool
    record_audio: bool
    transcription_enabled: bool
    mute_on_join: bool
    welcome_message: str | None
    guest_access_enabled: bool
    guest_token: str | None
    acl: list[AclEntryOut]
    moderators: list[AclEntryOut]
    active_meeting_id: uuid.UUID | None = None
    can_edit_system_fields: bool = False


class RoomManagePatch(BaseModel):
    """Частичное обновление. Пароль: строка — задать, пустая — снять, не передано — не менять."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    max_participants: int | None = Field(default=None, ge=1, le=200)
    password: str | None = Field(default=None, max_length=256, repr=False)
    camera_allowed: bool | None = None
    screen_share_allowed: bool | None = None
    board_allowed: bool | None = None
    room_type: str | None = Field(default=None, pattern="^(regular|presentation)$")
    auto_record: bool | None = None
    record_audio: bool | None = None
    mute_on_join: bool | None = None
    welcome_message: str | None = Field(default=None, max_length=2000)
    guest_access_enabled: bool | None = None
    acl: list[AclEntryIn] | None = None
    moderators: list[AclEntryIn] | None = None


async def _room_for_leader(request: Request, db: AsyncSession, room_id: uuid.UUID, su: SessionUser) -> Room:
    room = await db.get(Room, room_id)
    # чужая и несуществующая комнаты неразличимы; обычному участнику — 403 с понятным текстом (он видит комнату, но не управляет ею)
    if room is None or not (su.is_admin or roles.is_room_leader(room, su) or acl_allows(room, su)):
        raise HTTPException(status_code=404, detail="Комната не найдена")
    if not roles.can_manage_room(room, su):
        raise HTTPException(status_code=403, detail="Настройками комнаты управляют её руководители")
    return room


async def _out(db: AsyncSession, room: Room, su: SessionUser) -> RoomManageOut:
    active = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).scalar_one_or_none()
    return RoomManageOut(
        id=room.id, slug=room.slug, name=room.name, description=room.description, is_enabled=room.is_enabled, max_participants=room.max_participants,
        has_password=bool(room.password_hash), camera_allowed=room.camera_allowed, screen_share_allowed=room.screen_share_allowed,
        board_allowed=room.board_allowed, room_type=room.room_type, auto_record=room.auto_record, record_audio=room.record_audio,
        transcription_enabled=room.transcription_enabled, mute_on_join=room.mute_on_join, welcome_message=room.welcome_message,
        guest_access_enabled=room.guest_access_enabled, guest_token=room.guest_token,
        acl=[{"subject_type": a.subject_type, "subject_ref": a.subject_ref, "display_name": a.display_name} for a in room.acl],
        moderators=[{"subject_type": m.subject_type, "subject_ref": m.subject_ref, "display_name": m.display_name} for m in room.moderators],
        active_meeting_id=active, can_edit_system_fields=su.is_admin)


@router.get("", response_model=RoomManageOut)
async def get_manage(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_leader(request, db, room_id, su)
    return await _out(db, room, su)


@router.patch("")
async def patch_manage(room_id: uuid.UUID, body: RoomManagePatch, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_leader(request, db, room_id, su)
    fields = body.model_dump(exclude_unset=True)
    changed: dict = {}
    for name in LEADER_FIELDS:
        if name in fields and getattr(room, name) != fields[name]:
            changed[name] = {"from": getattr(room, name), "to": fields[name]}
            setattr(room, name, fields[name])
    if "password" in fields:
        pw = fields["password"]
        room.password_hash = hash_room_password(pw) if pw else None
        changed["password"] = "set" if pw else "cleared"
    if room.auto_record and not room.record_audio:
        room.record_audio = True    # автоматическая запись предполагает, что запись аудио разрешена
        changed.setdefault("record_audio", {"from": False, "to": True})
    if "guest_access_enabled" in changed:
        if room.guest_access_enabled and not room.guest_token:
            room.guest_token = new_guest_token()
        if not room.guest_access_enabled:
            await db.flush()
            changed["guests_disconnected"] = await request.app.state.meetings.kick_guests(db, room.id)
    if body.moderators is not None:
        rows = _mod_rows(body.moderators)
        if not rows and not su.is_admin:
            raise HTTPException(status_code=422, detail="У комнаты должен остаться хотя бы один руководитель (иначе ею сможет управлять только администратор сервера)")
        room.moderators.clear()
        await db.flush()
        room.moderators.extend(rows)
        changed["moderators"] = [{"type": m.subject_type, "ref": m.subject_ref} for m in room.moderators]
    if body.acl is not None:
        room.acl.clear()
        await db.flush()
        room.acl.extend(_acl_rows(body.acl))
        changed["acl"] = [{"type": a.subject_type, "ref": a.subject_ref} for a in room.acl]
    if changed:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.manage.update", target_type="room",
                          target_id=str(room.id), ip=client_ip(request), details={"room": room.slug, **changed})
    await db.commit()
    await db.refresh(room)
    out = (await _out(db, room, su)).model_dump(mode="json")
    out["needs_rejoin"] = bool(TOKEN_FIELDS & set(changed)) and out["active_meeting_id"] is not None
    return out


@router.post("/guest-link/{action}", response_model=RoomManageOut)
async def guest_link(room_id: uuid.UUID, action: str, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """`rotate` — новая гостевая ссылка (старая перестаёт работать); `revoke` — отозвать ссылку (гостевой доступ выключается). Гости встречи отключаются."""
    if action not in ("rotate", "revoke"):
        raise HTTPException(status_code=404, detail="Неизвестное действие")
    room = await _room_for_leader(request, db, room_id, su)
    if action == "rotate":
        room.guest_token, room.guest_access_enabled = new_guest_token(), True
    else:
        room.guest_token, room.guest_access_enabled = None, False
    await db.flush()
    kicked = await request.app.state.meetings.kick_guests(db, room.id)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=f"room.guest_link.{action}", target_type="room", target_id=str(room.id),
                      ip=client_ip(request), details={"room": room.slug, "guests_disconnected": kicked, "by_leader": not su.is_admin})
    await db.commit()
    await db.refresh(room)
    return await _out(db, room, su)


@router.get("/directory")
async def directory(room_id: uuid.UUID, request: Request, kind: str = Query(pattern="^(group|user)$"), q: str = Query(min_length=2, max_length=100),
                    su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Поиск пользователей и групп AD для списков доступа и руководителей (только для руководителей этой комнаты и администраторов)."""
    await _room_for_leader(request, db, room_id, su)
    try:
        return await asyncio.to_thread(request.app.state.directory.search, kind, q, 20)
    except DirectoryError as exc:
        raise HTTPException(status_code=503, detail=f"Каталог недоступен ({exc.code})") from None
