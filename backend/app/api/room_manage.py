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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.directory import DirectoryError
import re

from ..models import Meeting, Room, SipProfile
from ..services.llm_choice import clean_choice, describe, llm_options, resolve_llm, room_choice
from ..services.sip import normalize_number
from ..security.passwords import hash_room_password
from ..services import roles
from ..services.audit import write_audit
from ..services.mail_delivery import MATERIALS, clean_spec
from ..services.rooms import acl_allows
from .admin import _acl_rows, _mod_rows, new_guest_token
from .schemas import AclEntryIn, AclEntryOut

router = APIRouter(prefix="/rooms/{room_id}/manage", tags=["room-manage"])

# поля, которые руководитель может менять; всё остальное — только администратор
LEADER_FIELDS = ("name", "description", "max_participants", "camera_allowed", "screen_share_allowed", "board_allowed", "board_access", "room_type", "auto_record",
                 "record_audio", "recording_mode", "mute_on_join", "welcome_message", "guest_access_enabled", "protocol_instructions", "auto_map_mode")
# изменение этих полей отражается на токенах: у уже вошедших участников вступает в силу при следующем входе
TOKEN_FIELDS = {"camera_allowed", "screen_share_allowed", "room_type", "board_access"}


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
    board_access: str = "auto"
    board_level: str = "everyone"      # действующий уровень с учётом типа комнаты
    room_type: str
    auto_record: bool
    record_audio: bool
    recording_mode: str = "audio"
    transcription_enabled: bool
    mute_on_join: bool
    welcome_message: str | None
    guest_access_enabled: bool
    auto_map_mode: str = "inherit"       # карта разговора после встречи: inherit — как в системных настройках, on / off
    lifetime: str = "permanent"
    lifecycle: str = "active"
    guest_token: str | None
    acl: list[AclEntryOut]
    moderators: list[AclEntryOut]
    active_meeting_id: uuid.UUID | None = None
    can_edit_system_fields: bool = False
    mail_delivery: dict | None = None      # «Уведомления и доставка материалов» — выбор «что и кому»; SMTP руководителю не показывается
    protocol_instructions: str | None = None
    # Материалы и хранение (только чтение для руководителя — сроки и обезличивание задаёт администратор)
    retention: dict | None = None
    # Языковая модель комнаты: системная по умолчанию → комната → встреча
    llm: dict | None = None                # {mode, profile_id, local_model}
    llm_effective: dict | None = None      # какая модель будет использована сейчас и почему
    llm_summary: dict | None = None        # то же для краткого резюме (отдельная цепочка: система → комната → встреча)
    llm_summary_effective: dict | None = None
    llm_options: dict | None = None
    # Телефония (SIP через LiveKit)
    sip: dict | None = None
    sip_options: dict | None = None


