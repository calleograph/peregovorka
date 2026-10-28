from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, func, literal_column, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin, require_user
from ..models import (MEETING_LITE, GuestParticipant, Meeting, MeetingChatMessage, MeetingGrant, MeetingParticipant, MeetingWhiteboard, Protocol, Recording, Room,
                      TranscriptSegment, User)
from ..services.access import accessible_history, can_access_meeting, release_lease
from ..services import roles
from ..services.room_icons import icon_out
from ..services.audit import audit_now, write_audit
from ..services.video_rec import VideoError
from ..services.export_docs import md_to_plain, to_docx, to_html, to_pdf
from ..services.doc_render import with_incomplete_notice
from ..services.glass_html import CSP_HEADER
from ..services.llm_choice import once_options
from ..services.protocols import LOCAL_BUILTIN_INSTRUCTION
from ..services.meetings import JoinError
from ..services.settings import SettingsError
from ..services.segments import segment_to_dict
from ..services.reconcile import mark_missing
from ..services.storage import StorageError, StorageNotFound
from .profile import avatar_url
from .schemas import MeetingOut, ParticipantOut, SegmentOut, TranscriptOut

router = APIRouter(prefix="/meetings", tags=["meetings"])

KINDS = ("summary", "protocol")
CONTENT_TYPES = {
    "md": "text/markdown; charset=utf-8", "txt": "text/plain; charset=utf-8", "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf",
}


def meeting_out(m: Meeting, counts: dict | None = None, guests: list[GuestParticipant] | None = None) -> MeetingOut:
    # один человек мог заходить несколько раз — показываем по последнему входу
    latest: dict[uuid.UUID, MeetingParticipant] = {}
    for p in sorted(m.participants, key=lambda p: p.joined_at):
        latest[p.user_id] = p
    c = counts or {}
    leaders = {(mod.subject_ref or "").lower() for mod in m.room.moderators if mod.subject_type == "user"}

    def role_of(p: MeetingParticipant) -> str | None:
        if m.started_by_user_id is not None and p.user_id == m.started_by_user_id:
            return "organizer"
        return "leader" if (p.user.ad_guid or "").lower() in leaders else None
    guests = [ParticipantOut(guest_id=g.id, participant_type="phone" if g.is_phone else "guest", display_name=g.label, joined_at=g.joined_at,
                             left_at=g.left_at, online=g.left_at is None) for g in guests or []]
    return MeetingOut(
        id=m.id, room_id=m.room_id, room_name=m.room.name, room_icon=icon_out(m.room), started_at=m.started_at, ended_at=m.ended_at,
        end_reason=m.end_reason, transcription_enabled=m.transcription_enabled,
        participants=[ParticipantOut(user_id=p.user_id, role=role_of(p), display_name=p.user.display_name, joined_at=p.joined_at,
                                     left_at=p.left_at, online=p.left_at is None) for p in latest.values()] + guests,
        segments=c.get("segments", 0), recordings=c.get("recordings", 0), protocols=c.get("protocols", 0),
        chat_messages=c.get("chat_messages", 0), whiteboard_shapes=c.get("whiteboard_shapes", 0), guests=len(guests),
    )


async def meeting_counts(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    out: dict[uuid.UUID, dict] = {i: {} for i in ids}
    if not ids:
        return out
    parts = []
    for key, model, extra in (("segments", TranscriptSegment, None), ("recordings", Recording, None),
                              ("protocols", Protocol, Protocol.kind.in_(KINDS)), ("chat_messages", MeetingChatMessage, None)):
        stmt = select(literal_column(f"'{key}'").label("k"), model.meeting_id.label("m"), func.count().label("n")).where(model.meeting_id.in_(ids)).group_by(model.meeting_id)
        parts.append(stmt.where(extra) if extra is not None else stmt)
    parts.append(select(literal_column("'whiteboard_shapes'").label("k"), MeetingWhiteboard.meeting_id.label("m"), MeetingWhiteboard.shapes.label("n")).where(MeetingWhiteboard.meeting_id.in_(ids)))
    for key, mid, n in (await db.execute(union_all(*parts))).all():         # один запрос вместо пяти: история открывается часто
        out[mid][key] = n
    return out


async def meeting_guests(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[GuestParticipant]]:
    out: dict[uuid.UUID, list[GuestParticipant]] = {i: [] for i in ids}
    if ids:
        for g in (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id.in_(ids)).order_by(GuestParticipant.joined_at))).scalars():
            out[g.meeting_id].append(g)
    return out


