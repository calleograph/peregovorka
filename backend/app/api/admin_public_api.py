"""Администрирование публичного API: сервисные учётные записи, ключи (выпуск, ротация, отзыв), журнал обращений. Только администратор; секрет ключа показывается один раз."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import ApiClient, ApiKey, ApiRequestLog, Room, utcnow
from ..publicapi import keys as keylib
from ..publicapi.scopes import RATE_CLASSES, SCOPES, valid_scopes
from ..services.audit import write_audit

router = APIRouter(prefix="/admin/public-api", tags=["admin-public-api"])


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(None, max_length=1000)
    enabled: bool = True
    scopes: list[str] = Field(default_factory=list, max_length=len(SCOPES))
    rooms: list[uuid.UUID] | None = Field(None, max_length=1000, description="None — все комнаты")
    ip_allowlist: list[str] | None = Field(None, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Укажите название")
        return v

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        return valid_scopes(v)

    @field_validator("ip_allowlist")
    @classmethod
    def _ips(cls, v: list[str] | None) -> list[str] | None:
        if not v:
            return None
        try:
            return keylib.normalize_allowlist(v)
        except ValueError:
            raise ValueError("Адреса и сети — в формате 192.0.2.10 или 192.0.2.0/24") from None


class KeyIn(BaseModel):
    label: str | None = Field(None, max_length=120)
    expires_in_days: int | None = Field(None, ge=1, le=3650, description="None — бессрочно")
    grace_hours: int = Field(24, ge=0, le=24 * 30, description="Только для ротации: сколько часов прежний ключ продолжает работать")


def _key_out(k: ApiKey) -> dict:
    now = utcnow()
    state = "revoked" if k.revoked_at else ("expired" if k.expires_at and k.expires_at <= now else "active")
    return {"id": str(k.id), "key": keylib.masked(k.key_id, k.last4), "key_id": k.key_id, "label": k.label, "state": state, "expires_at": k.expires_at, "revoked_at": k.revoked_at,
            "last_used_at": k.last_used_at, "last_used_ip": k.last_used_ip, "created_at": k.created_at}


def _client_out(c: ApiClient) -> dict:
    return {"id": str(c.id), "name": c.name, "description": c.description, "enabled": c.enabled, "scopes": c.scopes or [], "rooms": c.rooms, "ip_allowlist": c.ip_allowlist,
            "created_by": c.created_by, "created_at": c.created_at, "updated_at": c.updated_at, "keys": [_key_out(k) for k in sorted(c.keys, key=lambda x: x.created_at, reverse=True)]}


async def _client(db: AsyncSession, cid: uuid.UUID) -> ApiClient:
    c = await db.get(ApiClient, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="Сервисная учётная запись не найдена")
    return c


async def _check_rooms(db: AsyncSession, rooms: list[uuid.UUID] | None) -> list[str] | None:
    if rooms is None:
        return None
    wanted = set(rooms)
    found = set((await db.execute(select(Room.id).where(Room.id.in_(wanted)))).scalars().all()) if wanted else set()
    if found != wanted:
        raise HTTPException(status_code=422, detail="Среди выбранных комнат есть несуществующие")
    return sorted(str(r) for r in wanted)


async def _audit(request: Request, db: AsyncSession, su: SessionUser, action: str, target: str, details: dict) -> None:
    details = json.loads(json.dumps(details, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)))     # даты — строками: колонка JSON их не принимает
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=action, target_type="api_client", target_id=target, ip=client_ip(request), details=details)


@router.get("/scopes")
async def scopes(su: SessionUser = Depends(require_admin)):
    return {"scopes": [{"name": k, "description": v} for k, v in SCOPES.items()], "rate_classes": list(RATE_CLASSES)}


@router.get("/clients")
async def list_clients(su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ApiClient).order_by(ApiClient.name))).scalars().unique().all()
    return [_client_out(c) for c in rows]


@router.post("/clients", status_code=201)
async def create_client(request: Request, body: ClientIn, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(func.count()).select_from(ApiClient).where(func.lower(ApiClient.name) == body.name.lower()))).scalar_one():
        raise HTTPException(status_code=409, detail="Учётная запись с таким названием уже есть")
    c = ApiClient(name=body.name, description=body.description, enabled=body.enabled, scopes=body.scopes, rooms=await _check_rooms(db, body.rooms),
                  ip_allowlist=body.ip_allowlist, created_by=su.display_name, keys=[])
    db.add(c)
    await db.flush()
    await _audit(request, db, su, "api_client.create", str(c.id), {"name": c.name, "scopes": c.scopes, "rooms": c.rooms, "ip_allowlist": c.ip_allowlist})
    await db.commit()
    return _client_out(c)


@router.patch("/clients/{client_id}")
async def update_client(client_id: uuid.UUID, request: Request, body: ClientIn, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    c = await _client(db, client_id)
    if body.name.lower() != c.name.lower() and (await db.execute(select(func.count()).select_from(ApiClient).where(func.lower(ApiClient.name) == body.name.lower()))).scalar_one():
        raise HTTPException(status_code=409, detail="Учётная запись с таким названием уже есть")
    before = {"scopes": c.scopes, "rooms": c.rooms, "ip_allowlist": c.ip_allowlist, "enabled": c.enabled}
    c.name, c.description, c.enabled, c.scopes = body.name, body.description, body.enabled, body.scopes
    c.rooms, c.ip_allowlist = await _check_rooms(db, body.rooms), body.ip_allowlist
    await _audit(request, db, su, "api_client.update", str(c.id), {"name": c.name, "before": before, "after": {"scopes": c.scopes, "rooms": c.rooms, "ip_allowlist": c.ip_allowlist, "enabled": c.enabled}})
    await db.commit()
    return _client_out(c)


@router.delete("/clients/{client_id}", status_code=204)
async def delete_client(client_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    c = await _client(db, client_id)
    await _audit(request, db, su, "api_client.delete", str(c.id), {"name": c.name, "keys": len(c.keys)})
    await db.delete(c)
    await db.commit()


@router.post("/clients/{client_id}/keys", status_code=201)
async def create_key(client_id: uuid.UUID, request: Request, body: KeyIn | None = None, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Выпуск ключа. Полный ключ есть только в этом ответе — в базе остаётся лишь хеш."""
    c = await _client(db, client_id)
    body = body or KeyIn()
    return await _issue(request, db, su, c, body.label, body.expires_in_days, "api_key.create")


