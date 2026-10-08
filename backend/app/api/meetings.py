from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import false as sa_false
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin, require_user
from ..models import (GuestParticipant, Meeting, MeetingChatMessage, MeetingGrant, MeetingParticipant, MeetingWhiteboard, Protocol, Recording, Room,
                      TranscriptSegment)
from ..services.access import can_access_meeting, release_lease
from ..services import roles
from ..services.audit import write_audit
from ..services.export_docs import md_to_plain, to_docx, to_pdf
from ..services.meetings import JoinError
from ..services.segments import segment_to_dict
from ..services.reconcile import mark_missing
from ..services.storage import StorageError, StorageNotFound
from .schemas import MeetingOut, ParticipantOut, SegmentOut, TranscriptOut

router = APIRouter(prefix="/meetings", tags=["meetings"])

KINDS = ("summary", "protocol")
CONTENT_TYPES = {
    "md": "text/markdown; charset=utf-8", "txt": "text/plain; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf",
}


def meeting_out(m: Meeting, counts: dict | None = None, guests: list[GuestParticipant] | None = None) -> MeetingOut:
    # один человек мог заходить несколько раз — показываем по последнему входу
    latest: dict[uuid.UUID, MeetingParticipant] = {}
    for p in sorted(m.participants, key=lambda p: p.joined_at):
        latest[p.user_id] = p
    c = counts or {}
    guests = [ParticipantOut(guest_id=g.id, participant_type="phone" if g.is_phone else "guest", display_name=g.label, joined_at=g.joined_at,
                             left_at=g.left_at, online=g.left_at is None) for g in guests or []]
    return MeetingOut(
        id=m.id, room_id=m.room_id, room_name=m.room.name, started_at=m.started_at, ended_at=m.ended_at,
        end_reason=m.end_reason, transcription_enabled=m.transcription_enabled,
        participants=[ParticipantOut(user_id=p.user_id, display_name=p.user.display_name, joined_at=p.joined_at,
                                     left_at=p.left_at, online=p.left_at is None) for p in latest.values()] + guests,
        segments=c.get("segments", 0), recordings=c.get("recordings", 0), protocols=c.get("protocols", 0),
        chat_messages=c.get("chat_messages", 0), whiteboard_shapes=c.get("whiteboard_shapes", 0), guests=len(guests),
    )


async def meeting_counts(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    out: dict[uuid.UUID, dict] = {i: {} for i in ids}
    if not ids:
        return out
    for key, model, extra in (("segments", TranscriptSegment, None), ("recordings", Recording, None),
                              ("protocols", Protocol, Protocol.kind.in_(KINDS)), ("chat_messages", MeetingChatMessage, None)):
        stmt = select(model.meeting_id, func.count()).where(model.meeting_id.in_(ids)).group_by(model.meeting_id)
        if extra is not None:
            stmt = stmt.where(extra)
        for mid, n in (await db.execute(stmt)).all():
            out[mid][key] = n
    for mid, shapes in (await db.execute(select(MeetingWhiteboard.meeting_id, MeetingWhiteboard.shapes).where(MeetingWhiteboard.meeting_id.in_(ids)))).all():
        out[mid]["whiteboard_shapes"] = shapes
    return out


async def meeting_guests(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[GuestParticipant]]:
    out: dict[uuid.UUID, list[GuestParticipant]] = {i: [] for i in ids}
    if ids:
        for g in (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id.in_(ids)).order_by(GuestParticipant.joined_at))).scalars():
            out[g.meeting_id].append(g)
    return out


async def get_meeting_for_user(request: Request, db: AsyncSession, meeting_id: uuid.UUID, su: SessionUser) -> Meeting:
    """Доступ — по правилам services/access.py (знание id/URL доступа не даёт). Иначе 404 без перечисления."""
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or not await can_access_meeting(db, request.app.state.redis, meeting, su):
        raise HTTPException(status_code=404, detail="Встреча не найдена или доступ закрыт")
    return meeting