async def get_meeting_for_user(request: Request, db: AsyncSession, meeting_id: uuid.UUID, su: SessionUser, *, participants: bool = True) -> Meeting:
    """Доступ — по правилам services/access.py (знание id/URL доступа не даёт). Иначе 404 без перечисления.
    participants=False — список участников не загружается (обращение к нему — ошибка): для частых запросов."""
    meeting = await db.get(Meeting, meeting_id, options=() if participants else MEETING_LITE)
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
        page = (await accessible_history(db, request.app.state.redis, su, room_id=room_id))[offset:offset + limit]
        found = {m.id: m for m in (await db.execute(select(Meeting).where(Meeting.id.in_(page)))).scalars().unique()} if page else {}
        meetings = [found[i] for i in page if i in found]
    ids = [m.id for m in meetings]
    counts, guests = await meeting_counts(db, ids), await meeting_guests(db, ids)
    return [meeting_out(m, counts.get(m.id), guests.get(m.id)) for m in meetings]


@router.get("/{meeting_id}/avatars")
async def meeting_avatars(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Аватарки участников встречи для плиток в комнате: {identity в LiveKit: адрес с версией}. Только у тех, у кого аватарка есть (у остальных — инициалы).
    Адрес версионный: браузер кэширует картинку надолго, а при смене фото адрес меняется. Доступ — как к самой встрече."""
    await get_meeting_for_user(request, db, meeting_id, su, participants=False)      # вызывает каждый входящий: только люди с аватаркой, а не вся встреча
    users = (await db.execute(select(User).where(User.avatar_mime.is_not(None), User.id.in_(
        select(MeetingParticipant.user_id).where(MeetingParticipant.meeting_id == meeting_id))))).scalars().all()
    return {u.livekit_identity: url for u in users if (url := avatar_url(u))}


@router.get("/{meeting_id}", response_model=MeetingOut)
async def get_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    m = await get_meeting_for_user(request, db, meeting_id, su)
    out = meeting_out(m, (await meeting_counts(db, [m.id])).get(m.id), (await meeting_guests(db, [m.id])).get(m.id))
    out.can_generate = can_edit_protocol(m, su)           # формировать документы и отправлять материалы: администратор, руководитель комнаты, организатор встречи
    out.can_send_materials = out.can_generate
    out.can_manage_access = roles.can_manage_room(m.room, su)
    return out


@router.post("/{meeting_id}/leave", status_code=204)
async def leave_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    m = await get_meeting_for_user(request, db, meeting_id, su, participants=False)      # выходит каждый: список участников не нужен
    await request.app.state.meetings.leave(db, meeting_id, su.user_id, session_id=su.session_id, client_id=request.headers.get("x-client-id", ""))
    await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.leave", target_type="meeting", target_id=str(meeting_id), ip=client_ip(request))
    request.app.state.journal.emit("room", "leave", user=su.sam_account_name, room=m.room.name, meeting_id=str(meeting_id), ip=client_ip(request))


@router.post("/{meeting_id}/release", status_code=204)
async def release_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user)):
    """Пользователь покинул страницу завершённой встречи: временный доступ прекращается (если нет иных оснований)."""
    await release_lease(request.app.state.redis, meeting_id, su.user_id)


@router.post("/{meeting_id}/end", status_code=204)
async def end_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Завершить встречу для всех (кнопка «завершить запись беседы»)."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    # как запись и транскрибация: руководитель комнаты и администратор (в комнате без руководителей, кроме презентационной, — любой участник, как раньше).
    # Раньше завершить встречу для всех мог любой участник, даже слушатель презентационной комнаты.
    if not roles.can_control_meeting(meeting.room, su):
        raise HTTPException(status_code=403, detail="Завершить встречу для всех могут руководители комнаты и администраторы. Вы можете выйти из комнаты.")
    ended = await request.app.state.meetings.end(db, meeting, "manual", kick=True)
    if ended:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.end",
                          target_type="meeting", target_id=str(meeting_id), details={"room": meeting.room.slug})
        await db.commit()


# ------------------------------------------------------------------------------------------ стенограмма
@router.get("/{meeting_id}/transcript", response_model=TranscriptOut)
async def get_transcript(meeting_id: uuid.UUID, request: Request, after_id: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000),
                         su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su, participants=False)
    rows = (await db.execute(select(TranscriptSegment).where(
        TranscriptSegment.meeting_id == meeting_id, TranscriptSegment.id > after_id)
        .order_by(TranscriptSegment.id).limit(limit + 1))).scalars().unique().all()  # постраничность по id; клиент сортирует по времени
    has_more = len(rows) > limit
    return TranscriptOut(meeting_id=meeting_id, has_more=has_more,
                         segments=[SegmentOut(**segment_to_dict(s, mask=meeting.room.mask_profanity)) for s in rows[:limit]])


def _file_response(data: bytes, fmt: str, base: str, inline: bool = False) -> Response:
    # inline — только для HTML: страница открывается в браузере (просмотр и печать), а не скачивается; у документа своя строгая политика безопасности (мета-тег)
    disp = "inline" if (inline and fmt == "html") else "attachment"
    return Response(data, media_type=CONTENT_TYPES[fmt],
                    headers={"Content-Disposition": f'{disp}; filename="{base}.{fmt}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             **({"Content-Security-Policy": CSP_HEADER} if disp == "inline" else {})})


def _render(fmt: str, markdown: str, title: str, kind: str = "protocol") -> bytes:
    if fmt == "md":
        return (markdown.rstrip() + "\n").encode("utf-8")
    if fmt == "txt":
        return md_to_plain(markdown).encode("utf-8")
    if fmt == "html":
        return to_html(markdown, title, kind=kind).encode("utf-8")
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
    await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="transcript.download", target_type="meeting", target_id=str(meeting_id), ip=client_ip(request),
                    details={"format": "txt"})
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="transcript-{str(meeting_id)[:8]}.txt"'})


@router.get("/{meeting_id}/transcript/export")
async def transcript_export(meeting_id: uuid.UUID, request: Request, format: str = Query("docx", pattern="^(txt|md|docx|pdf)$"),
                            su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    ps = request.app.state.protocols
    text = await ps.transcript_text(db, meeting, await ps._tz(db))  # noqa: SLF001
    md = "\n\n".join(line for line in text.splitlines() if line.strip())  # каждая строка стенограммы — отдельный абзац
    await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="transcript.download", target_type="meeting", target_id=str(meeting_id), ip=client_ip(request),
                    details={"format": format})
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


# ------------------------------------------------------------------------------------- видеозапись вкл/выкл
def _video_http(exc: VideoError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.get("/{meeting_id}/video-recording")
async def video_state(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Состояние видеозаписи встречи — видно всем участникам (индикатор «Идёт видеозапись»)."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    st = await request.app.state.video.state(db, meeting)
    st["can_control"] = bool(st["can_start"] and roles.can_control_meeting(meeting.room, su))
    return st


@router.post("/{meeting_id}/video-recording")
async def toggle_video_recording(meeting_id: uuid.UUID, request: Request, body: dict = Body(...),
                                 su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """«Начать видеозапись» / «Остановить видеозапись» — независимо от записи аудио и транскрибации. Руководитель комнаты (или администратор);
    в комнате без руководителей — любой участник. Начинается с текущего момента; остановка и повторный запуск дают новый фрагмент, прежний файл сохраняется."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not roles.can_control_meeting(meeting.room, su):
        raise HTTPException(status_code=403, detail="Видеозаписью управляют руководители комнаты")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="Ожидается enabled: true|false")
    video = request.app.state.video
    try:
        if enabled:
            await video.start(db, meeting, by=su.display_name)
            await _forget_manual_off(request, meeting_id)
        else:
            await video.stop(db, meeting, by=su.display_name, manual=True)
    except VideoError as exc:
        raise _video_http(exc) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.video_recording",
                      target_type="meeting", target_id=str(meeting_id), details={"enabled": enabled, "room": meeting.room.slug})
    await db.commit()
    return {"enabled": enabled}


