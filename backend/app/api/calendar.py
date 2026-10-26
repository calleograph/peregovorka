"""Календарь Outlook в Peregovorka: состояние и согласие, события за период, создание, перенос и отмена встречи. Все решения — в `services/calendar_svc.py`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import User
from ..services import calendar_svc as cs
from ..services import outlook as ol
from ..services import storage_encryption as se
from ..services.audit import write_audit

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _fail(exc: cs.CalendarDenied):
    raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None


async def _cfg(request: Request, db: AsyncSession):
    return await request.app.state.settings_svc.get(db, "outlook")


def _base(request: Request, cfg) -> str:
    return ol.public_url(request.app.state.settings.app_public_url, cfg)


def _dt(v: str, name: str) -> datetime:
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Параметр «{name}»: время в формате ISO 8601") from None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _transport(request: Request):
    return getattr(request.app.state, "calendar_transport", None)       # подставной транспорт в испытаниях; в работе — None


@router.get("/status")
async def status(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Показывать ли пункт «Календарь», подключён ли он у сотрудника и что ему доступно."""
    return await cs.status(db, su, await _cfg(request, db))


@router.post("/connect")
async def connect(request: Request, body: dict = Body(default={}), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Мастер подключения: сотрудник подтверждает, что разрешает Peregovorka показывать и вести его календарь Outlook; перед записью согласия проверяется доступ к его ящику."""
    if body.get("accept") is not True:
        raise HTTPException(status_code=422, detail={"code": "accept_required", "message": "Подтвердите согласие на доступ к календарю."})
    cfg = await _cfg(request, db)
    try:
        res = await cs.connect(db, su, cfg, request.app.state.redis, _transport(request))
    except cs.CalendarDenied as exc:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="calendar.connect", target_type="user", target_id=str(su.user_id),
                          ip=client_ip(request), result="denied", details={"code": exc.code})
        await db.commit()
        _fail(exc)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="calendar.connect", target_type="user", target_id=str(su.user_id), ip=client_ip(request))
    await db.commit()
    return res


@router.delete("/connect")
async def disconnect(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    cfg = await _cfg(request, db)
    await cs.disconnect(db, su, request.app.state.redis)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="calendar.disconnect", target_type="user", target_id=str(su.user_id), ip=client_ip(request))
    await db.commit()
    return await cs.status(db, su, cfg)


@router.get("/events")
async def events(request: Request, start: str = Query(alias="from", max_length=40), end: str = Query(alias="to", max_length=40), refresh: bool = False,
                 su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    cfg = await _cfg(request, db)
    try:
        return await cs.events(db, su, cfg, request.app.state.redis, _dt(start, "from"), _dt(end, "to"), base=_base(request, cfg), refresh=refresh, transport=_transport(request))
    except cs.CalendarDenied as exc:
        _fail(exc)


async def _attendees(request: Request, db: AsyncSession, raw: Any) -> tuple[list[dict], list[str]]:
    """Участники от интерфейса: сотрудник из поиска (`id` или `ref`+`login`, адрес берётся из профиля на сервере, чужой адрес подставить нельзя) или внешний адрес почты.
    Возвращает (участники, имена тех, у кого в профиле нет адреса)."""
    from .chats import _resolve_user  # noqa: PLC0415

    if raw is None:
        return [], []
    if not isinstance(raw, list) or len(raw) > cs.MAX_ATTENDEES:
        raise HTTPException(status_code=422, detail={"code": "bad_attendees", "message": f"Участников не больше {cs.MAX_ATTENDEES}."})
    out: list[dict] = []
    skipped: list[str] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        optional = bool(it.get("optional"))
        if it.get("email") and not (it.get("id") or it.get("ref")):
            out.append({"email": str(it["email"]).strip().lower(), "name": it.get("name"), "optional": optional})
            continue
        uid = await _resolve_user(request, db, it)
        u = await db.get(User, uid)
        if u is None or not u.is_active:
            raise HTTPException(status_code=404, detail={"code": "attendee_not_found", "message": "Сотрудник не найден или учётная запись отключена."})
        if not (u.email or "").strip():
            skipped.append(u.display_name)
            continue
        out.append({"email": u.email.strip().lower(), "name": u.display_name, "optional": optional})
    return out, skipped


@router.post("/events", status_code=201)
async def create(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    cfg = await _cfg(request, db)
    s = request.app.state
    attendees, skipped = await _attendees(request, db, body.get("attendees"))
    body = {**body, "attendees": attendees}
    try:
        res = await cs.create(db, su, cfg, s.redis, body, base=_base(request, cfg), settings=s.settings, encrypt_storage=se.default_for_new(await s.settings_svc.get(db, "general")),
                              transport=_transport(request))
    except cs.CalendarDenied as exc:
        _fail(exc)
    if not res.get("duplicate"):
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="calendar.create", target_type="room" if res.get("room_id") else "user",
                          target_id=res.get("room_id") or str(su.user_id), ip=client_ip(request),
                          details={"start": res["start"], "end": res["end"], "recurring": res["recurring"], "conference": bool(res.get("room_id")), "attendees": len(body.get("attendees") or [])})
        await db.commit()
    return {**res, "skipped": skipped}


@router.patch("/events/{room_id}")
async def reschedule(room_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    cfg = await _cfg(request, db)
    try:
        res = await cs.reschedule(db, su, cfg, request.app.state.redis, room_id, body, transport=_transport(request))
    except cs.CalendarDenied as exc:
        _fail(exc)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="calendar.update", target_type="room", target_id=str(room_id), ip=client_ip(request),
                      details={"fields": sorted(k for k in ("subject", "start", "end") if body.get(k) is not None)})
    await db.commit()
    return res


@router.delete("/events/{room_id}")
async def cancel(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    cfg = await _cfg(request, db)
    try:
        res = await cs.cancel(db, su, cfg, request.app.state.redis, room_id, transport=_transport(request))
    except cs.CalendarDenied as exc:
        _fail(exc)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="calendar.cancel", target_type="room", target_id=str(room_id), ip=client_ip(request))
    await db.commit()
    return res
