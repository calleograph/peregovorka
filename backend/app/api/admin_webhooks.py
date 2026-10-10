"""Администрирование подписок на события публичного API (webhooks): получатели, секреты (выпуск и ротация), проверка, история доставок, ручной повтор. Только администратор.
Секрет подписи показывается один раз; в списках его нет. Все действия пишутся в журнал аудита."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import WebhookDelivery, WebhookEndpoint, utcnow
from ..publicapi.ssrf import UrlRejected, validate_url
from ..publicapi.webhooks import EVENTS, WebhookError, new_secret
from ..services.audit import write_audit
from .admin_public_api import _check_rooms

router = APIRouter(prefix="/admin/public-api", tags=["admin-public-api"])


class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=500)
    enabled: bool = True
    events: list[str] = Field(default_factory=list, max_length=len(EVENTS), description="Пусто — все события (кроме проверочного).")
    rooms: list[uuid.UUID] | None = Field(None, max_length=1000, description="None — все комнаты")

    @field_validator("name")
    @classmethod
    def _n(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Укажите название")
        return v

    @field_validator("events")
    @classmethod
    def _e(cls, v: list[str]) -> list[str]:
        bad = [e for e in v if e not in EVENTS or e == "webhook.test"]
        if bad:
            raise ValueError("Неизвестные события: " + ", ".join(bad))
        return sorted(set(v))


class RotateIn(BaseModel):
    grace_hours: int = Field(24, ge=0, le=24 * 14, description="Сколько часов прежний секрет ещё подписывает события (получатель успевает перейти).")


def _out(e: WebhookEndpoint) -> dict:
    return {"id": str(e.id), "name": e.name, "url": e.url, "enabled": e.enabled, "status": e.status if e.enabled else "disabled", "events": e.events or [], "rooms": e.rooms,
            "consecutive_failures": e.consecutive_failures, "last_success_at": e.last_success_at, "last_failure_at": e.last_failure_at, "last_error": e.last_error,
            "disabled_reason": e.disabled_reason, "secret_set": bool(e.secret_enc), "previous_secret_until": e.previous_until, "created_by": e.created_by, "created_at": e.created_at}


async def _check_url(request: Request, db: AsyncSession, url: str) -> None:
    cfg = await request.app.state.settings_svc.get(db, "api")
    try:
        await validate_url(url, allow_http=cfg.webhook_allow_http, allow_text=cfg.webhook_allow_hosts, resolver=request.app.state.webhooks._resolver)     # type: ignore[attr-defined]
    except UrlRejected as exc:
        raise HTTPException(status_code=422, detail=f"Адрес получателя не принят: {exc}") from None


async def _get(db: AsyncSession, eid: uuid.UUID) -> WebhookEndpoint:
    e = await db.get(WebhookEndpoint, eid)
    if e is None:
        raise HTTPException(status_code=404, detail="Получатель не найден")
    return e


async def _audit(request: Request, db: AsyncSession, su: SessionUser, action: str, e: WebhookEndpoint, details: dict | None = None) -> None:
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=action, target_type="webhook", target_id=str(e.id), ip=client_ip(request),
                      details=json.loads(json.dumps({"name": e.name, **(details or {})}, default=str)))


@router.get("/webhook-events")
async def webhook_events(su: SessionUser = Depends(require_admin)):
    return [{"name": k, "description": v} for k, v in EVENTS.items() if k != "webhook.test"]


@router.get("/webhooks")
async def list_webhooks(su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(WebhookEndpoint).order_by(WebhookEndpoint.name))).scalars().all()
    return [_out(e) for e in rows]


@router.post("/webhooks", status_code=201)
async def create_webhook(request: Request, body: WebhookIn, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Создание получателя. Адрес проверяется политикой SSRF; секрет подписи показывается один раз — в этом ответе."""
    svc = request.app.state.webhooks
    await _check_url(request, db, body.url)
    secret = new_secret()
    e = WebhookEndpoint(name=body.name, url=body.url, enabled=body.enabled, events=body.events, rooms=await _check_rooms(db, body.rooms), created_by=su.display_name)
    db.add(e)
    await db.flush()
    try:
        e.secret_enc = svc.encrypt(e.id, secret)
    except WebhookError as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from None
    await _audit(request, db, su, "webhook.create", e, {"host": body.url.split("/")[2] if "//" in body.url else "", "events": e.events, "rooms": e.rooms})
    await db.commit()
    return {**_out(e), "secret": secret, "note": "Сохраните секрет сейчас: позже он не будет показан. Им проверяется подпись событий."}


