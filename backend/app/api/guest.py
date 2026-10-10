"""Гостевой доступ: вход в комнату по специальной ссылке без AD.

Ссылка вида /guest/<token>: токен — секрет комнаты (выпускается и отзывается администратором). Гость вводит имя, проходит проверку
оборудования и входит в УЖЕ идущую встречу. Это отдельный тип участника (`participant_type=guest`), а не пользователь AD:
сессия гостя привязана к одной встрече и живёт, пока встреча идёт и ссылка не отозвана; админ-функций у гостя нет.
"""
from __future__ import annotations

import time
import uuid
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import _origin_ok, client_ip, get_db
from ..auth.guests import GUEST_HEADER, Actor, require_actor
from ..models import GuestParticipant, Room
from ..security.passwords import verify_room_password
from ..services.journal import parse_client
from ..services.meetings import JoinError
from .rooms import room_out
from .clientcfg import build_client_config
from .schemas import JoinOut

router = APIRouter(prefix="/guest", tags=["guest"])

JOIN_LIMIT, JOIN_WINDOW = 20, 600          # входов по гостевой ссылке с одного IP за окно
PASSWORD_FAILS, PASSWORD_WINDOW = 8, 600   # неверных паролей комнаты с одного IP за окно


class GuestJoinIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    password: str | None = Field(default=None, max_length=256, repr=False)
    accepted_documents: list[str] = Field(default_factory=list, max_length=8)      # подтверждённые гостем документы (если организация требует подтверждения)


class GuestRoomInfo(BaseModel):
    room_name: str
    description: str | None = None
    meeting_active: bool
    has_password: bool
    camera_allowed: bool


class GuestJoinOut(JoinOut):
    guest_token: str = Field(repr=False)
    guest_id: str
    display_name: str


def clean_display_name(raw: str) -> str:
    """Имя гостя: без управляющих/форматирующих символов (в т.ч. разворота текста), пробелы схлопнуты, без собственной пометки «(гость)»."""
    text = "".join(ch for ch in raw if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))
    text = " ".join(text.split())
    low = text.lower()
    for tail in ("(гость)", "( гость )", "(guest)"):
        if low.endswith(tail):
            text = text[: -len(tail)].strip()
            low = text.lower()
    if len(text) < 2:
        raise HTTPException(status_code=422, detail="Введите имя (не короче двух символов)")
    return text[:60]


async def _room_by_token(db: AsyncSession, token: str) -> Room:
    """Комната по гостевому токену. Любая причина отказа (нет токена, ссылка отозвана, доступ выключен, комната отключена) — один и тот же 404."""
    room = None
    if 10 <= len(token) <= 64:
        room = (await db.execute(select(Room).where(Room.guest_token == token))).scalars().first()
    if room is None or not room.guest_access_enabled or not room.is_enabled or room.lifecycle == "closed":
        raise HTTPException(status_code=404, detail="Гостевая ссылка недействительна или отозвана")
    return room


async def _throttle(request: Request, key: str, limit: int, window: int, *, hit: bool = True) -> None:
    redis = request.app.state.redis
    k = f"{key}:{client_ip(request)}"
    n = await redis.incr(k) if hit else int(await redis.get(k) or 0)
    if hit and n == 1:
        await redis.expire(k, window)
    if n > limit:
        raise HTTPException(status_code=429, detail="Слишком много попыток. Повторите позже.",
                            headers={"Retry-After": str(max(1, await redis.ttl(k)))})


