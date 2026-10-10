"""Проверка ключа API: подлинность → состояние ключа и учётной записи → сеть → право (scope) → ограничение частоты.

Порядок важен: сведения о состоянии ключа (отозван, истёк, адрес не разрешён) сообщаются только тому, кто предъявил верный секрет;
на неверный ключ всегда один и тот же ответ.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import client_ip, get_db
from ..models import ApiKey, utcnow
from .errors import ApiError
from .keys import ip_allowed, parse_key, secret_matches
from .scopes import RATE_CLASSES, SCOPES

_CFG_TTL = 5.0
_DUMMY_HASH = "0" * 64
BEARER = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class Principal:
    client_id: uuid.UUID
    client_name: str
    key_id: str
    scopes: frozenset[str]
    rooms: frozenset[str] | None             # None — все комнаты
    key_expires_at: object | None = None

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    def allows_room(self, room_id: uuid.UUID) -> bool:
        return self.rooms is None or str(room_id) in self.rooms


async def api_config(request: Request, db: AsyncSession):
    """Настройки публичного API с коротким кэшем (читаются на каждый запрос, а менять их приходится редко)."""
    cache = getattr(request.app.state, "api_cfg_cache", None)
    now = time.monotonic()
    if cache and now - cache[0] < _CFG_TTL:
        return cache[1]
    cfg = await request.app.state.settings_svc.get(db, "api")
    request.app.state.api_cfg_cache = (now, cfg)
    return cfg


def invalidate_config(app) -> None:
    app.state.api_cfg_cache = None


async def _rate(request: Request, cfg, p: Principal, cls: str) -> None:
    limit = int(getattr(cfg, f"rate_{cls}"))
    window = 60
    now = int(time.time())
    reset = window - now % window
    try:
        key = f"pubapi:rl:{p.client_id.hex}:{cls}:{now // window}"
        redis = request.app.state.redis
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, window + 1)
    except Exception:  # noqa: BLE001 — сбой Redis не отключает API: ограничение защищает, а не обеспечивает работу
        return
    request.state.rate_headers = {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(max(0, limit - n)), "X-RateLimit-Reset": str(reset), "X-RateLimit-Class": cls}
    if n > limit:
        raise ApiError(429, "rate_limited", f"Превышен лимит запросов ({limit} в минуту, класс «{cls}»). Повторите через {reset} с.",
                       headers={**request.state.rate_headers, "Retry-After": str(reset)})


class Access:
    """Зависимость маршрута: `Depends(Access("meetings:read"))` или `Access(None)` — только подлинность (например, GET /me)."""

    def __init__(self, scope: str | None, rate: str = "read"):
        assert scope is None or scope in SCOPES, scope
        assert rate in RATE_CLASSES, rate
        self.scope, self.rate = scope, rate

    async def __call__(self, request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
        cfg = await api_config(request, db)
        if not cfg.enabled:
            raise ApiError(503, "api_disabled", "Публичный API выключен администратором.")
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise ApiError(401, "unauthorized", "Требуется ключ API: заголовок «Authorization: Bearer pgk_…».", headers=BEARER)
        parsed = parse_key(token.strip())
        key = (await db.execute(select(ApiKey).where(ApiKey.key_id == parsed[0]))).scalars().first() if parsed else None
        if parsed is not None and key is None:
            secret_matches(parsed[1], _DUMMY_HASH)      # то же время ответа, что и при неверном секрете: по задержке не узнать, существует ли идентификатор ключа
        if parsed is None or key is None or not secret_matches(parsed[1], key.secret_hash):
            raise ApiError(401, "invalid_api_key", "Ключ API недействителен.", headers=BEARER)
        now = utcnow()
        client = key.client
        if key.revoked_at is not None:
            raise ApiError(401, "key_revoked", "Ключ API отозван.", headers=BEARER)
        if key.expires_at is not None and key.expires_at <= now:
            raise ApiError(401, "key_expired", "Срок действия ключа API истёк.", headers=BEARER)
        if not client.enabled:
            raise ApiError(403, "client_disabled", "Сервисная учётная запись отключена.")
        ip = client_ip(request)
        if not ip_allowed(ip, client.ip_allowlist):
            raise ApiError(403, "ip_not_allowed", "Обращения с этого адреса для данного ключа запрещены.")
        p = Principal(client.id, client.name, key.key_id, frozenset(client.scopes or []), frozenset(client.rooms) if client.rooms is not None else None, key.expires_at)
        request.state.principal = p
        if self.scope and not p.has(self.scope):
            raise ApiError(403, "insufficient_scope", f"Не хватает права «{self.scope}».", extra={"required_scope": self.scope})
        await _rate(request, cfg, p, self.rate)
        await _touch(request, db, key, ip, now)
        return p


async def _touch(request: Request, db: AsyncSession, key: ApiKey, ip: str, now) -> None:
    """Отметка «последнее использование» — не чаще раза в минуту на ключ (иначе запись в БД на каждый запрос)."""
    try:
        if await request.app.state.redis.set(f"pubapi:seen:{key.key_id}", "1", nx=True, ex=60):
            key.last_used_at, key.last_used_ip = now, ip[:64]
            await db.commit()
    except Exception:  # noqa: BLE001 — отметка не должна ломать запрос
        await db.rollback()