@router.patch("/webhooks/{endpoint_id}")
async def update_webhook(endpoint_id: uuid.UUID, request: Request, body: WebhookIn, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    e = await _get(db, endpoint_id)
    if body.url != e.url:
        await _check_url(request, db, body.url)
    e.name, e.url, e.events, e.rooms, e.enabled = body.name, body.url, body.events, await _check_rooms(db, body.rooms), body.enabled
    await _audit(request, db, su, "webhook.update", e, {"events": e.events, "rooms": e.rooms, "enabled": e.enabled})
    await db.commit()
    return _out(e)


@router.delete("/webhooks/{endpoint_id}", status_code=204)
async def delete_webhook(endpoint_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    e = await _get(db, endpoint_id)
    await _audit(request, db, su, "webhook.delete", e)
    await db.delete(e)
    await db.commit()


@router.post("/webhooks/{endpoint_id}/rotate-secret", status_code=201)
async def rotate_secret(endpoint_id: uuid.UUID, request: Request, body: RotateIn | None = None, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Ротация без простоя: новый секрет показывается один раз; прежний ещё `grace_hours` часов подписывает события вторым значением в заголовке."""
    body = body or RotateIn()
    svc = request.app.state.webhooks
    e = await _get(db, endpoint_id)
    secret = new_secret()
    try:
        e.previous_secret_enc, e.previous_until = (e.secret_enc, utcnow() + timedelta(hours=body.grace_hours)) if body.grace_hours else (None, None)
        e.secret_enc = svc.encrypt(e.id, secret)
    except WebhookError as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from None
    await _audit(request, db, su, "webhook.rotate_secret", e, {"grace_hours": body.grace_hours})
    await db.commit()
    return {**_out(e), "secret": secret, "note": "Сохраните секрет сейчас. Прежний продолжает подписывать события до окончания окна перехода."}


@router.post("/webhooks/{endpoint_id}/enable")
async def enable_webhook(endpoint_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Ручное включение после отключения (в том числе автоматического): сбрасывает счётчик неудач; накопленные ожидающие события будут отправлены."""
    e = await _get(db, endpoint_id)
    e.enabled, e.status, e.consecutive_failures, e.disabled_reason = True, "active", 0, None
    await _audit(request, db, su, "webhook.enable", e)
    await db.commit()
    request.app.state.webhooks._wake.set()
    return _out(e)


@router.post("/webhooks/{endpoint_id}/test")
async def test_webhook(endpoint_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Отправляет проверочное событие `webhook.test` сразу и возвращает результат (повторов у него нет)."""
    e = await _get(db, endpoint_id)
    res = await request.app.state.webhooks.send_test(e.id)
    await db.refresh(e)
    await _audit(request, db, su, "webhook.test", e, {"ok": res["ok"], "status": res["status"]})
    await db.commit()
    return res


@router.get("/webhooks/{endpoint_id}/deliveries")
async def deliveries(endpoint_id: uuid.UUID, status: str | None = Query(None, pattern="^(pending|delivered|failed)$"), before: str | None = Query(None, max_length=80),
                     limit: int = Query(50, ge=1, le=200), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """История доставок (новые первыми). Следующая страница — `before` = `created_at` последней строки."""
    await _get(db, endpoint_id)
    stmt = select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint_id).order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(WebhookDelivery.status == status)
    if before:
        try:
            stmt = stmt.where(WebhookDelivery.created_at < datetime.fromisoformat(before))
        except ValueError:
            raise HTTPException(status_code=422, detail="before: дата ISO 8601") from None
    rows = (await db.execute(stmt)).scalars().all()
    return [{"id": str(d.id), "event_id": d.event_id, "event_type": d.event_type, "status": d.status, "attempts": d.attempts, "manual_retries": d.manual_retries,
             "next_attempt_at": d.next_attempt_at if d.status == "pending" else None, "last_status": d.last_status, "last_error": d.last_error, "attempt_log": d.attempt_log or [],
             "created_at": d.created_at, "delivered_at": d.delivered_at} for d in rows]


@router.post("/webhooks/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Ручная повторная отправка (тот же `event_id`, новая метка времени и подпись). Отключённому получателю — сначала включите его."""
    d = await db.get(WebhookDelivery, delivery_id)
    e = await db.get(WebhookEndpoint, d.endpoint_id) if d else None
    if d is None or e is None:
        raise HTTPException(status_code=404, detail="Доставка не найдена")
    if e.status == "disabled" or not e.enabled:
        raise HTTPException(status_code=409, detail="Получатель отключён: сначала включите его")
    await request.app.state.webhooks.retry(db, delivery_id)
    await _audit(request, db, su, "webhook.retry", e, {"delivery": str(delivery_id), "event_id": d.event_id})
    await db.commit()
    request.app.state.webhooks._wake.set()
    return {"ok": True}