async def _issue(request: Request, db: AsyncSession, su: SessionUser, c: ApiClient, label: str | None, days: int | None, action: str, extra: dict | None = None) -> dict:
    full, key_id, digest, last4 = keylib.new_key()
    k = ApiKey(client_id=c.id, key_id=key_id, secret_hash=digest, last4=last4, label=(label or "").strip() or None, expires_at=utcnow() + timedelta(days=days) if days else None)
    db.add(k)
    await db.flush()
    await _audit(request, db, su, action, str(c.id), {"client": c.name, "key_id": key_id, "label": k.label, "expires_at": k.expires_at, **(extra or {})})
    await db.commit()
    return {**_key_out(k), "secret": full, "note": "Сохраните ключ сейчас: позже он не будет показан."}


async def _key(db: AsyncSession, key_pk: uuid.UUID) -> ApiKey:
    k = await db.get(ApiKey, key_pk)
    if k is None:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    return k


@router.post("/keys/{key_pk}/rotate", status_code=201)
async def rotate_key(key_pk: uuid.UUID, request: Request, body: KeyIn | None = None, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Ротация без простоя: выпускается новый ключ, прежний работает ещё `grace_hours` часов (по умолчанию 24) и затем перестаёт действовать."""
    body = body or KeyIn()
    old = await _key(db, key_pk)
    if old.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Ключ уже отозван")
    c = old.client
    until = utcnow() + timedelta(hours=body.grace_hours)
    if old.expires_at is None or old.expires_at > until:
        old.expires_at = until
    return await _issue(request, db, su, c, body.label or old.label, body.expires_in_days, "api_key.rotate", {"replaces": old.key_id, "old_valid_until": old.expires_at})


@router.post("/keys/{key_pk}/revoke", status_code=204)
async def revoke_key(key_pk: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    k = await _key(db, key_pk)
    if k.revoked_at is None:
        k.revoked_at = utcnow()
        await _audit(request, db, su, "api_key.revoke", str(k.client_id), {"client": k.client.name, "key_id": k.key_id})
        await db.commit()


@router.get("/log")
async def request_log(client_id: uuid.UUID | None = None, status_from: int | None = Query(None, ge=100, le=599), before_id: int | None = Query(None, ge=1),
                      limit: int = Query(100, ge=1, le=500), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Журнал обращений (новые первыми); следующая страница — `before_id` = id последней строки."""
    stmt = select(ApiRequestLog).order_by(ApiRequestLog.id.desc()).limit(limit)
    if client_id:
        stmt = stmt.where(ApiRequestLog.client_id == client_id)
    if status_from:
        stmt = stmt.where(ApiRequestLog.status >= status_from)
    if before_id:
        stmt = stmt.where(ApiRequestLog.id < before_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [{"id": r.id, "at": r.at, "client_id": str(r.client_id) if r.client_id else None, "key_id": r.key_id, "method": r.method, "path": r.path, "status": r.status, "ms": r.ms,
             "ip": r.ip, "request_id": r.request_id, "error_code": r.error_code} for r in rows]