# ------------------------------------------------------------------------------------- список / карточка
@router.get("", response_model=list[MeetingOut])
async def list_meetings(request: Request, room_id: uuid.UUID | None = None, limit: int = Query(30, ge=1, le=100),
                        offset: int = Query(0, ge=0), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Meeting).order_by(Meeting.started_at.desc())
    if room_id:
        stmt = stmt.where(Meeting.room_id == room_id)
    if su.is_admin:
        meetings = list((await db.execute(stmt.limit(limit).offset(offset))).scalars().unique())
    else:
        mine = select(MeetingParticipant.meeting_id).where(MeetingParticipant.user_id == su.user_id)
        granted = select(MeetingGrant.meeting_id).where(MeetingGrant.user_id == su.user_id)
        # встречи комнат, которыми человек руководит, видны ему в истории даже без участия в них
        led = [r.id for r in (await db.execute(select(Room))).scalars().unique() if roles.is_room_leader(r, su)]
        cond = Meeting.id.in_(mine) | Meeting.id.in_(granted) | (Meeting.room_id.in_(led) if led else sa_false())
        cand = (await db.execute(stmt.where(cond).limit(400))).scalars().unique().all()
        allowed = [m for m in cand if await can_access_meeting(db, request.app.state.redis, m, su)]
        meetings = allowed[offset:offset + limit]
    ids = [m.id for m in meetings]
    counts, guests = await meeting_counts(db, ids), await meeting_guests(db, ids)
    return [meeting_out(m, counts.get(m.id), guests.get(m.id)) for m in meetings]


@router.get("/{meeting_id}", response_model=MeetingOut)
async def get_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    m = await get_meeting_for_user(request, db, meeting_id, su)
    out = meeting_out(m, (await meeting_counts(db, [m.id])).get(m.id), (await meeting_guests(db, [m.id])).get(m.id))
    out.can_send_materials = roles.can_manage_room(m.room, su)
    return out


@router.post("/{meeting_id}/leave", status_code=204)
async def leave_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    m = await get_meeting_for_user(request, db, meeting_id, su)
    await request.app.state.meetings.leave(db, meeting_id, su.user_id)
    request.app.state.journal.emit("room", "leave", user=su.sam_account_name, room=m.room.name, meeting_id=str(meeting_id), ip=client_ip(request))


@router.post("/{meeting_id}/release", status_code=204)
async def release_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user)):
    """Пользователь покинул страницу завершённой встречи: временный доступ прекращается (если нет иных оснований)."""
    await release_lease(request.app.state.redis, meeting_id, su.user_id)


@router.post("/{meeting_id}/end", status_code=204)
async def end_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Завершить встречу для всех (кнопка «завершить запись беседы»)."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    ended = await request.app.state.meetings.end(db, meeting, "manual", kick=True)
    if ended:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.end",
                          target_type="meeting", target_id=str(meeting_id), details={"room": meeting.room.slug})
        await db.commit()


# ------------------------------------------------------------------------------------------ стенограмма
@router.get("/{meeting_id}/transcript", response_model=TranscriptOut)
async def get_transcript(meeting_id: uuid.UUID, request: Request, after_id: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000),
                         su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    await get_meeting_for_user(request, db, meeting_id, su)
    rows = (await db.execute(select(TranscriptSegment).where(
        TranscriptSegment.meeting_id == meeting_id, TranscriptSegment.id > after_id)
        .order_by(TranscriptSegment.id).limit(limit + 1))).scalars().unique().all()  # постраничность по id; клиент сортирует по времени
    has_more = len(rows) > limit
    return TranscriptOut(meeting_id=meeting_id, has_more=has_more,
                         segments=[SegmentOut(**segment_to_dict(s)) for s in rows[:limit]])