@router.get("/room/{token}", response_model=GuestRoomInfo)
async def room_info(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _throttle(request, "guest:info", 120, 600)
    room = await _room_by_token(db, token)
    active = await request.app.state.meetings._active_meeting(db, room.id)  # noqa: SLF001
    return GuestRoomInfo(room_name=room.name, description=room.description, meeting_active=active is not None,
                         has_password=bool(room.password_hash), camera_allowed=room.camera_allowed)


def _guest_join_out(request: Request, result, room_dto, screen, guest_token: str, display_name: str, asr_ready: bool) -> GuestJoinOut:
    settings = request.app.state.settings
    return GuestJoinOut(
        meeting_id=result.meeting.id, room=room_dto, livekit_url=settings.livekit_public_url, livekit_room=result.meeting.livekit_room,
        token=result.token, identity=result.identity, recording=result.meeting.record_audio, transcription=result.meeting.transcription_enabled,
        asr_ready=asr_ready, client=build_client_config(result.room, screen, result, guest=True),
        guest_token=guest_token, guest_id=str(result.guest.id), display_name=display_name)


async def _asr_ready(request: Request) -> bool:
    try:
        hb = await request.app.state.bridge.heartbeat()
        return bool(hb and hb.get("model_loaded"))
    except Exception:  # noqa: BLE001
        return False


@router.post("/room/{token}/join", response_model=GuestJoinOut)
async def join(token: str, body: GuestJoinIn, request: Request, db: AsyncSession = Depends(get_db)):
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    await _throttle(request, "guest:join", JOIN_LIMIT, JOIN_WINDOW)
    room = await _room_by_token(db, token)
    name = clean_display_name(body.display_name)
    ip, client = client_ip(request), parse_client(request.headers.get("user-agent"))
    journal = request.app.state.journal
    from .site import required_documents  # noqa: PLC0415

    need = await required_documents(db)             # документы организации, подтверждение которых требуется перед входом гостя
    missing = [d for d in need if d.kind not in body.accepted_documents]
    if missing:
        raise HTTPException(status_code=422, detail={"code": "consent_required", "message": "Подтвердите ознакомление с документами организации.",
                                                      "documents": [{"kind": d.kind, "title": d.published_title, "version": d.version} for d in missing]})

    if room.password_hash:  # пароль комнаты действует и для гостей; подбор ограничен по IP
        await _throttle(request, "guest:pwfail", PASSWORD_FAILS, PASSWORD_WINDOW, hit=False)
        if not body.password:
            raise HTTPException(status_code=403, detail={"code": "room_password_required", "message": "Требуется пароль комнаты."})
        if not verify_room_password(room.password_hash, body.password):
            await _throttle(request, "guest:pwfail", PASSWORD_FAILS, PASSWORD_WINDOW)
            journal.emit("room", "guest_join_denied", level="warn", user=f"guest:{name}", room=room.name, ip=ip, client=client,
                         message="Неверный пароль комнаты", data={"code": "room_password_invalid"})
            raise HTTPException(status_code=403, detail={"code": "room_password_invalid", "message": "Неверный пароль комнаты."})

    try:
        result = await request.app.state.meetings.join_guest(db, room, name, ip=ip, client=client)
    except JoinError as exc:
        journal.emit("room", "guest_join_denied", level="warn", user=f"guest:{name}", room=room.name, ip=ip, client=client,
                     message=exc.message, data={"code": exc.code})
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None

    if need:
        from .site import record_consents  # noqa: PLC0415

        await record_consents(db, request, "guest", str(result.guest.id), name, need)
        await db.commit()
    guest_token = await request.app.state.guest_sessions.create(
        guest_id=result.guest.id, meeting_id=result.meeting.id, room_id=room.id, display_name=name)
    screen = await request.app.state.settings_svc.get(db, "screen")
    journal.emit("room", "guest_join", user=f"guest:{name}", room=room.name, meeting_id=str(result.meeting.id), ip=ip, client=client,
                 data={"guest_id": str(result.guest.id)})
    return _guest_join_out(request, result, await room_out(db, room, result.meeting, 0), screen, guest_token, name, await _asr_ready(request))


def _require_guest(actor: Actor) -> Actor:
    if not actor.is_guest:
        raise HTTPException(status_code=403, detail="Только для гостя")
    return actor


@router.post("/session/rejoin", response_model=GuestJoinOut)
async def rejoin(request: Request, actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    """Новый токен LiveKit для гостя с действующей сессией (обновление страницы, обрыв сети) — без повторного ввода имени."""
    _require_guest(actor)
    guest = await db.get(GuestParticipant, actor.id)
    room = await db.get(Room, uuid.UUID(actor.guest.room_id))
    if guest is None or room is None or not room.guest_access_enabled or not room.guest_token:
        raise HTTPException(status_code=401, detail="Гостевая ссылка отозвана")
    try:
        result = await request.app.state.meetings.rejoin_guest(db, guest)
    except JoinError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None
    screen = await request.app.state.settings_svc.get(db, "screen")
    return _guest_join_out(request, result, await room_out(db, room, result.meeting, 0), screen,
                           request.headers.get(GUEST_HEADER, ""), guest.display_name, await _asr_ready(request))


@router.post("/session/leave", status_code=204)
async def leave(request: Request, actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    _require_guest(actor)
    await request.app.state.meetings.leave_guest(db, uuid.UUID(actor.guest.meeting_id), actor.id)
    await request.app.state.guest_sessions.destroy(request.headers.get(GUEST_HEADER))
    request.app.state.journal.emit("room", "guest_leave", user=f"guest:{actor.display_name}", meeting_id=actor.guest.meeting_id, ip=client_ip(request))


@router.get("/session")
async def session(request: Request, actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    _require_guest(actor)
    from ..models import Meeting  # noqa: PLC0415

    meeting = await db.get(Meeting, uuid.UUID(actor.guest.meeting_id))
    return {"guest_id": str(actor.id), "display_name": actor.display_name, "meeting_id": actor.guest.meeting_id,
            "meeting_active": bool(meeting and meeting.ended_at is None), "ts": time.time()}
