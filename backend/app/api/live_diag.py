"""Диагностика эфира: текущее состояние, журнал, ряды показателей, история и выгрузки.

Серверные показатели видят те, кто может управлять эфиром этой комнаты, и администраторы; зритель получает только свою клиентскую диагностику — она считается в его браузере.
Отчёт браузера участника принимается здесь, отчёт зрителя приходит пульсом страницы просмотра (api/live.py)."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, current_session, get_db, require_admin, require_user
from ..models import Broadcast, LiveDiagEvent, LiveDiagSample, Meeting, Room
from ..services import roles
from ..services.journal import parse_client
from ..services.live import LIVE_ACTIVE
from ..services.live_diag import RING_KEYS, clean, ring_point
from .guest import _throttle
from .meetings import get_meeting_for_user

router = APIRouter(tags=["live-diag"])
public_router = APIRouter(prefix="/live", tags=["live-diag"])

LEVEL_RU = {"info": "инфо", "warn": "предупреждение", "error": "ошибка"}
KIND_RU = {"confirmed": "подтверждено", "assumed": "предположение", "unknown": "данных недостаточно"}
SERIES_MAX = 720


def _can(room: Room, su: SessionUser) -> bool:
    return roles.can_control_meeting(room, su) or roles.can_manage_room(room, su)


async def _latest(db: AsyncSession, room_id: uuid.UUID, meeting_id: uuid.UUID | None = None) -> Broadcast | None:
    """Идущий эфир комнаты, а если его нет — последний (этой встречи, если она указана)."""
    q = select(Broadcast).where(Broadcast.room_id == room_id).order_by(Broadcast.started_at.desc())
    rows = list((await db.execute(q.limit(20))).scalars())
    return next((b for b in rows if b.status in LIVE_ACTIVE), None) or next((b for b in rows if meeting_id is None or b.meeting_id == meeting_id), None)


async def _owned(request: Request, db: AsyncSession, bid: uuid.UUID, su: SessionUser) -> tuple[Broadcast, Room]:
    b = await db.get(Broadcast, bid)
    room = await db.get(Room, b.room_id) if b is not None else None
    if b is None or room is None or not _can(room, su):
        raise HTTPException(status_code=404, detail="Эфир не найден или нет прав")
    return b, room


def _event_out(e: LiveDiagEvent) -> dict:
    return {"id": e.id, "at": e.at.astimezone(timezone.utc).isoformat(), "component": e.component, "level": e.level, "code": e.code, "message": e.message, "cause": e.cause, "kind": e.cause_kind, "details": e.details}


async def _state(request: Request, db: AsyncSession, b: Broadcast | None, *, ring: bool, events: bool) -> dict:
    cfg = await request.app.state.settings_svc.get(db, "live")
    if b is None:
        return {"bid": None, "status": "idle", "enabled": bool(cfg.diag_enabled), "now": None, "ring": [], "keys": RING_KEYS, "events": []}
    out = await request.app.state.live_diag.current(b.id, ring=ring)
    out.update(bid=str(b.id), status=b.status, quality=b.quality, started_at=b.started_at.isoformat(), ended_at=b.ended_at.isoformat() if b.ended_at else None, enabled=bool(cfg.diag_enabled), error=b.error)
    out["stages"] = [_event_out(e) for e in (await db.execute(select(LiveDiagEvent).where(LiveDiagEvent.broadcast_id == b.id, LiveDiagEvent.code.startswith("stage_")).order_by(LiveDiagEvent.id).limit(12))).scalars()]
    if events:
        rows = (await db.execute(select(LiveDiagEvent).where(LiveDiagEvent.broadcast_id == b.id).order_by(LiveDiagEvent.id.desc()).limit(60))).scalars()
        out["events"] = [_event_out(e) for e in rows]
    return out


@router.get("/meetings/{meeting_id}/live/diag")
async def meeting_diag(meeting_id: uuid.UUID, request: Request, ring: bool = True, events: bool = True, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not _can(meeting.room, su):
        raise HTTPException(status_code=403, detail="Диагностика доступна тем, кто управляет эфиром")
    return await _state(request, db, await _latest(db, meeting.room_id, meeting.id), ring=ring, events=events)


@router.post("/meetings/{meeting_id}/live/diag/client", status_code=204)
async def meeting_client(meeting_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Показатели WebRTC из браузера участника: разрешение и кадры источника, потери, задержка, причина снижения качества."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    b = await request.app.state.live.current(db, meeting.room_id)
    if b is not None and b.status in ("starting", "live"):
        key = hashlib.sha256(str(su.user_id).encode()).hexdigest()[:12]
        await request.app.state.live_diag.report_client(b.id, f"p:{key}", body.get("stats"), parse_client(request.headers.get("user-agent")))
    return Response(status_code=204)


@public_router.get("/{token}/diag")
async def viewer_diag_access(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Страница зрителя: вошедшему организатору — встреча для серверных показателей. Ответ всегда 200: зрителю без входа нечего сообщать и незачем ошибка 401."""
    await _throttle(request, "live:diag", 600, 600)
    su = await current_session(request)
    room = (await db.execute(select(Room).where(Room.live_token == token))).scalars().first()
    b = None
    if room is None:
        b = (await db.execute(select(Broadcast).where(Broadcast.view_token == token))).scalars().first()
        room = await db.get(Room, b.room_id) if b is not None else None
    if su is None or room is None or su.must_change or su.must_totp or not _can(room, su):
        return {"allowed": False}
    b = b or await _latest(db, room.id)
    return {"allowed": True, "meeting_id": str(b.meeting_id) if b is not None else None}


