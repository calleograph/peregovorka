"""Надстройка Outlook: выдача токена, создание/перенос/отмена запланированной конференции, публичная страница обратного отсчёта, манифест.

Токен надстройки выдаётся вошедшему пользователю (в окне Office dialog, где работают пароль, 2FA и CAPTCHA) и годится только для этих ручек. Время — в UTC (ISO 8601).
См. services/outlook.py и docs/OUTLOOK_ADDIN.md.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import Meeting, Room, utcnow
from ..services import outlook as ol
from ..services import storage_encryption as se
from ..services.audit import write_audit
from ..services.temp_rooms import TempRoomError

router = APIRouter(prefix="/outlook", tags=["outlook"])
PUBLIC_LIMIT, PUBLIC_WINDOW_S = 120, 60            # опросов страницы обратного отсчёта в минуту с одного IP
CREATE_LIMIT, CREATE_WINDOW_S = 30, 60             # создаёт/переносит не чаще 30 раз в минуту на пользователя


async def _cfg(request: Request, db: AsyncSession):
    return await request.app.state.settings_svc.get(db, "outlook")


async def _addin_user(request: Request) -> SessionUser:
    h = request.headers.get("authorization", "")
    su = await ol.principal(request.app.state.redis, h[7:].strip()) if h.lower().startswith("bearer ") else None
    if su is None:
        raise HTTPException(status_code=401, detail={"code": "token_invalid", "message": "Нужен вход: токен надстройки истёк или не выдан."})
    r = request.app.state.redis
    key = f"outlook:rate:{su.user_id}"
    if await r.incr(key) == 1:
        await r.expire(key, CREATE_WINDOW_S)
    if int(await r.get(key) or 0) > CREATE_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много запросов. Подождите минуту.", headers={"Retry-After": str(CREATE_WINDOW_S)})
    return su


def _dt(v, field: str) -> datetime:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Поле «{field}»: время в формате ISO 8601 (например, 2026-10-20T07:00:00Z)") from None
    return ol.aware(d)


_EMAIL = re.compile(r"^[^@\s<>\"']{1,120}@[A-Za-z0-9.-]{1,120}$")
_ITEM_ID = re.compile(r"^[A-Za-z0-9+/=_.%-]{8,700}$")


def _link(room: Room, body: dict) -> None:
    """Связь с событием календаря для серверной сверки: организатор, идентификаторы (EWS и REST), серия, часовой пояс. Неверные значения молча отбрасываются."""
    org = str(body.get("organizer") or "").strip().lower()
    if _EMAIL.match(org):
        room.outlook_organizer = org
    for field, key in (("outlook_ews_id", "ews_id"), ("outlook_rest_id", "rest_id"), ("outlook_series_id", "series_id")):
        v = str(body.get(key) or "").strip()
        if _ITEM_ID.match(v) and getattr(room, field) != v:
            setattr(room, field, v)
            room.outlook_synced_at = None                                     # новый идентификатор — сверить при ближайшем проходе
    tz = str(body.get("tz") or "").strip()
    if re.match(r"^[A-Za-z0-9_+./ -]{1,64}$", tz):
        room.outlook_tz = tz


def _fail(exc: TempRoomError):
    raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None


def _out(room: Room, base: str, cfg, created: bool = False, meeting_active: bool = False) -> dict:
    html, text = ol.invite_texts(room, base, cfg, guest=bool(room.guest_access_enabled and cfg.guest_links))
    return {"id": str(room.id), "created": created, "url": ol.join_link(base, room), "state": ol.state(room, meeting_active=meeting_active), "name": room.name,
            "start": room.scheduled_start, "end": room.scheduled_end, "opens_at": room.join_opens_at, "recurring": room.recurring, "invite_html": html, "invite_text": text}


@router.get("/config")
async def config(request: Request, db: AsyncSession = Depends(get_db)):
    """Нужно надстройке до входа: включена ли интеграция и как называется сервер."""
    cfg = await _cfg(request, db)
    return {"enabled": cfg.enabled, "early_minutes": cfg.early_minutes}


@router.post("/token")
async def token(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Токен надстройки для вошедшего пользователя (вызывается страницей `/outlook/auth.html` в окне Office dialog)."""
    cfg = await _cfg(request, db)
    if not cfg.enabled:
        raise HTTPException(status_code=404, detail="Интеграция с Outlook отключена.")
    if not ol.allowed(cfg, su):
        raise HTTPException(status_code=403, detail="Создавать конференции из Outlook вам не разрешено.")
    tok, ttl = await ol.issue_token(request.app.state.redis, su, cfg.token_ttl_hours)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.token", target_type="user", target_id=str(su.user_id), ip=client_ip(request))
    await db.commit()
    return Response('{"token":"%s","expires_in":%d}' % (tok, ttl), media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/meetings")
async def create_meeting(request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """Создать запланированную конференцию для события календаря (идемпотентно по `key`) или вернуть уже связанную; обновляет время и название."""
    su = await _addin_user(request)
    cfg = await _cfg(request, db)
    s = request.app.state
    try:
        room, created = await ol.ensure(db, su, cfg, key=str(body.get("key") or ""), subject=str(body.get("subject") or ""), start=_dt(body.get("start"), "start"),
                                        end=_dt(body.get("end"), "end"), recurring=bool(body.get("recurring")), default_text_days=s.settings.default_text_retention_days,
                                        default_audio_days=s.settings.default_audio_retention_days,
                                        encrypt_storage=se.default_for_new(await s.settings_svc.get(db, "general")))
    except TempRoomError as exc:
        _fail(exc)
    _link(room, body)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.create" if created else "outlook.sync", target_type="room", target_id=str(room.id),
                      ip=client_ip(request), details={"start": room.scheduled_start.isoformat(), "end": room.scheduled_end.isoformat(), "recurring": room.recurring})
    await db.commit()
    return _out(room, ol.public_url(s.settings.app_public_url, cfg), cfg, created)


@router.post("/log", status_code=204)
async def addin_log(request: Request, body: dict = Body(...)):
    """Ошибки надстройки (то, что пользователь видит в уведомлении) попадают в журнал событий: «Администрирование → Интеграции → Microsoft Outlook»."""
    su = await _addin_user(request)
    msg = " ".join(str(body.get("message") or "").split())[:300]
    if msg:
        request.app.state.journal.emit("outlook", "addin_error", level="warn", user=su.display_name, ip=client_ip(request), message=msg, data={"where": str(body.get("where") or "")[:40]})


@router.get("/meetings/by-key/{key}")
async def by_key(key: str, request: Request, db: AsyncSession = Depends(get_db)):
    su = await _addin_user(request)
    cfg = await _cfg(request, db)
    room = await ol.find_by_key(db, su.user_id, key) if ol.KEY_RE.match(key) else None
    if room is None:
        raise HTTPException(status_code=404, detail="Связанной конференции нет.")
    return _out(room, ol.public_url(request.app.state.settings.app_public_url, cfg), cfg)


@router.patch("/meetings/{room_id}")
async def update_meeting(room_id: uuid.UUID, request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    su = await _addin_user(request)
    cfg = await _cfg(request, db)
    try:
        room = await ol.owned(db, su, room_id)
        changed = await ol.reschedule(db, room, cfg, subject=" ".join(str(body.get("subject") or "").split())[:200] or None,
                                      start=_dt(body.get("start", room.scheduled_start.isoformat()), "start"), end=_dt(body.get("end", room.scheduled_end.isoformat()), "end"),
                                      recurring=bool(body.get("recurring", room.recurring)))
    except TempRoomError as exc:
        _fail(exc)
    if changed:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.reschedule", target_type="room", target_id=str(room.id), ip=client_ip(request),
                          details={"start": room.scheduled_start.isoformat(), "end": room.scheduled_end.isoformat()})
        await db.commit()
    return _out(room, ol.public_url(request.app.state.settings.app_public_url, cfg), cfg)


@router.delete("/meetings/{room_id}", status_code=204)
async def cancel_meeting(room_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
    su = await _addin_user(request)
    try:
        room = await ol.owned(db, su, room_id)
    except TempRoomError as exc:
        _fail(exc)
    if await ol.cancel(db, room):
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="outlook.cancel", target_type="room", target_id=str(room.id), ip=client_ip(request))
        await db.commit()


@router.get("/public/{code}")
async def public_info(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Страница обратного отсчёта: название, время, состояние. Без входа; неверный код и чужая комната отвечают одинаково (404)."""
    r = request.app.state.redis
    key = f"outlook:pub:{client_ip(request)}"
    if await r.incr(key) == 1:
        await r.expire(key, PUBLIC_WINDOW_S)
    if int(await r.get(key) or 0) > PUBLIC_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много обращений.", headers={"Retry-After": str(PUBLIC_WINDOW_S)})
    cfg = await _cfg(request, db)
    code = code.strip().lower()
    if not cfg.enabled or not (20 <= len(code) <= 32) or not code.isalnum():
        raise HTTPException(status_code=404, detail="Конференция не найдена.")
    room = (await db.execute(select(Room).where(Room.slug == ol.PREFIX + code))).scalars().first()
    if room is None or room.scheduled_start is None:
        raise HTTPException(status_code=404, detail="Конференция не найдена.")
    live = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_(None)))).first() is not None
    now = utcnow()
    return ol.public_view(room, ol.state(room, now, meeting_active=live), now=now, cfg=cfg)


@router.get("/manifest.xml")
async def manifest(request: Request, db: AsyncSession = Depends(get_db)):
    """Манифест надстройки для установки администратором (в нём нет секретов; отдаётся только при включённой интеграции)."""
    cfg = await _cfg(request, db)
    s = request.app.state.settings
    base = ol.public_url(s.app_public_url, cfg)
    if not cfg.enabled or not base.startswith("https://"):
        raise HTTPException(status_code=404, detail="Интеграция выключена или не задан публичный HTTPS-адрес.")
    return Response(ol.manifest_xml(base, s.app_version, sync_on_send=cfg.sync_on_send), media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="peregovorka-outlook-manifest.xml"', "Cache-Control": "no-store"})