def _file_response(data: bytes, fmt: str, base: str) -> Response:
    return Response(data, media_type=CONTENT_TYPES[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{base}.{fmt}"', "Cache-Control": "no-store"})


def _render(fmt: str, markdown: str, title: str) -> bytes:
    if fmt == "md":
        return (markdown.rstrip() + "\n").encode("utf-8")
    if fmt == "txt":
        return md_to_plain(markdown).encode("utf-8")
    try:
        return to_docx(markdown, title) if fmt == "docx" else to_pdf(markdown, title)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("/{meeting_id}/transcript.txt", response_class=PlainTextResponse)
async def transcript_txt(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Полный протокол по времени и участникам (шапка «Участвовали: …» и реплики)."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    ps = request.app.state.protocols
    text = await ps.transcript_text(db, meeting, await ps._tz(db))  # noqa: SLF001
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="transcript-{str(meeting_id)[:8]}.txt"'})


@router.get("/{meeting_id}/transcript/export")
async def transcript_export(meeting_id: uuid.UUID, request: Request, format: str = Query("docx", pattern="^(txt|md|docx|pdf)$"),
                            su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    ps = request.app.state.protocols
    text = await ps.transcript_text(db, meeting, await ps._tz(db))  # noqa: SLF001
    md = "\n\n".join(line for line in text.splitlines() if line.strip())  # каждая строка стенограммы — отдельный абзац
    return _file_response(_render(format, md, f"Стенограмма: {meeting.room.name}"), format, f"transcript-{str(meeting_id)[:8]}")


# ------------------------------------------------------------------------------------- запись вкл/выкл
@router.post("/{meeting_id}/recording")
async def toggle_recording(meeting_id: uuid.UUID, request: Request, body: dict = Body(...),
                           su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """«Начать запись» / «Остановить запись» — запись АУДИО встречи (транскрибация переключается отдельно: /transcription). Руководитель комнаты;
    в комнате без руководителей — любой участник."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not roles.can_control_meeting(meeting.room, su):
        raise HTTPException(status_code=403, detail="Записью управляют руководители комнаты")
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


# -------------------------------------------------------------------------------- протоколы и резюме (LLM)
def _protocol_dict(p: Protocol, with_content: bool = False) -> dict:
    d = {"id": str(p.id), "meeting_id": str(p.meeting_id), "kind": p.kind, "status": p.status, "error": p.error,
         "created_by": p.created_by, "created_at": p.created_at, "updated_at": p.updated_at, "title": p.title,
         "edited_at": p.edited_at, "edited_by": p.edited_by, "model": (p.meta or {}).get("model"),
         # предупреждения конвейера (обрезка ответа по лимиту, упрощённая инструкция, длинная стенограмма) — их видно рядом с документом
         "warnings": (p.meta or {}).get("warnings") or [], "truncated": bool((p.meta or {}).get("truncated")),
         # ссылка на выгруженный файл отдаётся, только пока файл есть (по сверке с хранилищем); сам текст всегда в базе
         "location": (p.meta or {}).get("location") if p.file_state != "missing" else None, "file_state": p.file_state,
         "export_files": (p.meta or {}).get("export_files")}
    if with_content:
        d["content"], d["instruction"] = p.content, p.instruction
    return d


@router.get("/{meeting_id}/protocols")
async def list_protocols(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    await get_meeting_for_user(request, db, meeting_id, su)
    rows = (await db.execute(select(Protocol).where(Protocol.meeting_id == meeting_id, Protocol.kind.in_(KINDS))
                             .order_by(Protocol.created_at.desc()))).scalars().all()
    return [_protocol_dict(p) for p in rows]


@router.get("/{meeting_id}/protocols/default-instruction")
async def default_instruction(meeting_id: uuid.UUID, request: Request, kind: str = Query("protocol", pattern="^(summary|protocol)$"),
                              su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Инструкция по умолчанию для окна «Сформировать протокол» (общая + дополнения переговорки)."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    ps = request.app.state.protocols
    return {"kind": kind, "instruction": await ps.default_instruction(db, meeting, kind), "plan": await ps.plan(db, meeting)}


@router.post("/{meeting_id}/protocols", status_code=202)
async def create_protocol(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...),
                          su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Сформировать протокол/резюме. Инструкцию подтверждает пользователь (окно перед отправкой). Текст сначала
    обезличивается по API, затем уходит в LLM; выполняется в фоне — клиент опрашивает статус."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    kind = body.get("kind", "protocol")
    instruction = body.get("instruction")
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="kind: summary | protocol")
    if instruction is not None and (not isinstance(instruction, str) or len(instruction) > 20000):
        raise HTTPException(status_code=422, detail="instruction: строка до 20000 символов")
    if meeting.ended_at is None:
        raise HTTPException(status_code=409, detail="Протокол формируется после завершения встречи")
    ps = request.app.state.protocols
    plan = await ps.plan(db, meeting)
    if not plan["llm_ready"]:
        raise HTTPException(status_code=409, detail="Языковая модель (LLM) не настроена администратором")
    if not plan["anonymizer_ready"]:
        raise HTTPException(status_code=409, detail="Для этой переговорки включено обезличивание, но сервис обезличивания не настроен")
    if not await ps.has_materials(meeting_id):
        raise HTTPException(status_code=409, detail="В встрече нет реплик, чата и схемы — формировать протокол не из чего")
    pid = await ps.create_protocol_row(meeting_id, kind, su.display_name, instruction)
    ps.start_protocol(pid)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol.create",
                      target_type="meeting", target_id=str(meeting_id), details={"protocol_id": str(pid), "kind": kind})
    await db.commit()
    return {"protocol_id": str(pid), "status": "pending"}


async def _get_protocol(request: Request, db: AsyncSession, meeting_id: uuid.UUID, pid: uuid.UUID, su: SessionUser) -> Protocol:
    await get_meeting_for_user(request, db, meeting_id, su)
    p = await db.get(Protocol, pid)
    if p is None or p.meeting_id != meeting_id or p.kind not in KINDS:
        raise HTTPException(status_code=404, detail="Протокол не найден")
    return p


@router.get("/{meeting_id}/protocols/{protocol_id}")
async def get_protocol(meeting_id: uuid.UUID, protocol_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user),
                       db: AsyncSession = Depends(get_db)):
    return _protocol_dict(await _get_protocol(request, db, meeting_id, protocol_id, su), with_content=True)


@router.patch("/{meeting_id}/protocols/{protocol_id}")
async def edit_protocol(meeting_id: uuid.UUID, protocol_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...),
                        su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Ручное редактирование результата (исходный Markdown) и заголовка."""
    p = await _get_protocol(request, db, meeting_id, protocol_id, su)
    if p.status == "pending":
        raise HTTPException(status_code=409, detail="Протокол ещё формируется")
    from ..models import utcnow

    if "content" in body:
        if not isinstance(body["content"], str) or len(body["content"]) > 400_000:
            raise HTTPException(status_code=422, detail="content: строка до 400000 символов")
        p.content, p.status, p.error = body["content"], "ready", None
    if "title" in body:
        if body["title"] is not None and (not isinstance(body["title"], str) or len(body["title"]) > 300):
            raise HTTPException(status_code=422, detail="title: до 300 символов")
        p.title = body["title"] or None
    p.edited_at, p.edited_by = utcnow(), su.display_name
    await db.commit()
    return _protocol_dict(p, with_content=True)


@router.delete("/{meeting_id}/protocols/{protocol_id}", status_code=204)
async def delete_protocol(meeting_id: uuid.UUID, protocol_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    p = await _get_protocol(request, db, meeting_id, protocol_id, su)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol.delete", target_type="protocol",
                      target_id=str(p.id), ip=client_ip(request), details={"meeting_id": str(meeting_id), "kind": p.kind})
    await db.delete(p)
    await db.commit()


@router.get("/{meeting_id}/protocols/{protocol_id}/export")
async def export_protocol(meeting_id: uuid.UUID, protocol_id: uuid.UUID, request: Request,
                          format: str = Query("docx", pattern="^(txt|md|docx|pdf)$"),
                          su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    p = await _get_protocol(request, db, meeting_id, protocol_id, su)
    if p.status != "ready" or not p.content:
        raise HTTPException(status_code=409, detail="Протокол ещё не готов")
    meeting = await db.get(Meeting, meeting_id)
    title = p.title or ("Протокол совещания" if p.kind == "protocol" else "Краткое резюме") + f": {meeting.room.name}"
    return _file_response(_render(format, p.content, title), format, f"{p.kind}-{str(p.id)[:8]}")


# ----------------------------------------------------------------------------------------- записи (admin)
@router.get("/{meeting_id}/recordings")
async def meeting_recordings(meeting_id: uuid.UUID, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Аудиозаписи встречи — только администраторам."""
    rows = (await db.execute(select(Recording).where(Recording.meeting_id == meeting_id).order_by(Recording.created_at))).scalars().all()
    return [{"id": str(r.id), "identity": r.participant_identity, "size_bytes": r.size_bytes, "duration_s": r.duration_s,
             "name": r.path.rsplit("/", 1)[-1], "export_status": r.export_status, "export_error": r.export_error, "file_state": r.file_state} for r in rows]


@router.get("/{meeting_id}/recordings/{recording_id}")
async def download_recording(meeting_id: uuid.UUID, recording_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin),
                             db: AsyncSession = Depends(get_db)):
    rec = await db.get(Recording, recording_id)
    if rec is None or rec.meeting_id != meeting_id:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if rec.file_state == "missing":
        raise HTTPException(status_code=410, detail={"code": "file_missing", "message": "Файл записи удалён из хранилища (обнаружено при сверке). Скачать его нельзя."})
    try:
        data = await request.app.state.protocols.read_recording(db, rec)
    except StorageNotFound:
        await mark_missing(db, rec, request.app.state.journal, "запись аудио")
        raise HTTPException(status_code=410, detail={"code": "file_missing", "message": "Файл записи не найден в хранилище — вероятно, его удалили вне приложения. Состояние записи обновлено."}) from None
    except StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="recording.download",
                      target_type="recording", target_id=str(recording_id))
    await db.commit()
    name = rec.path.rsplit("/", 1)[-1].encode("ascii", "ignore").decode() or "recording.wav"
    return Response(data, media_type="audio/wav", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/{meeting_id}/recordings", status_code=204)
async def delete_recordings(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Удалить запись»: только аудио встречи; стенограмма и протоколы сохраняются."""
    recs = (await db.execute(select(Recording).where(Recording.meeting_id == meeting_id))).scalars().all()
    for r in recs:
        await request.app.state.protocols.delete_recording(db, r)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="recording.delete", target_type="meeting",
                      target_id=str(meeting_id), ip=client_ip(request), details={"files": len(recs)})
    await db.commit()


@router.delete("/{meeting_id}/recordings/{recording_id}", status_code=204)
async def delete_recording(meeting_id: uuid.UUID, recording_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin),
                           db: AsyncSession = Depends(get_db)):
    rec = await db.get(Recording, recording_id)
    if rec is None or rec.meeting_id != meeting_id:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    await request.app.state.protocols.delete_recording(db, rec)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="recording.delete", target_type="recording",
                      target_id=str(recording_id), ip=client_ip(request), details={"meeting_id": str(meeting_id)})
    await db.commit()


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Удалить встречу со всеми материалами»: реплики, протоколы, аудио, разрешения. Идущую встречу сначала нужно завершить."""
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if meeting.ended_at is None:
        raise HTTPException(status_code=409, detail="Встреча идёт — сначала завершите её")
    recs = (await db.execute(select(Recording).where(Recording.meeting_id == meeting_id))).scalars().all()
    for r in recs:
        await request.app.state.protocols.delete_recording(db, r)
    await db.execute(delete(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id))
    await db.execute(delete(Protocol).where(Protocol.meeting_id == meeting_id))
    await db.execute(delete(MeetingGrant).where(MeetingGrant.meeting_id == meeting_id))
    await request.app.state.chat_files.delete_for_meetings(db, [meeting_id])
    await db.execute(delete(MeetingChatMessage).where(MeetingChatMessage.meeting_id == meeting_id))
    await db.execute(delete(MeetingWhiteboard).where(MeetingWhiteboard.meeting_id == meeting_id))
    await db.execute(delete(GuestParticipant).where(GuestParticipant.meeting_id == meeting_id))
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.delete", target_type="meeting",
                      target_id=str(meeting_id), ip=client_ip(request),
                      details={"room": meeting.room.slug, "started_at": meeting.started_at.isoformat(), "recordings": len(recs)})
    await db.delete(meeting)
    await db.commit()
