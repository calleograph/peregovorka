"""Публичный API v1 (`/api/public/v1`): чтение комнат, встреч, стенограмм, документов; завершение встречи. Авторизация — ключ сервисной учётной записи.

Правила: каждый объект проверяется на принадлежность области комнат ключа (чужой объект = 404, не 403: существование не раскрывается);
ошибки — единый формат; списки — курсоры; идентификаторы — типизированные (`mtg_…`); персональные данные — только необходимое (без e-mail и телефонов).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import client_ip, get_db
from ..models import (ConversationMap, GuestParticipant, Meeting, MeetingChatMessage, MeetingParticipant, Protocol, Recording, Room, TranscriptSegment, User)
from ..services.audit import write_audit
from ..services.export_docs import md_to_plain, to_html
from ..services.protocols import format_clock
from ..services.segments import author_name
from . import cursor, ids, render
from .auth import Access, Principal, api_config
from .errors import ApiError, PublicRoute, not_found

router = APIRouter(prefix="/api/public/v1", route_class=PublicRoute)

MAX_EXPORT_SEGMENTS = 20000


# ------------------------------------------------------------------------------------------------ схемы ответов
class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorOut(BaseModel):
    error: ErrorBody


class Page(BaseModel):
    next_cursor: str | None = Field(None, description="Курсор следующей страницы; null — страниц больше нет.")


class MeOut(BaseModel):
    client: str
    key_id: str
    scopes: list[str]
    rooms: list[str] | None = Field(None, description="Идентификаторы комнат, доступных ключу; null — все комнаты.")
    key_expires_at: datetime | None = None


class RoomRef(BaseModel):
    id: str
    slug: str
    name: str


class RoomOut(RoomRef):
    description: str | None = None
    type: Literal["regular", "presentation"] | str
    lifetime: Literal["permanent", "temporary"] | str
    enabled: bool
    active_meeting_id: str | None = None


class RoomList(Page):
    items: list[RoomOut]


class MeetingOut(BaseModel):
    id: str
    room: RoomRef
    state: Literal["active", "ended"]
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None
    duration_s: int | None = None
    participants_count: int
    transcription_enabled: bool
    record_audio: bool


class MeetingList(Page):
    items: list[MeetingOut]


class ParticipantOut(BaseModel):
    id: str
    name: str
    type: Literal["user", "guest", "phone"]
    title: str | None = None
    department: str | None = None
    joined_at: datetime
    last_left_at: datetime | None = Field(None, description="null — участник сейчас подключён (или вышел некорректно и ещё не закрыт сервером).")
    sessions: int = Field(description="Сколько раз участник входил во встречу.")


class ParticipantList(BaseModel):
    items: list[ParticipantOut]


class SpeakerOut(BaseModel):
    id: str | None = None
    name: str
    type: Literal["user", "guest", "unknown"]


class SegmentOut(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime
    speaker: SpeakerOut
    text: str
    language: str | None = None


class TranscriptPage(Page):
    meeting_id: str
    items: list[SegmentOut]


class DocOut(BaseModel):
    id: str
    meeting_id: str
    kind: Literal["protocol", "summary", "transcript"] | str
    status: Literal["pending", "ready", "failed"] | str
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    edited_at: datetime | None = None
    model: str | None = None
    error: str | None = None


class DocFull(DocOut):
    content: str | None = None
    instruction: str | None = None


class DocList(BaseModel):
    items: list[DocOut]


class MapOut(BaseModel):
    id: str
    meeting_id: str
    status: str
    data: dict | None = None
    edits: dict | None = None
    error: str | None = None
    updated_at: datetime


class RecordingOut(BaseModel):
    id: str
    speaker: str | None = None
    size_bytes: int
    duration_s: int | None = None
    created_at: datetime
    available: bool = Field(description="Файл сейчас доступен (по последней сверке с хранилищем).")


class RecordingList(BaseModel):
    items: list[RecordingOut]


class AuthorOut(BaseModel):
    type: str
    name: str


class MessageOut(BaseModel):
    id: str
    created_at: datetime
    author: AuthorOut
    text: str


class MessageList(Page):
    items: list[MessageOut]


class EndOut(BaseModel):
    id: str
    state: Literal["ended"]
    already_ended: bool


ERR = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}, 429: {"model": ErrorOut}}


# ------------------------------------------------------------------------------------------------ вспомогательное
def _utc(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


async def _limit(request: Request, db: AsyncSession, limit: int | None) -> int:
    cfg = await api_config(request, db)
    return max(1, min(limit or 50, cfg.max_page_size))


def _pid(kind: str, text: str, what: str) -> uuid.UUID:
    v = ids.parse(kind, text)
    if v is None:
        raise not_found(what)
    return v


async def load_meeting(db: AsyncSession, p: Principal, public_id: str) -> Meeting:
    """Встреча по публичному идентификатору; вне области комнат ключа — 404 (как несуществующая)."""
    m = await db.get(Meeting, _pid("meeting", public_id, "Встреча"))
    if m is None or not p.allows_room(m.room_id):
        raise not_found("Встреча")
    return m


def _room_ref(r: Room) -> RoomRef:
    return RoomRef(id=ids.pub("room", r.id), slug=r.slug, name=r.name)


def _meeting_out(m: Meeting, people: int) -> MeetingOut:
    started, ended = _utc(m.started_at), _utc(m.ended_at)
    return MeetingOut(id=ids.pub("meeting", m.id), room=_room_ref(m.room), state="ended" if ended else "active", started_at=started, ended_at=ended, end_reason=m.end_reason,
                      duration_s=int((ended - started).total_seconds()) if ended else None, participants_count=people,
                      transcription_enabled=m.transcription_enabled, record_audio=m.record_audio)


async def _people_counts(db: AsyncSession, meeting_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not meeting_ids:
        return {}
    users = dict((await db.execute(select(MeetingParticipant.meeting_id, func.count(func.distinct(MeetingParticipant.user_id)))
                                   .where(MeetingParticipant.meeting_id.in_(meeting_ids)).group_by(MeetingParticipant.meeting_id))).all())
    guests = dict((await db.execute(select(GuestParticipant.meeting_id, func.count()).where(GuestParticipant.meeting_id.in_(meeting_ids))
                                    .group_by(GuestParticipant.meeting_id))).all())
    return {i: int(users.get(i, 0)) + int(guests.get(i, 0)) for i in meeting_ids}


# ------------------------------------------------------------------------------------------------ служебное
@router.get("/me", response_model=MeOut, tags=["service"], summary="Кто я: учётная запись ключа, права и область комнат", responses=ERR)
async def me(request: Request, p: Principal = Depends(Access(None))):
    return MeOut(client=p.client_name, key_id=p.key_id, scopes=sorted(p.scopes), rooms=sorted(ids.pub("room", uuid.UUID(r)) for r in p.rooms) if p.rooms is not None else None,
                 key_expires_at=_utc(p.key_expires_at))


# ------------------------------------------------------------------------------------------------ комнаты
def _room_out(r: Room, active: dict) -> RoomOut:
    return RoomOut(id=ids.pub("room", r.id), slug=r.slug, name=r.name, description=r.description, type=r.room_type, lifetime=r.lifetime,
                   enabled=r.is_enabled and r.lifecycle != "closed", active_meeting_id=ids.pub("meeting", active[r.id]) if r.id in active else None)


async def _active(db: AsyncSession, room_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not room_ids:
        return {}
    rows = (await db.execute(select(Meeting.room_id, Meeting.id).where(Meeting.room_id.in_(room_ids), Meeting.ended_at.is_(None)))).all()
    return {r: m for r, m in rows}


@router.get("/rooms", response_model=RoomList, tags=["rooms"], summary="Список комнат", responses=ERR)
async def list_rooms(request: Request, limit: int | None = Query(None, ge=1, le=1000), after: str | None = Query(None, description="Курсор из `next_cursor`."),
                     p: Principal = Depends(Access("rooms:read")), db: AsyncSession = Depends(get_db)):
    n = await _limit(request, db, limit)
    stmt = select(Room).order_by(Room.created_at, Room.id)
    if p.rooms is not None:
        stmt = stmt.where(Room.id.in_([uuid.UUID(r) for r in p.rooms]))
    if after:
        ts, rid = cursor.decode(after, 2)
        try:
            c_ts, c_id = _iso(ts), uuid.UUID(hex=rid)
        except ValueError:
            raise ApiError(400, "invalid_cursor", "Курсор недействителен: начните выборку сначала.") from None
        stmt = stmt.where(or_(Room.created_at > c_ts, and_(Room.created_at == c_ts, Room.id > c_id)))
    rows = (await db.execute(stmt.limit(n + 1))).scalars().unique().all()
    page = rows[:n]
    active = await _active(db, [r.id for r in page])
    nxt = cursor.encode(_utc(page[-1].created_at).isoformat(), page[-1].id.hex) if len(rows) > n else None
    return RoomList(items=[_room_out(r, active) for r in page], next_cursor=nxt)


@router.get("/rooms/{room_id}", response_model=RoomOut, tags=["rooms"], summary="Карточка комнаты", responses=ERR)
async def get_room(room_id: str, p: Principal = Depends(Access("rooms:read")), db: AsyncSession = Depends(get_db)):
    r = await db.get(Room, _pid("room", room_id, "Комната"))
    if r is None or not p.allows_room(r.id):
        raise not_found("Комната")
    return _room_out(r, await _active(db, [r.id]))


# ------------------------------------------------------------------------------------------------ встречи
@router.get("/meetings", response_model=MeetingList, tags=["meetings"], summary="Список встреч (новые первыми)", responses=ERR)
async def list_meetings(request: Request, room_id: str | None = Query(None, description="Только встречи этой комнаты (`rom_…`)."),
                        state: Literal["active", "ended"] | None = None, started_after: datetime | None = None, started_before: datetime | None = None,
                        limit: int | None = Query(None, ge=1, le=1000), after: str | None = Query(None, description="Курсор из `next_cursor`."),
                        p: Principal = Depends(Access("meetings:read")), db: AsyncSession = Depends(get_db)):
    n = await _limit(request, db, limit)
    stmt = select(Meeting).order_by(Meeting.started_at.desc(), Meeting.id.desc())
    if p.rooms is not None:
        stmt = stmt.where(Meeting.room_id.in_([uuid.UUID(r) for r in p.rooms]))
    if room_id:
        rid = _pid("room", room_id, "Комната")
        if not p.allows_room(rid):
            raise not_found("Комната")
        stmt = stmt.where(Meeting.room_id == rid)
    if state == "active":
        stmt = stmt.where(Meeting.ended_at.is_(None))
    elif state == "ended":
        stmt = stmt.where(Meeting.ended_at.is_not(None))
    if started_after:
        stmt = stmt.where(Meeting.started_at >= _utc(started_after))
    if started_before:
        stmt = stmt.where(Meeting.started_at < _utc(started_before))
    if after:
        ts, mid = cursor.decode(after, 2)
        try:
            c_ts, c_id = _iso(ts), uuid.UUID(hex=mid)
        except ValueError:
            raise ApiError(400, "invalid_cursor", "Курсор недействителен: начните выборку сначала.") from None
        stmt = stmt.where(or_(Meeting.started_at < c_ts, and_(Meeting.started_at == c_ts, Meeting.id < c_id)))
    rows = (await db.execute(stmt.limit(n + 1))).scalars().unique().all()
    page = rows[:n]
    counts = await _people_counts(db, [m.id for m in page])
    nxt = cursor.encode(_utc(page[-1].started_at).isoformat(), page[-1].id.hex) if len(rows) > n else None
    return MeetingList(items=[_meeting_out(m, counts.get(m.id, 0)) for m in page], next_cursor=nxt)


@router.get("/meetings/{meeting_id}", response_model=MeetingOut, tags=["meetings"], summary="Карточка встречи", responses=ERR)
async def get_meeting(meeting_id: str, p: Principal = Depends(Access("meetings:read")), db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    return _meeting_out(m, (await _people_counts(db, [m.id])).get(m.id, 0))


@router.get("/meetings/{meeting_id}/participants", response_model=ParticipantList, tags=["meetings"], summary="Участники встречи", responses=ERR,
            description="Один элемент на человека. Показываются имя, должность и подразделение на момент встречи; e-mail и телефон не передаются.")
async def meeting_participants(meeting_id: str, p: Principal = Depends(Access("meetings:read")), db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    people: dict[uuid.UUID, ParticipantOut] = {}
    for row in sorted(m.participants, key=lambda x: x.joined_at):
        snap = row.snapshot or {}
        cur = people.get(row.user_id)
        left = _utc(row.left_at)
        if cur is None:
            people[row.user_id] = ParticipantOut(id=ids.pub("user", row.user_id), name=str(snap.get("name") or (row.user.display_name if row.user else "Участник")), type="user",
                                                 title=str(snap.get("title") or (row.user.title if row.user else "") or "") or None,
                                                 department=str(snap.get("department") or (row.user.department if row.user else "") or "") or None,
                                                 joined_at=_utc(row.joined_at), last_left_at=left, sessions=1)
        else:
            cur.sessions += 1
            cur.last_left_at = None if (left is None or cur.last_left_at is None) else max(cur.last_left_at, left)
    out = list(people.values())
    guests = (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id == m.id).order_by(GuestParticipant.joined_at))).scalars().all()
    out += [ParticipantOut(id=ids.pub("guest", g.id), name=g.display_name, type="phone" if g.is_phone else "guest", joined_at=_utc(g.joined_at), last_left_at=_utc(g.left_at), sessions=1) for g in guests]
    return ParticipantList(items=out)


@router.post("/meetings/{meeting_id}/end", response_model=EndOut, tags=["meetings"], summary="Завершить встречу для всех", responses=ERR,
             description="Повторный вызов безопасен: для уже завершённой встречи вернётся `already_ended: true`. Действие записывается в журнал аудита от имени сервисной учётной записи.")
async def end_meeting(meeting_id: str, request: Request, p: Principal = Depends(Access("meetings:end", "write")), db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    ended = await request.app.state.meetings.end(db, m, "api", kick=True)
    if ended:
        await write_audit(db, actor_user_id=None, actor_name=f"api:{p.client_name}", action="meeting.end", target_type="meeting", target_id=str(m.id),
                          details={"room": m.room.slug, "via": "public_api", "key": p.key_id}, ip=client_ip(request))
        await db.commit()
    return EndOut(id=ids.pub("meeting", m.id), state="ended", already_ended=not ended)


# ------------------------------------------------------------------------------------------------ стенограмма
def _speaker(seg: TranscriptSegment) -> SpeakerOut:
    if seg.user_id:
        return SpeakerOut(id=ids.pub("user", seg.user_id), name=author_name(seg) or "Участник", type="user")
    if seg.guest_id:
        return SpeakerOut(id=ids.pub("guest", seg.guest_id), name=author_name(seg) or "Гость", type="guest")
    return SpeakerOut(name="Неизвестный участник", type="unknown")


def _segment_out(s: TranscriptSegment) -> SegmentOut:
    return SegmentOut(id=s.id, started_at=_utc(s.started_at), ended_at=_utc(s.ended_at), speaker=_speaker(s), text=s.text, language=s.language)


@router.get("/meetings/{meeting_id}/transcript", tags=["transcripts"], summary="Стенограмма: JSON (с курсором) или текст (txt, md, vtt, srt)", responses={**ERR, 200: {
            "content": {"application/json": {"schema": TranscriptPage.model_json_schema()}, "text/plain": {}, "text/markdown": {}, "text/vtt": {}, "application/x-subrip": {}}}},
            description="`format=json` (по умолчанию) — реплики по времени постранично (`after`, `limit`). Остальные форматы отдают стенограмму целиком (до 20 000 реплик); "
                        "для WebVTT и SRT время отсчитывается от начала встречи.")
async def transcript(meeting_id: str, request: Request, format: Literal["json", "txt", "md", "vtt", "srt"] = "json", limit: int | None = Query(None, ge=1, le=1000),
                     after: str | None = Query(None, description="Курсор из `next_cursor` (только для json)."),
                     p: Principal = Depends(Access("transcripts:read")), db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    base = select(TranscriptSegment).where(TranscriptSegment.meeting_id == m.id).order_by(TranscriptSegment.started_at, TranscriptSegment.id)
    if format == "json":
        n = await _limit(request, db, limit)
        if after:
            ts, sid = cursor.decode(after, 2)
            try:
                c_ts = _iso(str(ts))
            except ValueError:
                raise ApiError(400, "invalid_cursor", "Курсор недействителен: начните выборку сначала.") from None
            if not isinstance(sid, int):
                raise ApiError(400, "invalid_cursor", "Курсор недействителен: начните выборку сначала.")
            base = base.where(or_(TranscriptSegment.started_at > c_ts, and_(TranscriptSegment.started_at == c_ts, TranscriptSegment.id > sid)))
        rows = (await db.execute(base.limit(n + 1))).scalars().unique().all()
        page = rows[:n]
        nxt = cursor.encode(_utc(page[-1].started_at).isoformat(), page[-1].id) if len(rows) > n else None
        return TranscriptPage(meeting_id=ids.pub("meeting", m.id), items=[_segment_out(s) for s in page], next_cursor=nxt)
    ps = request.app.state.protocols
    tz = await ps._tz(db)  # noqa: SLF001
    if format == "txt":
        return PlainTextResponse(await ps.transcript_text(db, m, tz))
    rows = (await db.execute(base.limit(MAX_EXPORT_SEGMENTS + 1))).scalars().unique().all()
    if len(rows) > MAX_EXPORT_SEGMENTS:
        raise ApiError(413, "too_large", "Стенограмма слишком велика для текстового формата: используйте format=json и курсор.")
    items = [(_utc(s.started_at), _utc(s.ended_at), author_name(s) or "Неизвестный участник", s.text) for s in rows]
    origin = _utc(m.started_at)
    if format == "vtt":
        return Response(render.to_vtt(items, origin), media_type="text/vtt; charset=utf-8")
    if format == "srt":
        return Response(render.to_srt(items, origin), media_type="application/x-subrip; charset=utf-8")
    header = [f"Переговорка: {m.room.name}", f"Начало: {_utc(m.started_at).astimezone(tz):%Y-%m-%d %H:%M}"]
    return Response(render.to_markdown(f"Стенограмма: {m.room.name}", header, items, lambda t: format_clock(t, tz)), media_type="text/markdown; charset=utf-8")


# ------------------------------------------------------------------------------------------------ документы
_KIND_SCOPE = {"protocol": "protocols:read", "summary": "summaries:read", "transcript": "transcripts:read"}


def _doc(pr: Protocol, full: bool = False) -> DocOut | DocFull:
    base = dict(id=ids.pub("protocol", pr.id), meeting_id=ids.pub("meeting", pr.meeting_id), kind=pr.kind, status=pr.status, title=pr.title, created_at=_utc(pr.created_at),
                updated_at=_utc(pr.updated_at), edited_at=_utc(pr.edited_at), model=(pr.meta or {}).get("model"), error=pr.error)
    return DocFull(**base, content=pr.content, instruction=pr.instruction) if full else DocOut(**base)


def _readable_kinds(p: Principal) -> list[str]:
    return [k for k, s in _KIND_SCOPE.items() if p.has(s)]


@router.get("/meetings/{meeting_id}/documents", response_model=DocList, tags=["documents"], summary="Протоколы, резюме и сохранённые стенограммы встречи", responses=ERR,
            description="Показываются только документы тех видов, на чтение которых у ключа есть право: `protocol` — `protocols:read`, `summary` — `summaries:read`, `transcript` — `transcripts:read`.")
async def list_documents(meeting_id: str, p: Principal = Depends(Access(None)), db: AsyncSession = Depends(get_db)):
    kinds = _readable_kinds(p)
    if not kinds:
        raise ApiError(403, "insufficient_scope", "Не хватает права «protocols:read» (или summaries:read / transcripts:read).", extra={"required_scope": "protocols:read"})
    m = await load_meeting(db, p, meeting_id)
    rows = (await db.execute(select(Protocol).where(Protocol.meeting_id == m.id, Protocol.kind.in_(kinds)).order_by(Protocol.created_at, Protocol.id))).scalars().all()
    return DocList(items=[_doc(r) for r in rows])


@router.get("/meetings/{meeting_id}/documents/{document_id}", tags=["documents"], summary="Документ целиком: JSON или текст (md, txt, html)", responses={**ERR, 200: {
            "content": {"application/json": {"schema": DocFull.model_json_schema()}, "text/markdown": {}, "text/plain": {}, "text/html": {}}}})
async def get_document(meeting_id: str, document_id: str, format: Literal["json", "md", "txt", "html"] = "json", p: Principal = Depends(Access(None)),
                       db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    pr = await db.get(Protocol, _pid("protocol", document_id, "Документ"))
    if pr is None or pr.meeting_id != m.id or pr.kind not in _KIND_SCOPE:
        raise not_found("Документ")
    need = _KIND_SCOPE[pr.kind]
    if not p.has(need):
        raise ApiError(403, "insufficient_scope", f"Не хватает права «{need}».", extra={"required_scope": need})
    if format == "json":
        return _doc(pr, full=True)
    if pr.status != "ready" or pr.content is None:
        raise ApiError(409, "not_ready", "Документ ещё не готов.")
    text = pr.content
    if format == "md":
        return Response(text.rstrip() + "\n", media_type="text/markdown; charset=utf-8")
    if format == "txt":
        return Response(md_to_plain(text), media_type="text/plain; charset=utf-8")
    return Response(to_html(text, pr.title or m.room.name), media_type="text/html; charset=utf-8",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})


@router.get("/meetings/{meeting_id}/map", response_model=MapOut, tags=["documents"], summary="Карта разговора", responses=ERR)
async def get_map(meeting_id: str, p: Principal = Depends(Access("maps:read")), db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    cm = (await db.execute(select(ConversationMap).where(ConversationMap.meeting_id == m.id))).scalars().first()
    if cm is None:
        raise not_found("Карта разговора")
    return MapOut(id=ids.pub("map", cm.id), meeting_id=ids.pub("meeting", m.id), status=cm.status, data=cm.data, edits=cm.edits, error=cm.error, updated_at=_utc(cm.updated_at))


# ------------------------------------------------------------------------------------------------ записи и чат
@router.get("/meetings/{meeting_id}/recordings", response_model=RecordingList, tags=["recordings"], summary="Аудиозаписи встречи (только сведения)", responses=ERR,
            description="Скачивание файлов в этой версии API не предоставляется.")
async def list_recordings(meeting_id: str, p: Principal = Depends(Access("recordings:read")), db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    rows = (await db.execute(select(Recording).where(Recording.meeting_id == m.id).order_by(Recording.created_at, Recording.id))).scalars().all()
    names = {}
    uids = [r.user_id for r in rows if r.user_id]
    if uids:
        names = {u.id: u.display_name for u in (await db.execute(select(User).where(User.id.in_(uids)))).scalars().all()}
    return RecordingList(items=[RecordingOut(id=ids.pub("recording", r.id), speaker=names.get(r.user_id), size_bytes=r.size_bytes, duration_s=r.duration_s, created_at=_utc(r.created_at),
                                             available=r.file_state == "ok") for r in rows])


@router.get("/meetings/{meeting_id}/messages", response_model=MessageList, tags=["chat"], summary="Сообщения чата встречи (по порядку отправки)", responses=ERR)
async def list_messages(meeting_id: str, request: Request, limit: int | None = Query(None, ge=1, le=1000), after: str | None = Query(None, description="Курсор из `next_cursor`."),
                        p: Principal = Depends(Access("messages:read")), db: AsyncSession = Depends(get_db)):
    m = await load_meeting(db, p, meeting_id)
    n = await _limit(request, db, limit)
    stmt = select(MeetingChatMessage).where(MeetingChatMessage.meeting_id == m.id).order_by(MeetingChatMessage.id)
    if after:
        (last,) = cursor.decode(after, 1)
        if not isinstance(last, int):
            raise ApiError(400, "invalid_cursor", "Курсор недействителен: начните выборку сначала.")
        stmt = stmt.where(MeetingChatMessage.id > last)
    rows = (await db.execute(stmt.limit(n + 1))).scalars().all()
    page = rows[:n]
    return MessageList(items=[MessageOut(id=ids.pub("message", r.id), created_at=_utc(r.created_at), author=AuthorOut(type=r.author_type, name=r.author_name), text=r.text) for r in page],
                       next_cursor=cursor.encode(page[-1].id) if len(rows) > n else None)


# ------------------------------------------------------------------------------------------------ описание API
@router.get("/openapi.json", include_in_schema=False)
async def openapi_json(request: Request):
    from .docs import build_spec  # noqa: PLC0415

    return build_spec(request)


@router.get("/docs", include_in_schema=False)
async def docs_page(request: Request):
    from .docs import build_spec, render_html  # noqa: PLC0415

    return Response(render_html(build_spec(request)), media_type="text/html; charset=utf-8",
                    headers={"Content-Security-Policy": "default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'"})


@router.get("/docs.css", include_in_schema=False)
async def docs_css():
    from .docs import CSS  # noqa: PLC0415

    return Response(CSS, media_type="text/css; charset=utf-8")


# ------------------------------------------------------------------------------------------------ неизвестные пути
@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def unknown(rest: str):
    raise ApiError(404, "not_found", "Такого метода нет. Описание API: /api/public/v1/openapi.json")
