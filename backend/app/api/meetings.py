from __future__ import annotations

import uuid

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, get_db, require_user
from ..models import Meeting, MeetingParticipant, Protocol, Recording, TranscriptSegment
from ..services.meetings import JoinError
from ..services.audit import write_audit
from ..services.segments import segment_to_dict
from .schemas import MeetingOut, ParticipantOut, SegmentOut, TranscriptOut

router = APIRouter(prefix="/meetings", tags=["meetings"])


def meeting_out(m: Meeting) -> MeetingOut:
    # один человек мог заходить несколько раз — показываем по последнему входу
    latest: dict[uuid.UUID, MeetingParticipant] = {}
    for p in sorted(m.participants, key=lambda p: p.joined_at):
        latest[p.user_id] = p
    return MeetingOut(
        id=m.id, room_id=m.room_id, room_name=m.room.name, started_at=m.started_at, ended_at=m.ended_at,
        end_reason=m.end_reason, transcription_enabled=m.transcription_enabled,
        participants=[ParticipantOut(user_id=p.user_id, display_name=p.user.display_name, joined_at=p.joined_at,
                                     left_at=p.left_at, online=p.left_at is None) for p in latest.values()],
    )


async def get_meeting_for_user(db: AsyncSession, meeting_id: uuid.UUID, su: SessionUser) -> Meeting:
    """Встреча доступна администратору и тем, кто в ней участвовал. Иначе — 404 (без перечисления)."""
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if not su.is_admin and not any(p.user_id == su.user_id for p in meeting.participants):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return meeting


@router.get("", response_model=list[MeetingOut])
async def list_meetings(room_id: uuid.UUID | None = None, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                        su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Meeting).order_by(Meeting.started_at.desc()).limit(limit).offset(offset)
    if room_id:
        stmt = stmt.where(Meeting.room_id == room_id)
    if not su.is_admin:
        stmt = stmt.where(Meeting.id.in_(select(MeetingParticipant.meeting_id).where(MeetingParticipant.user_id == su.user_id)))
    return [meeting_out(m) for m in (await db.execute(stmt)).scalars().unique()]


@router.get("/{meeting_id}", response_model=MeetingOut)
async def get_meeting(meeting_id: uuid.UUID, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return meeting_out(await get_meeting_for_user(db, meeting_id, su))


@router.post("/{meeting_id}/leave", status_code=204)
async def leave_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user),
                        db: AsyncSession = Depends(get_db)):
    await get_meeting_for_user(db, meeting_id, su)
    await request.app.state.meetings.leave(db, meeting_id, su.user_id)


@router.post("/{meeting_id}/end", status_code=204)
async def end_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user),
                      db: AsyncSession = Depends(get_db)):
    """Завершить встречу для всех (кнопка «завершить запись беседы»)."""
    meeting = await get_meeting_for_user(db, meeting_id, su)
    ended = await request.app.state.meetings.end(db, meeting, "manual", kick=True)
    if ended:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.end",
                          target_type="meeting", target_id=str(meeting_id), details={"room": meeting.room.slug})
        await db.commit()


@router.get("/{meeting_id}/transcript", response_model=TranscriptOut)
async def get_transcript(meeting_id: uuid.UUID, after_id: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000),
                         su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    await get_meeting_for_user(db, meeting_id, su)
    rows = (await db.execute(select(TranscriptSegment).where(
        TranscriptSegment.meeting_id == meeting_id, TranscriptSegment.id > after_id)
        .order_by(TranscriptSegment.id).limit(limit + 1))).scalars().unique().all()  # постраничность по id; клиент сортирует по времени
    has_more = len(rows) > limit
    return TranscriptOut(meeting_id=meeting_id, has_more=has_more,
                         segments=[SegmentOut(**segment_to_dict(s)) for s in rows[:limit]])


@router.get("/{meeting_id}/transcript.txt", response_class=PlainTextResponse)
async def transcript_txt(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user),
                         db: AsyncSession = Depends(get_db)):
    """Полный протокол по времени и участникам (шапка «Участвовали: …» и реплики)."""
    meeting = await get_meeting_for_user(db, meeting_id, su)
    ps = request.app.state.protocols
    tz = await ps._tz(db)  # noqa: SLF001
    text = await ps.transcript_text(db, meeting, tz)
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="protocol-{str(meeting_id)[:8]}.txt"'})