async def _forget_manual_off(request: Request, meeting_id: uuid.UUID) -> None:
    await request.app.state.redis.delete(f"video:manual_off:{meeting_id}")          # ручной запуск снимает запрет автозапуска


# -------------------------------------------------------------------------------- протоколы и резюме (LLM)
def can_edit_protocol(meeting: Meeting | None, su: SessionUser) -> bool:
    """Править сформированный документ: администратор, руководитель комнаты, организатор встречи (тот, кто её начал)."""
    if meeting is None:
        return False
    return roles.can_manage_room(meeting.room, su) or meeting.started_by_user_id == su.user_id


def _protocol_dict(p: Protocol, with_content: bool = False, can_edit: bool = False, live: dict | None = None, admin: bool = False) -> dict:
    d = {"id": str(p.id), "meeting_id": str(p.meeting_id), "kind": p.kind, "status": p.status, "error": p.error,
         "created_by": p.created_by, "created_at": p.created_at, "updated_at": p.updated_at, "title": p.title,
         "edited_at": p.edited_at, "edited_by": p.edited_by, "model": (p.meta or {}).get("model"), "can_edit": can_edit,
         # предупреждения конвейера (обрезка ответа по лимиту, упрощённая инструкция, длинная стенограмма) — их видно рядом с документом
         "warnings": (p.meta or {}).get("warnings") or [], "truncated": bool((p.meta or {}).get("truncated")),
         # ход и итог: когда задание взято в работу (для таймера в интерфейсе), сколько обработано при остановке по времени, полнота резюме, предел времени
         "progress": (p.meta or {}).get("progress"), "coverage": (p.meta or {}).get("coverage"), "limit_s": (p.meta or {}).get("limit_s"),
         "run_started_at": (p.meta or {}).get("run_started_at"), "live": live, "instruction_builtin": bool((p.meta or {}).get("instruction_builtin")),
         # ссылка на выгруженный файл отдаётся, только пока файл есть (по сверке с хранилищем); сам текст всегда в базе
         "location": (p.meta or {}).get("location") if p.file_state != "missing" else None, "file_state": p.file_state,
         "export_files": (p.meta or {}).get("export_files"),
         "timing": {k: (p.meta or {}).get(k) for k in ("requested_at", "started_at", "llm_started_at", "llm_finished_at", "finished_at", "queue_s", "prepare_s", "llm_s", "total_s")},
         # как создан документ: модель, локальная ли, тип API, сколько частей и повторов, причины остановки; ошибка/обрыв видны в статусе
         "generation": {k: (p.meta or {}).get(k) for k in ("model", "model_title", "llm_local", "api_type", "llm_profile", "llm_source", "llm_once", "chunks", "llm_calls", "retries",
                                                               "length_hits", "finish", "max_tokens", "limit_requested", "limit_applied", "limit_note", "input_chars", "output_chars", "prompt_tokens",
                                                               "completion_tokens", "structured", "failed", "error_code")}}
    # привязка времён документа к стенограмме: секунды суток начала встречи в часовом поясе, в котором записаны метки [ЧЧ:ММ:СС] (переход от пункта к реплике)
    d["time_base"] = ((p.meta or {}).get("time_base") or {}).get("offset_s")
    # техническая диагностика формирования (обращения, повторы по причинам, токены, покрытие) — только администратору; обычному пользователю достаточно состояния и предупреждений
    if admin:
        d["diagnostics"] = (p.meta or {}).get("diag")
    if with_content:
        d["content"], d["instruction"] = p.content, p.instruction
    return d


