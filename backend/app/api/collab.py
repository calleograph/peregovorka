"""Чат комнаты и общая доска встречи (схемы draw.io).

И то и другое привязано к ВСТРЕЧЕ (а не к комнате: в комнате встреч много) и хранится вместе с её материалами.
Доступ — по тем же правилам, что к стенограмме (services/access.py); гость видит и пишет только в идущей встрече, к которой привязана его сессия.

Доска: редактор — draw.io (в браузере), здесь только обмен. Правки идут как diffSync-патчи draw.io: backend присваивает им сквозной номер
`seq` (Redis INCR — одинаков для всех экземпляров backend), рассылает через Redis pub/sub → WebSocket и хранит хвост патчей для опоздавших;
полный XML (чтобы схему можно было открыть и продолжить редактировать) сохраняется снимками, которые присылают участники после паузы в правках.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_db
from ..auth.guests import Actor, require_actor
from ..models import Meeting, MeetingChatMessage, MeetingWhiteboard, utcnow
from ..services import events, whiteboard as wb
from ..services.materials import render_chat
from ..services.access import can_access_meeting_actor

router = APIRouter(prefix="/meetings", tags=["collab"])

CHAT_MAX_CHARS = 4000
CHAT_RATE = (15, 10)            # сообщений / секунд на одного участника
PATCH_RATE = (200, 10)          # патчей доски / секунд
SNAPSHOT_RATE = (6, 10)         # снимков доски / секунд
PATCH_TAIL = 400                # сколько последних патчей хранить для опоздавших
BOARD_TTL = 12 * 3600

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]")  # управляющие и «разворот текста»; \n и \t остаются


async def _meeting_for_actor(request: Request, db: AsyncSession, meeting_id: uuid.UUID, actor: Actor, *, write: bool = False) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or not await can_access_meeting_actor(db, request.app.state.redis, meeting, actor):
        raise HTTPException(status_code=404, detail="Встреча не найдена или доступ закрыт")
    if write and meeting.ended_at is not None:
        raise HTTPException(status_code=409, detail="Встреча уже завершена")
    return meeting


async def _rate(request: Request, kind: str, actor: Actor, meeting_id: uuid.UUID, limit_window: tuple[int, int]) -> None:
    limit, window = limit_window
    key = f"rl:{kind}:{meeting_id}:{actor.id}"
    redis = request.app.state.redis
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, window)
    if n > limit:
        raise HTTPException(status_code=429, detail="Слишком часто. Подождите немного.", headers={"Retry-After": str(window)})


# ------------------------------------------------------------------------------------------------ чат
def chat_out(m: MeetingChatMessage) -> dict:
    return {"id": m.id, "meeting_id": str(m.meeting_id), "created_at": m.created_at.isoformat(), "author_type": m.author_type,
            "author_id": str(m.user_id or m.guest_id) if (m.user_id or m.guest_id) else None, "author_name": m.author_name, "text": m.text}


def clean_chat_text(raw: Any) -> str:
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail="Ожидается текст сообщения")
    text = _CTRL.sub("", raw.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        raise HTTPException(status_code=422, detail="Пустое сообщение")
    if len(text) > CHAT_MAX_CHARS:
        raise HTTPException(status_code=422, detail=f"Сообщение длиннее {CHAT_MAX_CHARS} символов")
    return text


@router.get("/{meeting_id}/chat")
async def chat_list(meeting_id: uuid.UUID, request: Request, after_id: int = Query(0, ge=0), before_id: int | None = Query(None, ge=1),
                    limit: int = Query(100, ge=1, le=500), actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    """Без параметров — последние `limit` сообщений; `before_id` — более ранние (прокрутка вверх); `after_id` — новые после id (после переподключения)."""
    await _meeting_for_actor(request, db, meeting_id, actor)
    stmt = select(MeetingChatMessage).where(MeetingChatMessage.meeting_id == meeting_id)
    if after_id:
        rows = (await db.execute(stmt.where(MeetingChatMessage.id > after_id).order_by(MeetingChatMessage.id).limit(limit + 1))).scalars().all()
        has_more = len(rows) > limit
        rows = rows[:limit]
    else:
        if before_id:
            stmt = stmt.where(MeetingChatMessage.id < before_id)
        rows = list((await db.execute(stmt.order_by(MeetingChatMessage.id.desc()).limit(limit + 1))).scalars().all())
        has_more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
    return {"messages": [chat_out(m) for m in rows], "has_more": has_more}


@router.post("/{meeting_id}/chat", status_code=201)
async def chat_post(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), actor: Actor = Depends(require_actor),
                    db: AsyncSession = Depends(get_db)):
    await _meeting_for_actor(request, db, meeting_id, actor, write=True)
    text = clean_chat_text(body.get("text"))
    await _rate(request, "chat", actor, meeting_id, CHAT_RATE)
    msg = MeetingChatMessage(meeting_id=meeting_id, author_type="guest" if actor.is_guest else "user",
                             user_id=None if actor.is_guest else actor.id, guest_id=actor.id if actor.is_guest else None,
                             author_name=actor.label, text=text)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    out = chat_out(msg)
    await events.publish(request.app.state.redis, meeting_id, {"type": "chat_message", "message": out})
    return out


@router.get("/{meeting_id}/chat.txt", response_class=PlainTextResponse)
async def chat_txt(meeting_id: uuid.UUID, request: Request, actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    await _meeting_for_actor(request, db, meeting_id, actor)
    rows = (await db.execute(select(MeetingChatMessage).where(MeetingChatMessage.meeting_id == meeting_id)
                             .order_by(MeetingChatMessage.id))).scalars().all()
    tz = await request.app.state.protocols._tz(db)  # noqa: SLF001
    return PlainTextResponse(render_chat(list(rows), tz), headers={
        "Content-Disposition": f'attachment; filename="chat-{str(meeting_id)[:8]}.txt"', "Cache-Control": "no-store"})


# ----------------------------------------------------------------------------------------------- доска
def _seq_key(meeting_id: uuid.UUID) -> str:
    return f"wb:{meeting_id}:seq"


def _tail_key(meeting_id: uuid.UUID) -> str:
    return f"wb:{meeting_id}:patches"


def board_meta(row: MeetingWhiteboard | None) -> dict:
    return {"used": bool(row and row.shapes > 0), "shapes": row.shapes if row else 0,
            "updated_at": row.updated_at.isoformat() if row else None, "updated_by": row.updated_by if row else None}


@router.get("/{meeting_id}/whiteboard")
async def board_get(meeting_id: uuid.UUID, request: Request, actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    """Последний снимок схемы + патчи, пришедшие после него (чтобы опоздавший участник получил актуальное состояние)."""
    meeting = await _meeting_for_actor(request, db, meeting_id, actor)
    row = await db.get(MeetingWhiteboard, meeting_id)
    base = row.seq if row else 0
    patches: list[dict] = []
    if meeting.ended_at is None:
        for raw in await request.app.state.redis.lrange(_tail_key(meeting_id), 0, -1):
            p = json.loads(raw)
            if p["seq"] > base:
                patches.append(p)
        patches.sort(key=lambda p: p["seq"])
    return {"xml": row.xml if row else None, "seq": base, "patches": patches, "active": meeting.ended_at is None, **board_meta(row)}


@router.post("/{meeting_id}/whiteboard/patch")
async def board_patch(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), actor: Actor = Depends(require_actor),
                      db: AsyncSession = Depends(get_db)):
    """Правка схемы (diffSync-патч draw.io). Backend присваивает номер и рассылает всем подписанным на встречу."""
    await _meeting_for_actor(request, db, meeting_id, actor, write=True)
    try:
        patch = wb.validate_patch(body.get("patch"))
    except wb.WhiteboardError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    checksum = body.get("checksum")
    client_id = str(body.get("client_id") or "")[:64]
    await _rate(request, "wbp", actor, meeting_id, PATCH_RATE)
    redis = request.app.state.redis
    if not await redis.exists(_seq_key(meeting_id)):
        row = await db.get(MeetingWhiteboard, meeting_id)
        await redis.set(_seq_key(meeting_id), row.seq if row else 0, nx=True, ex=BOARD_TTL)
    seq = await redis.incr(_seq_key(meeting_id))
    event = {"type": "whiteboard_patch", "seq": seq, "patch": patch, "checksum": checksum if isinstance(checksum, str) else None,
             "from": client_id, "by": actor.label}
    await redis.rpush(_tail_key(meeting_id), json.dumps(event, ensure_ascii=False))
    await redis.ltrim(_tail_key(meeting_id), -PATCH_TAIL, -1)
    await redis.expire(_tail_key(meeting_id), BOARD_TTL)
    await redis.expire(_seq_key(meeting_id), BOARD_TTL)
    await events.publish(redis, meeting_id, event)
    return {"seq": seq}


@router.put("/{meeting_id}/whiteboard")
async def board_save(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), actor: Actor = Depends(require_actor),
                     db: AsyncSession = Depends(get_db)):
    """Снимок схемы (полный XML draw.io), `seq` — номер последнего патча, учтённого в этом снимке. Старее сохранённого не принимается."""
    await _meeting_for_actor(request, db, meeting_id, actor, write=True)
    xml, seq = body.get("xml"), body.get("seq")
    if not isinstance(seq, int) or seq < 0:
        raise HTTPException(status_code=422, detail="seq: целое число")
    if not isinstance(xml, str) or not xml.strip() or len(xml) > wb.MAX_XML_CHARS:
        raise HTTPException(status_code=422, detail="xml: схема draw.io (не пустая, не больше 3 МБ)")
    await _rate(request, "wbs", actor, meeting_id, SNAPSHOT_RATE)  # разбор XML — самая дорогая часть, лимит стоит перед ним
    try:
        desc = wb.describe(wb.validate(xml))
    except wb.WhiteboardError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    row = await db.get(MeetingWhiteboard, meeting_id)
    if row is not None and seq < row.seq:
        return {"saved": False, "seq": row.seq, **board_meta(row)}
    if row is None:
        row = MeetingWhiteboard(meeting_id=meeting_id, created_at=utcnow())
        db.add(row)
    row.xml, row.seq, row.shapes, row.updated_at, row.updated_by = xml, seq, desc.shapes, utcnow(), actor.label
    await db.commit()
    await events.publish(request.app.state.redis, meeting_id, {"type": "whiteboard_saved", "seq": seq, "shapes": desc.shapes, "by": actor.label})
    return {"saved": True, "seq": seq, **board_meta(row)}


@router.get("/{meeting_id}/whiteboard.drawio")
async def board_download(meeting_id: uuid.UUID, request: Request, actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    """Схема в формате draw.io (XML): открывается в draw.io / diagrams.net для дальнейшего редактирования."""
    from fastapi.responses import Response  # noqa: PLC0415

    await _meeting_for_actor(request, db, meeting_id, actor)
    row = await db.get(MeetingWhiteboard, meeting_id)
    if row is None or not row.xml:
        raise HTTPException(status_code=404, detail="Доска в этой встрече не использовалась")
    return Response(row.xml.encode("utf-8"), media_type="application/vnd.jgraph.mxfile", headers={
        "Content-Disposition": f'attachment; filename="whiteboard-{str(meeting_id)[:8]}.drawio"', "Cache-Control": "no-store"})