@router.post("/{meeting_id}/recording")
async def toggle_recording(meeting_id: uuid.UUID, request: Request, body: dict = Body(...),
                           su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Кнопки «начать запись» / «завершить запись» внутри встречи (транскрибация и, если включена в комнате, аудио)."""
    meeting = await get_meeting_for_user(db, meeting_id, su)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="Ожидается enabled: true|false")
    try:
        state = await request.app.state.meetings.set_recording(db, meeting, enabled)
    except JoinError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.recording",
                      target_type="meeting", target_id=str(meeting_id), details={"enabled": state, "room": meeting.room.slug})
    await db.commit()
    return {"enabled": state}


def _protocol_dict(p: Protocol, with_content: bool = False) -> dict:
    d = {"id": str(p.id), "meeting_id": str(p.meeting_id), "kind": p.kind, "status": p.status, "error": p.error,
         "created_by": p.created_by, "created_at": p.created_at, "updated_at": p.updated_at,
         "model": (p.meta or {}).get("model"), "location": (p.meta or {}).get("location")}
    if with_content:
        d["content"] = p.content
    return d


@router.get("/{meeting_id}/protocols")
async def list_protocols(meeting_id: uuid.UUID, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    await get_meeting_for_user(db, meeting_id, su)
    rows = (await db.execute(select(Protocol).where(Protocol.meeting_id == meeting_id, Protocol.kind == "summary")
                             .order_by(Protocol.created_at.desc()))).scalars().all()
    return [_protocol_dict(p) for p in rows]


@router.get("/{meeting_id}/protocols/{protocol_id}")
async def get_protocol(meeting_id: uuid.UUID, protocol_id: uuid.UUID, su: SessionUser = Depends(require_user),
                       db: AsyncSession = Depends(get_db)):
    await get_meeting_for_user(db, meeting_id, su)
    p = await db.get(Protocol, protocol_id)
    if p is None or p.meeting_id != meeting_id:
        raise HTTPException(status_code=404, detail="Протокол не найден")
    return _protocol_dict(p, with_content=True)


@router.post("/{meeting_id}/protocols/summary", status_code=202)
async def create_summary(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user),
                         db: AsyncSession = Depends(get_db)):
    """Краткий протокол (решения, поручения) через LLM; текст сначала обезличивается по API. Выполняется в фоне."""
    meeting = await get_meeting_for_user(db, meeting_id, su)
    if meeting.ended_at is None:
        raise HTTPException(status_code=409, detail="Краткий протокол создаётся после завершения встречи")
    svc = request.app.state.settings_svc
    cfg_an = await svc.get(db, "anonymizer")
    cfg_llm = await svc.get(db, "llm")
    if not cfg_an.enabled or not cfg_llm.enabled:
        raise HTTPException(status_code=409, detail="Обезличивание и LLM не настроены администратором")
    ps = request.app.state.protocols
    pid = await ps.create_summary_row(meeting_id, su.display_name)
    ps.start_summary(pid)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol.summary.create",
                      target_type="meeting", target_id=str(meeting_id), details={"protocol_id": str(pid)})
    await db.commit()
    return {"protocol_id": str(pid), "status": "pending"}


@router.get("/{meeting_id}/recordings")
async def meeting_recordings(meeting_id: uuid.UUID, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Аудиозаписи встречи — только администраторам."""
    if not su.is_admin:
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    rows = (await db.execute(select(Recording).where(Recording.meeting_id == meeting_id).order_by(Recording.created_at))).scalars().all()
    return [{"id": str(r.id), "identity": r.participant_identity, "size_bytes": r.size_bytes, "duration_s": r.duration_s,
             "name": r.path.rsplit("/", 1)[-1]} for r in rows]


@router.get("/{meeting_id}/recordings/{recording_id}")
async def download_recording(meeting_id: uuid.UUID, recording_id: uuid.UUID, request: Request,
                             su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    if not su.is_admin:
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    rec = await db.get(Recording, recording_id)
    if rec is None or rec.meeting_id != meeting_id:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    root = Path(request.app.state.settings.recordings_path).resolve()
    full = (root / rec.path).resolve()
    if root not in full.parents or not full.is_file():
        raise HTTPException(status_code=404, detail="Файл записи не найден")
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="recording.download",
                      target_type="recording", target_id=str(recording_id))
    await db.commit()
    return FileResponse(full, media_type="audio/wav", filename=full.name)