@router.get("/{meeting_id}/protocols")
async def list_protocols(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    rows = (await db.execute(select(Protocol).where(Protocol.meeting_id == meeting_id, Protocol.kind.in_(KINDS))
                             .order_by(Protocol.created_at.desc()))).scalars().all()
    can = can_edit_protocol(meeting, su)
    ps = request.app.state.protocols
    return [_protocol_dict(p, can_edit=can, admin=su.is_admin, live=await ps.live_progress(p.id) if p.status in ("pending", "running") else None) for p in rows]


@router.get("/{meeting_id}/protocols/default-instruction")
async def default_instruction(meeting_id: uuid.UUID, request: Request, kind: str = Query("protocol", pattern="^(summary|protocol)$"),
                              llm: str = Query("", max_length=80), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Инструкция по умолчанию для окна «Сформировать протокол» (общая + дополнения переговорки), план (какая модель, прогноз времени) и,
    для руководителей, список моделей для разового выбора (`llm` — выбранная модель: прогноз пересчитывается под неё)."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not can_edit_protocol(meeting, su):
        raise HTTPException(status_code=403, detail="Формировать документы и отправлять материалы могут администратор, руководитель комнаты и организатор встречи")
    ps = request.app.state.protocols
    can_override = roles.can_manage_room(meeting.room, su)
    try:
        plan = await ps.plan(db, meeting, kind, once=llm if can_override else None)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    builtin = bool(plan.get("builtin_instruction"))      # локальная модель: показывается встроенная инструкция, пользовательские и комнатные к ней не применяются
    out = {"kind": kind, "instruction": LOCAL_BUILTIN_INSTRUCTION if builtin else await ps.default_instruction(db, meeting, kind), "builtin": builtin,
           "plan": plan, "can_override": can_override}
    if can_override:
        out["llm_choices"] = await once_options(ps.profiles, ps.local_llm, db)
    return out


@router.post("/{meeting_id}/protocols", status_code=202)
async def create_protocol(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...),
                          su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Сформировать протокол/резюме. Инструкцию подтверждает пользователь (окно перед отправкой). Текст сначала
    обезличивается по API, затем уходит в LLM; выполняется в фоне — клиент опрашивает статус."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not can_edit_protocol(meeting, su):
        raise HTTPException(status_code=403, detail="Формировать документы и отправлять материалы могут администратор, руководитель комнаты и организатор встречи")
    kind = body.get("kind", "protocol")
    instruction = body.get("instruction")
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="kind: summary | protocol")
    if instruction is not None and (not isinstance(instruction, str) or len(instruction) > 20000):
        raise HTTPException(status_code=422, detail="instruction: строка до 20000 символов")
    if meeting.ended_at is None:
        raise HTTPException(status_code=409, detail="Протокол формируется после завершения встречи")
    ps = request.app.state.protocols
    llm_once = body.get("llm_once") or None
    if llm_once is not None:
        if not isinstance(llm_once, str) or len(llm_once) > 80:
            raise HTTPException(status_code=422, detail="llm_once: строка local:<модель> или profile:<профиль>")
        if not roles.can_manage_room(meeting.room, su):
            raise HTTPException(status_code=403, detail="Другую модель на один раз выбирают руководители комнаты и администраторы")
    try:
        plan = await ps.plan(db, meeting, kind, once=llm_once)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not plan["llm_ready"]:
        raise HTTPException(status_code=409, detail="Языковая модель (LLM) не настроена администратором")
    if not plan["anonymizer_ready"]:
        raise HTTPException(status_code=409, detail="Для этой переговорки включено обезличивание, но сервис обезличивания не настроен")
    if not await ps.has_materials(meeting_id):
        raise HTTPException(status_code=409, detail="В встрече нет реплик, чата и схемы — формировать протокол не из чего")
    pid = await ps.create_protocol_row(meeting_id, kind, su.display_name, instruction, llm_once)
    ps.start_protocol(pid)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol.create",
                      target_type="meeting", target_id=str(meeting_id), details={"protocol_id": str(pid), "kind": kind, "llm_once": llm_once,
                                                                                    "model": plan.get("llm_model"), "model_source": plan.get("llm_source")})
    await db.commit()
    return {"protocol_id": str(pid), "status": "pending"}