@router.get("/live-diag/{bid}/events")
async def diag_events(bid: uuid.UUID, request: Request, level: str | None = None, component: str | None = None, before: int | None = None, limit: int = Query(default=200, ge=1, le=1000),
                      su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    b, _room = await _owned(request, db, bid, su)
    q = select(LiveDiagEvent).where(LiveDiagEvent.broadcast_id == b.id)
    if level:
        q = q.where(LiveDiagEvent.level.in_(level.split(",")[:3]))
    if component:
        q = q.where(LiveDiagEvent.component.in_(component.split(",")[:12]))
    if before:
        q = q.where(LiveDiagEvent.id < before)
    rows = list((await db.execute(q.order_by(LiveDiagEvent.id.desc()).limit(limit))).scalars())
    return {"items": [_event_out(e) for e in rows]}


@router.get("/live-diag/{bid}/series")
async def diag_series(bid: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Показатели всего эфира для графиков: шаг 15 с, длинный эфир прореживается до SERIES_MAX точек."""
    b, _room = await _owned(request, db, bid, su)
    rows = list((await db.execute(select(LiveDiagSample.data).where(LiveDiagSample.broadcast_id == b.id).order_by(LiveDiagSample.at))).scalars())
    step = max(1, len(rows) // SERIES_MAX)
    return {"keys": RING_KEYS, "points": [ring_point(d) for d in rows[::step]]}


async def _history(db: AsyncSession, where: Any, limit: int) -> list[dict]:
    rows = (await db.execute(select(Broadcast, Room.name).join(Room, Room.id == Broadcast.room_id).where(where).order_by(Broadcast.started_at.desc()).limit(limit))).all()
    return [{"id": str(b.id), "room": name, "title": b.title, "status": b.status, "quality": b.quality, "started_at": b.started_at.isoformat(), "ended_at": b.ended_at.isoformat() if b.ended_at else None,
             "peak_viewers": b.peak_viewers, "summary": b.diag_summary} for b, name in rows]


@router.get("/rooms/{room_id}/live/diag/history")
async def room_history(room_id: uuid.UUID, request: Request, limit: int = Query(default=30, ge=1, le=100), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await db.get(Room, room_id)
    if room is None or not _can(room, su):
        raise HTTPException(status_code=404, detail="Комната не найдена или нет прав")
    return {"items": await _history(db, Broadcast.room_id == room.id, limit)}


@router.get("/admin/live/diag/history")
async def admin_history(limit: int = Query(default=50, ge=1, le=200), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return {"items": await _history(db, Broadcast.diag_summary.is_not(None), limit)}


def _line(e: LiveDiagEvent) -> str:
    t = e.at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cause = f" | {KIND_RU.get(e.cause_kind or '', 'причина')}: {e.cause}" if e.cause else ""
    return f"{t} UTC  {LEVEL_RU.get(e.level, e.level):<14} {e.component:<10} {e.message}{cause}"


def _meta(b: Broadcast, room: Room, request: Request) -> dict:
    s = request.app.state.settings
    return {"broadcast_id": str(b.id), "room": room.name, "title": b.title, "quality": b.quality, "status": b.status, "started_at": b.started_at.isoformat(), "ended_at": b.ended_at.isoformat() if b.ended_at else None,
            "peak_viewers": b.peak_viewers, "error": b.error, "version": s.app_version, "commit": s.app_git_commit, "exported_at": datetime.now(timezone.utc).isoformat()}


def _fmt(v: Any, unit: str = "") -> str:
    if isinstance(v, dict):
        return f"среднее {v.get('avg')}{unit}, максимум {v.get('max')}{unit}"
    return "нет данных" if v is None else f"{v}{unit}"


def _report(meta: dict, summary: dict | None, events: list[LiveDiagEvent]) -> str:
    s = summary or {}
    head = [f"ДИАГНОСТИЧЕСКИЙ ОТЧЁТ ЭФИРА {meta['broadcast_id']}", "", f"Комната: {meta['room']}", f"Название: {meta['title'] or '—'}", f"Качество: {meta['quality']}", f"Состояние: {meta['status']}",
            f"Начало: {meta['started_at']}", f"Окончание: {meta['ended_at'] or 'идёт'}", f"Версия: {meta['version']} ({meta['commit']})", f"Зрителей на пике: {meta['peak_viewers']}"]
    if meta.get("error"):
        head.append(f"Ошибка: {meta['error']}")
    body = ["", "ИТОГ", f"Длительность: {_fmt(s.get('duration_s'), ' с')}", f"Режим: {json.dumps(s.get('mode') or {}, ensure_ascii=False)}", f"Разрешение потока: {_fmt(s.get('res'))}",
            f"Процессор сервера: {_fmt(s.get('cpu'), ' %')}", f"Память занята: {_fmt(s.get('mem_used_pct'), ' %')}", f"Исходящий трафик: {_fmt(s.get('tx_mbit'), ' Мбит/с')}", f"Диск занят: {_fmt(s.get('disk_util'), ' %')}",
            f"Нагрузка узла Egress: {_fmt(s.get('egress_load'), ' %')}", f"Ядер у Egress: {_fmt(s.get('egress_cores'))}", f"Сегмент готов через: {_fmt(s.get('seg_ready_s'), ' с')}", f"Битрейт видео: {_fmt(s.get('video_kbps'), ' кбит/с')}",
            f"Кадров в секунду: {_fmt(s.get('fps'))}", f"Отставание зрителя от источника: {_fmt(s.get('latency_s'), ' с')}", f"Загрузка сегмента у зрителя: {_fmt(s.get('seg_ms'), ' мс')}",
            f"Остановок воспроизведения: {_fmt(s.get('stalls'))}", f"Переподключений зрителей: {_fmt(s.get('reconnects'))}"]
    problems = [e for e in events if e.level != "info"]
    body += ["", f"ПРОБЛЕМЫ ({len(problems)})"] + ([_line(e) for e in problems] or ["Предупреждений и ошибок нет."])
    body += ["", f"ХРОНОЛОГИЯ ({len(events)})"] + [_line(e) for e in events]
    return "\n".join(head + body) + "\n"


@router.get("/live-diag/{bid}/export")
async def diag_export(bid: uuid.UUID, request: Request, format: str = "json", su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """journal.txt — журнал; report.txt — полный текстовый отчёт; json — всё: идентификатор, показатели сервера и клиентов, последовательность событий. Секретов нет: записи очищены при сохранении и ещё раз здесь."""
    if format not in ("json", "txt", "report"):
        raise HTTPException(status_code=422, detail="Ожидается format: json | txt | report")
    b, room = await _owned(request, db, bid, su)
    meta = _meta(b, room, request)
    events = list((await db.execute(select(LiveDiagEvent).where(LiveDiagEvent.broadcast_id == b.id).order_by(LiveDiagEvent.at, LiveDiagEvent.id))).scalars())
    short = str(b.id)[:8]
    if format == "txt":
        body, name, mime = "\n".join([f"Журнал эфира {meta['broadcast_id']}: {meta['room']}", ""] + [_line(e) for e in events]) + "\n", f"efir-{short}-journal.txt", "text/plain; charset=utf-8"
    elif format == "report":
        summary = b.diag_summary or await request.app.state.live_diag.summary(db, b, save=b.status not in LIVE_ACTIVE)         # идущий эфир: итог на этот момент
        body, name, mime = _report(meta, summary, events), f"efir-{short}-report.txt", "text/plain; charset=utf-8"
    else:
        samples = [{"at": at.isoformat(), **d} for at, d in (await db.execute(select(LiveDiagSample.at, LiveDiagSample.data).where(LiveDiagSample.broadcast_id == b.id).order_by(LiveDiagSample.at))).all()]
        body = json.dumps(clean({"schema": 1, "broadcast": meta, "summary": b.diag_summary, "events": [_event_out(e) for e in events], "samples": samples}), ensure_ascii=False, default=str)
        name, mime = f"efir-{short}.json", "application/json"
    return Response(content=body.encode("utf-8"), media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