class RoomManagePatch(BaseModel):
    """Частичное обновление. Пароль: строка — задать, пустая — снять, не передано — не менять."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    max_participants: int | None = Field(default=None, ge=1, le=200)
    password: str | None = Field(default=None, max_length=256, repr=False)
    camera_allowed: bool | None = None
    screen_share_allowed: bool | None = None
    board_allowed: bool | None = None
    board_access: str | None = Field(default=None, pattern="^(auto|everyone|speakers|leaders|private)$")
    room_type: str | None = Field(default=None, pattern="^(regular|presentation)$")
    auto_record: bool | None = None
    record_audio: bool | None = None
    recording_mode: str | None = Field(default=None, pattern="^(audio|audio_video|off)$")
    mute_on_join: bool | None = None
    welcome_message: str | None = Field(default=None, max_length=2000)
    guest_access_enabled: bool | None = None
    auto_map_mode: str | None = Field(default=None, pattern="^(inherit|on|off)$")
    acl: list[AclEntryIn] | None = None
    moderators: list[AclEntryIn] | None = None
    mail_delivery: dict | None = None
    protocol_instructions: str | None = Field(default=None, max_length=20000)
    llm: dict | None = None
    llm_summary: dict | None = None
    sip: dict | None = None


async def _room_for_leader(request: Request, db: AsyncSession, room_id: uuid.UUID, su: SessionUser) -> Room:
    room = await db.get(Room, room_id)
    # чужая и несуществующая комнаты неразличимы; обычному участнику — 403 с понятным текстом (он видит комнату, но не управляет ею)
    if room is None or not (su.is_admin or roles.is_room_leader(room, su) or acl_allows(room, su)):
        raise HTTPException(status_code=404, detail="Комната не найдена")
    if not roles.can_manage_room(room, su):
        raise HTTPException(status_code=403, detail="Настройками комнаты управляют её руководители")
    return room


async def _sip_block(request: Request, db: AsyncSession, room: Room) -> tuple[dict, dict]:
    st = request.app.state
    profiles = [p for p in await st.sip.list(db) if p.enabled]
    enabled = str(st.settings.sip_enabled).strip().lower() in ("yes", "true", "1", "on")
    sip = {"mode": room.sip_mode, "profile_id": str(room.sip_profile_id) if room.sip_profile_id else None, "extension": room.sip_extension,
           "allow_inbound": room.sip_allow_inbound, "allow_outbound": room.sip_allow_outbound, "contacts": list(room.sip_contacts or [])}
    opts = {"server_enabled": enabled, "profiles": [{"id": str(p.id), "name": p.name, "direction": p.direction, "is_default": p.is_default, "synced": bool(p.lk_outbound_trunk_id or p.lk_inbound_trunk_id)} for p in profiles],
            "default": next((p.name for p in profiles if p.is_default), None)}
    return sip, opts


async def _out(db: AsyncSession, room: Room, su: SessionUser, request: Request | None = None) -> RoomManageOut:
    active = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).scalar_one_or_none()
    extra: dict = {}
    if request is not None:
        st = request.app.state
        ch = await resolve_llm(st.protocols.profiles, st.local_llm, db, room, None)
        chs = await resolve_llm(st.protocols.profiles, st.local_llm, db, room, None, "summary")
        sip, sip_opts = await _sip_block(request, db, room)
        extra = {"llm": room_choice(room), "llm_effective": describe(ch), "llm_summary": room_choice(room, "summary"), "llm_summary_effective": describe(chs), "llm_options": await llm_options(st.protocols.profiles, st.local_llm, db), "sip": sip, "sip_options": sip_opts}
    return RoomManageOut(
        protocol_instructions=room.protocol_instructions,
        retention={"text_days": room.text_retention_days, "audio_days": room.audio_retention_days, "history_access": room.history_access, "anonymize_mode": room.anonymize_mode},
        **extra,
        id=room.id, slug=room.slug, name=room.name, description=room.description, is_enabled=room.is_enabled, max_participants=room.max_participants,
        has_password=bool(room.password_hash), camera_allowed=room.camera_allowed, screen_share_allowed=room.screen_share_allowed,
        board_allowed=room.board_allowed, board_access=room.board_access, board_level=roles.board_level(room), room_type=room.room_type, auto_record=room.auto_record, record_audio=room.record_audio, recording_mode=room.recording_mode,
        transcription_enabled=room.transcription_enabled, mute_on_join=room.mute_on_join, welcome_message=room.welcome_message,
        guest_access_enabled=room.guest_access_enabled, auto_map_mode=room.auto_map_mode, guest_token=room.guest_token, lifetime=room.lifetime, lifecycle=room.lifecycle,
        acl=[{"subject_type": a.subject_type, "subject_ref": a.subject_ref, "display_name": a.display_name} for a in room.acl],
        moderators=[{"subject_type": m.subject_type, "subject_ref": m.subject_ref, "display_name": m.display_name} for m in room.moderators],
        active_meeting_id=active, can_edit_system_fields=su.is_admin, mail_delivery=_safe_spec(room.mail_delivery))


def _safe_spec(raw: dict | None) -> dict:
    try:
        return clean_spec(raw)
    except ValueError:
        return clean_spec(None)


_EXT = re.compile(r"^[0-9]{2,16}$")


async def _apply_sip(request: Request, db: AsyncSession, room: Room, raw: dict, changed: dict) -> None:
    """Телефония комнаты. Профиль можно выбрать только из включённых; внутренний номер уникален; вход/выход разрешаются независимо."""
    st = request.app.state
    mode = str(raw.get("mode", room.sip_mode))
    if mode not in ("off", "default", "profile"):
        raise HTTPException(status_code=422, detail="Телефония: отключена, профиль по умолчанию или конкретный профиль")
    pid = None
    if mode == "profile":
        try:
            pid = uuid.UUID(str(raw.get("profile_id") or ""))
        except ValueError:
            raise HTTPException(status_code=422, detail="Выберите SIP-профиль") from None
        prof = await db.get(SipProfile, pid)
        if prof is None or not prof.enabled:
            raise HTTPException(status_code=422, detail="Выбранный SIP-профиль не найден или выключен")
    ext = str(raw.get("extension") or "").strip() or None
    if ext and not _EXT.match(ext):
        raise HTTPException(status_code=422, detail="Внутренний номер: от 2 до 16 цифр")
    if ext:
        other = (await db.execute(select(Room.name).where(Room.sip_extension == ext, Room.id != room.id))).scalars().first()
        if other:
            raise HTTPException(status_code=409, detail=f"Внутренний номер {ext} уже занят комнатой «{other}»")
    inbound, outbound = bool(raw.get("allow_inbound", room.sip_allow_inbound)), bool(raw.get("allow_outbound", room.sip_allow_outbound))
    if inbound and not ext:
        raise HTTPException(status_code=422, detail="Для входящих звонков задайте внутренний номер комнаты")
    contacts = []
    for c in (raw.get("contacts") if "contacts" in raw else room.sip_contacts) or []:
        try:
            contacts.append({"name": str((c or {}).get("name") or "")[:80], "number": normalize_number(str((c or {}).get("number") or ""))})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Сохранённый номер: {exc}") from None
    if len(contacts) > 30:
        raise HTTPException(status_code=422, detail="Сохранённых номеров не больше 30")
    before = (room.sip_mode, room.sip_profile_id, room.sip_extension, room.sip_allow_inbound, room.sip_allow_outbound, room.sip_contacts or [])
    room.sip_mode, room.sip_profile_id, room.sip_extension = mode, pid, ext
    room.sip_allow_inbound, room.sip_allow_outbound, room.sip_contacts = inbound, outbound, contacts
    if mode == "off" or not inbound:
        # входящие больше не нужны — правило LiveKit снимается (иначе по номеру комнаты можно было бы дозвониться)
        if room.sip_dispatch_rule_id:
            try:
                await st.sip_gateway.drop_rule(room.sip_dispatch_rule_id)
            finally:
                room.sip_dispatch_rule_id = None
    if before != (room.sip_mode, room.sip_profile_id, room.sip_extension, room.sip_allow_inbound, room.sip_allow_outbound, room.sip_contacts or []):
        changed["sip"] = {"mode": mode, "profile_id": str(pid) if pid else None, "extension": ext, "inbound": inbound, "outbound": outbound, "contacts": len(contacts)}


@router.get("", response_model=RoomManageOut)
async def get_manage(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_leader(request, db, room_id, su)
    return await _out(db, room, su, request)


@router.patch("")
async def patch_manage(room_id: uuid.UUID, body: RoomManagePatch, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_leader(request, db, room_id, su)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("recording_mode") == "audio_video" and room.recording_mode != "audio_video":
        raise HTTPException(409, "Запись видео пока в разработке: выберите «Только аудио» или «Без общей записи».")   # не принимаем режим, который не выполняется
    if room.lifetime == "temporary" and fields.get("guest_access_enabled") and not room.guest_access_enabled:
        await _require_temp_guest_policy(request, db)
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
    if "mail_delivery" in fields:
        try:
            spec = clean_spec(fields["mail_delivery"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if spec != _safe_spec(room.mail_delivery):
            room.mail_delivery = spec
            r = spec["recipients"]
            changed["mail_delivery"] = {"enabled": spec["enabled"], "materials": spec["materials"], "leaders": r["leaders"], "participants": r["participants"],
                                        "users": len(r["users"]), "emails": len(r["emails"])}
    for key, purpose in (("llm", "protocol"), ("llm_summary", "summary")):
        if key not in fields:
            continue
        try:
            ch = clean_choice(fields[key] if fields[key] is not None else {"mode": "inherit"})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        assert ch is not None
        pre = "llm_summary" if purpose == "summary" else "llm"
        cur = (getattr(room, f"{pre}_mode"), getattr(room, f"{pre}_profile_id") and str(getattr(room, f"{pre}_profile_id")), getattr(room, f"{pre}_local_model"))
        if cur != (ch["mode"], ch["profile_id"], ch["local_model"]):
            setattr(room, f"{pre}_mode", ch["mode"])
            setattr(room, f"{pre}_profile_id", uuid.UUID(ch["profile_id"]) if ch["profile_id"] and ch["profile_id"] != "main" else None)
            if ch["profile_id"] == "main":
                setattr(room, f"{pre}_mode", "inherit")        # «основной» профиль — это и есть системная настройка
            setattr(room, f"{pre}_local_model", ch["local_model"])
            changed[key] = ch
    if "sip" in fields and fields["sip"] is not None:
        await _apply_sip(request, db, room, fields["sip"], changed)
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
    out = (await _out(db, room, su, request)).model_dump(mode="json")
    out["needs_rejoin"] = bool(TOKEN_FIELDS & set(changed)) and out["active_meeting_id"] is not None
    return out


async def _require_temp_guest_policy(request: Request, db: AsyncSession) -> None:
    """Гостевую ссылку для временной переговорки выпускают, только если это разрешено в «Общих настройках»."""
    if not (await request.app.state.settings_svc.get(db, "general")).temp_room_allow_guest_link:     # type: ignore[attr-defined]
        raise HTTPException(status_code=403, detail="Гостевая ссылка для временных переговорок отключена администратором")


@router.post("/guest-link/{action}", response_model=RoomManageOut)
async def guest_link(room_id: uuid.UUID, action: str, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """`rotate` — новая гостевая ссылка (старая перестаёт работать); `revoke` — отозвать ссылку (гостевой доступ выключается). Гости встречи отключаются."""
    if action not in ("rotate", "revoke"):
        raise HTTPException(status_code=404, detail="Неизвестное действие")
    room = await _room_for_leader(request, db, room_id, su)
    if action == "rotate":
        if room.lifetime == "temporary":
            await _require_temp_guest_policy(request, db)
        room.guest_token, room.guest_access_enabled = new_guest_token(), True
    else:
        room.guest_token, room.guest_access_enabled = None, False
    await db.flush()
    kicked = await request.app.state.meetings.kick_guests(db, room.id)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=f"room.guest_link.{action}", target_type="room", target_id=str(room.id),
                      ip=client_ip(request), details={"room": room.slug, "guests_disconnected": kicked, "by_leader": not su.is_admin})
    await db.commit()
    await db.refresh(room)
    return await _out(db, room, su, request)


@router.get("/directory")
async def directory(room_id: uuid.UUID, request: Request, kind: str = Query(pattern="^(group|user)$"), q: str = Query(min_length=2, max_length=100),
                    su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Поиск пользователей и групп AD для списков доступа и руководителей (только для руководителей этой комнаты и администраторов)."""
    await _room_for_leader(request, db, room_id, su)
    try:
        return await asyncio.to_thread(request.app.state.directory.search, kind, q, 20)
    except DirectoryError as exc:
        raise HTTPException(status_code=503, detail="Каталог не подключён. Администратор добавляет подключение в разделе «LDAP и доступ»." if exc.code == "not_configured"
                            else f"Каталог недоступен ({exc.code})") from None


@router.post("/delivery-preview")
async def delivery_preview(room_id: uuid.UUID, request: Request, body: dict = Body(default={}), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Кому уйдёт рассылка по текущим (даже ещё не сохранённым) настройкам: адреса из каталога, у кого адреса нет, что запрещено политикой доменов.
    Участники встречи определяются по факту встречи — здесь их состав неизвестен. Реквизиты почтового сервера руководителю не показываются."""
    room = await _room_for_leader(request, db, room_id, su)
    try:
        spec = clean_spec(body.get("mail_delivery") if body.get("mail_delivery") is not None else room.mail_delivery)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    delivery = request.app.state.delivery
    recipients = await delivery.resolve(db, room, None, spec)
    pol = await delivery.policy(db)
    return {"recipients": [r.public() for r in recipients], "participants_by_meeting": spec["recipients"]["participants"],
            "mail_configured": await request.app.state.mail.active(db) is not None, "allowed_domains": pol.domains(),
            "materials": [{"kind": k, "label": d.label, "describe": d.describe} for k, d in MATERIALS.items()]}
