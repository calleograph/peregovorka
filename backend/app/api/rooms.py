from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, get_db, require_user
from ..models import Meeting, MeetingParticipant, Room
from ..services import timings
from ..services.meetings import JoinError
from ..services.rooms import list_accessible_rooms
from .schemas import ActiveMeetingOut, ClientConfig, JoinIn, JoinOut, RoomOut

router = APIRouter(prefix="/rooms", tags=["rooms"])


async def room_out(db: AsyncSession, room: Room, active: Meeting | None = None, participants: int = 0) -> RoomOut:
    return RoomOut(
        id=room.id, slug=room.slug, name=room.name, description=room.description,
        max_participants=room.max_participants, has_password=bool(room.password_hash),
        transcription_enabled=room.transcription_enabled, record_audio=room.record_audio,
        camera_allowed=room.camera_allowed, screen_share_allowed=room.screen_share_allowed,
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


@router.post("/{room_id}/join", response_model=JoinOut)
async def join_room(room_id: uuid.UUID, body: JoinIn, request: Request,
                    su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    started = time.monotonic()
    try:
        result = await request.app.state.meetings.join(db, room_id, su, body.password)
    except JoinError as exc:
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
    return JoinOut(
        meeting_id=result.meeting.id, room=await room_out(db, result.room, result.meeting, 0),
        livekit_url=settings.livekit_public_url, livekit_room=result.meeting.livekit_room,
        token=result.token, identity=result.identity, recording=result.meeting.transcription_enabled, asr_ready=asr_ready,
        client=ClientConfig(screen_profile=screen.profile, screen_share_audio=screen.share_audio,
                            one_sharer_at_a_time=screen.one_sharer_at_a_time),
    )