@router.post("/{meeting_id}/protocols/{protocol_id}/cancel", status_code=202)
async def cancel_protocol(meeting_id: uuid.UUID, protocol_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Остановить формирование: идущий запрос к модели отменяется, ресурсы освобождаются. Документ получает статус «cancelled»."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not can_edit_protocol(meeting, su):
        raise HTTPException(status_code=403, detail="Останавливать формирование могут администратор, руководитель комнаты и организатор встречи")
    p = await db.get(Protocol, protocol_id)
    if p is None or p.meeting_id != meeting_id or p.kind not in KINDS:
        raise HTTPException(status_code=404, detail="Протокол не найден")
    if p.status not in ("pending", "running"):
        raise HTTPException(status_code=409, detail="Формирование уже завершено")
    ps = request.app.state.protocols
    live = await ps.cancel(protocol_id)
    if not live:
        await ps.recover_orphans()                       # живой задачи нет (сервер перезапускали): документ закрывается как прерванный
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol.cancel", target_type="meeting", target_id=str(meeting_id),
                      details={"protocol_id": str(protocol_id), "kind": p.kind})
    await db.commit()
    return {"protocol_id": str(protocol_id), "status": "cancelling" if live else "closed"}


async def _get_protocol(request: Request, db: AsyncSession, meeting_id: uuid.UUID, pid: uuid.UUID, su: SessionUser) -> Protocol:
    await get_meeting_for_user(request, db, meeting_id, su)
    p = await db.get(Protocol, pid)
    if p is None or p.meeting_id != meeting_id or p.kind not in KINDS:
        raise HTTPException(status_code=404, detail="Протокол не найден")
    return p


