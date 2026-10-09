from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import Meeting, MeetingParticipant, Room
from ..services import temp_rooms, timings
from ..services.audit import write_audit
from ..services.journal import parse_client
from ..services.meetings import JoinError
from ..services.rooms import acl_allows, find_by_ref, list_accessible_rooms
from .clientcfg import build_client_config
from .schemas import ActiveMeetingOut, JoinIn, JoinOut, RoomOut

router = APIRouter(prefix="/rooms", tags=["rooms"])


async def room_out(db: AsyncSession, room: Room, active: Meeting | None = None, participants: int = 0) -> RoomOut:
    return RoomOut(
        id=room.id, slug=room.slug, name=room.name, description=room.description,
        max_participants=room.max_participants, has_password=bool(room.password_hash),
        transcription_enabled=room.transcription_enabled, record_audio=room.record_audio,
        camera_allowed=room.camera_allowed, screen_share_allowed=room.screen_share_allowed,
        board_allowed=room.board_allowed, board_access=room.board_access, room_type=room.room_type, auto_record=room.auto_record,
        lifetime=room.lifetime, lifecycle=room.lifecycle, auto_close_at=room.auto_close_at, created_by_name=room.created_by_name,
        active_meeting=ActiveMeetingOut(id=active.id, started_at=active.started_at, participants=participants) if active else None,
    )


@router.get("", response_model=list[RoomOut])
async def list_rooms(su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    rooms = await list_accessible_rooms(db, su)
    if not rooms:
        return []
    active = {m.room_id: m for m in (await db.execute(
        select(Meeting).where(Meeting.room_id.in_([r.id for r in rooms]), Meeting.ended_at.is_(None)))).scalars()}
    counts: dict[uuid.UUID, int] = {}
    if active:
        rows = await db.execute(
            select(MeetingParticipant.meeting_id, func.count(func.distinct(MeetingParticipant.user_id)))
            .where(MeetingParticipant.meeting_id.in_([m.id for m in active.values()]), MeetingParticipant.left_at.is_(None))
            .group_by(MeetingParticipant.meeting_id))
        counts = {mid: n for mid, n in rows}
    return [await room_out(db, r, active.get(r.id), counts.get(active[r.id].id, 0) if r.id in active else 0) for r in rooms]


@router.get("/resolve/{ref}")
async def resolve_room(ref: str, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Комната по ссылке из адресной строки: технический идентификатор (адрес), прежний адрес или UUID из старых ссылок. `canonical=false` —
    клиент заменяет адрес в строке браузера на нынешний. Недоступная и несуществующая комната неразличимы."""
    room, canonical = await find_by_ref(db, ref)
    if room is None or not room.is_enabled or not acl_allows(room, su):
        raise HTTPException(status_code=404, detail="Комната не найдена или недоступна")
    active = (await db.execute(select(Meeting).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).scalars().first()
    people = 0
    if active is not None:
        people = (await db.execute(select(func.count(func.distinct(MeetingParticipant.user_id))).where(
            MeetingParticipant.meeting_id == active.id, MeetingParticipant.left_at.is_(None)))).scalar_one()
    info = (await room_out(db, room, active, people)).model_dump(mode="json")      # для страницы «перед входом»: описание, возможности, кто уже в комнате
    return {"id": str(room.id), "slug": room.slug, "name": room.name, "canonical": canonical, "lifetime": room.lifetime, "lifecycle": room.lifecycle, "room": info}


@router.get("/temporary/policy")
async def temporary_policy(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Можно ли пользователю создать временную переговорку и сколько у него осталось (для кнопки в списке комнат)."""
    cfg = await request.app.state.settings_svc.get(db, "general")
    return await temp_rooms.policy(db, cfg, su)


@router.post("/temporary", response_model=RoomOut, status_code=201)
async def create_temporary_room(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """«Создать временную переговорку»: только обычный авторизованный пользователь (гость по ссылке сюда не попадает — у него нет сессии).
    Создатель становится руководителем; остальные параметры — значения по умолчанию. Комната живёт, пока идёт встреча, и закрывается сама."""
    cfg = await request.app.state.settings_svc.get(db, "general")
    try:
        room = await temp_rooms.create(db, su, str(body.get("name") or ""), cfg, default_text_days=request.app.state.settings.default_text_retention_days,
                                       default_audio_days=request.app.state.settings.default_audio_retention_days)
    except temp_rooms.TempRoomError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.create_temporary", target_type="room",
                      target_id=str(room.id), ip=client_ip(request), details={"slug": room.slug, "name": room.name})
    await db.commit()
    request.app.state.journal.emit("room", "temporary_created", user=su.sam_account_name, room=room.name, ip=client_ip(request),
                                   client=parse_client(request.headers.get("user-agent")), data={"slug": room.slug})
    return await room_out(db, room)


@router.post("/{room_id}/join", response_model=JoinOut)
async def join_room(room_id: uuid.UUID, body: JoinIn, request: Request,
                    su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    started = time.monotonic()
    try:
        result = await request.app.state.meetings.join(db, room_id, su, body.password)
    except JoinError as exc:
        request.app.state.journal.emit("room", "join_denied", level="warn", user=su.sam_account_name, ip=client_ip(request),
                                       client=parse_client(request.headers.get("user-agent")), message=exc.message, data={"code": exc.code})
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}, headers=headers) from None
    settings = request.app.state.settings
    screen = await request.app.state.settings_svc.get(db, "screen")
    asr_ready = False
    try:  # готовность ASR только сообщается клиенту; вход в комнату её не ждёт
        hb = await request.app.state.bridge.heartbeat()
        asr_ready = bool(hb and hb.get("model_loaded"))
        await timings.record(request.app.state.redis, "join_backend_ms", (time.monotonic() - started) * 1000)
    except Exception:  # noqa: BLE001
        pass
    request.app.state.journal.emit("room", "join", user=su.sam_account_name, room=result.room.name, meeting_id=str(result.meeting.id),
                                   ip=client_ip(request), client=parse_client(request.headers.get("user-agent")),
                                   data={"api_ms": int((time.monotonic() - started) * 1000), "asr_ready": asr_ready})
    return JoinOut(
        meeting_id=result.meeting.id, room=await room_out(db, result.room, result.meeting, 0),
        livekit_url=settings.livekit_public_url, livekit_room=result.meeting.livekit_room,
        token=result.token, identity=result.identity, recording=result.meeting.record_audio, transcription=result.meeting.transcription_enabled,
        asr_ready=asr_ready, client=build_client_config(result.room, screen, result, su=su),
    )