@router.get("/{meeting_id}/protocols/{protocol_id}")
async def get_protocol(meeting_id: uuid.UUID, protocol_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user),
                       db: AsyncSession = Depends(get_db)):
    p = await _get_protocol(request, db, meeting_id, protocol_id, su)
    return _protocol_dict(p, with_content=True, can_edit=can_edit_protocol(await db.get(Meeting, meeting_id), su), admin=su.is_admin, live=await request.app.state.protocols.live_progress(p.id) if p.status in ("pending", "running") else None)


@router.patch("/{meeting_id}/protocols/{protocol_id}")
async def edit_protocol(meeting_id: uuid.UUID, protocol_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...),
                        su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Ручное редактирование результата (исходный Markdown) и заголовка. Сформированный протокол — контролируемый документ: править его могут
    администратор, руководитель комнаты и организатор встречи, но не любой участник (замечания остальных — отдельным механизмом в будущем)."""
    p = await _get_protocol(request, db, meeting_id, protocol_id, su)
    meeting = await db.get(Meeting, meeting_id)
    if not can_edit_protocol(meeting, su):
        raise HTTPException(status_code=403, detail="Править протокол могут администратор, руководитель комнаты и организатор встречи")
    if p.status in ("pending", "running"):
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
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol.edit", target_type="meeting", target_id=str(meeting_id),
                      details={"protocol_id": str(protocol_id), "content_changed": "content" in body, "title_changed": "title" in body})
    await db.commit()
    return _protocol_dict(p, with_content=True, can_edit=True, admin=su.is_admin)


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
                          format: str = Query("docx", pattern="^(txt|md|docx|pdf|html)$"), inline: bool = Query(False),
                          su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    p = await _get_protocol(request, db, meeting_id, protocol_id, su)
    if p.status != "ready" or not p.content:
        raise HTTPException(status_code=409, detail="Протокол ещё не готов")
    meeting = await db.get(Meeting, meeting_id)
    title = p.title or ("Протокол встречи" if p.kind == "protocol" else "Краткое резюме") + f": {meeting.room.name}"
    await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="protocol.export", target_type="protocol", target_id=str(p.id), ip=client_ip(request),
                    details={"format": format, "kind": p.kind, "meeting_id": str(meeting_id)})
    return _file_response(_render(format, with_incomplete_notice(p.content, p.meta), title, p.kind), format, f"{p.kind}-{str(p.id)[:8]}", inline=inline)


# ----------------------------------------------------------------------------------------- записи (admin)
@router.get("/{meeting_id}/recordings")
async def meeting_recordings(meeting_id: uuid.UUID, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Аудиозаписи встречи — только администраторам."""
    rows = (await db.execute(select(Recording).where(Recording.meeting_id == meeting_id, Recording.kind == "participant").order_by(Recording.created_at))).scalars().all()
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
    return Response(data, media_type=rec.mime or "audio/wav", headers={"Content-Disposition": f'attachment; filename="{name}"'})


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
