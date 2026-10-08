"""FastAPI-зависимости: сессия, роли, CSRF, IP клиента, состояние приложения."""
from __future__ import annotations

import hmac
import ipaddress
import re
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from .sessions import SessionData, SessionStore

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_ALLOWED_WHILE_CHANGING = {"/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/change-password"}
_IP_RE = re.compile(r"^[0-9a-fA-F:.]{2,45}$")


@dataclass(frozen=True)
class SessionUser:
    session_id: str
    user_id: uuid.UUID
    ad_guid: str
    sam_account_name: str
    display_name: str
    is_admin: bool
    groups: frozenset[str]
    csrf: str
    local: bool = False
    must_change: bool = False

    @classmethod
    def from_session(cls, sid: str, data: SessionData) -> "SessionUser":
        return cls(sid, uuid.UUID(data.user_id), data.ad_guid, data.sam_account_name, data.display_name,
                   data.is_admin, frozenset(data.groups), data.csrf, data.local, data.must_change)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request):
    async with request.app.state.session_maker() as session:
        yield session


def parse_networks(raw: str) -> list:
    out = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue  # некорректная запись в настройке не должна ронять запросы
    return out


def pick_client_ip(xff: str, peer: str | None, trusted: list, hops: int = 1) -> str:
    """Адрес клиента из цепочки X-Forwarded-For.

    Идём справа налево (подделать можно только левую часть цепочки): пропускаем адреса доверенных прокси (`TRUSTED_PROXY_CIDRS`, по умолчанию
    loopback — например, локальный TLS-терминатор, который иначе подставлял бы 127.0.0.1 вместо клиента), затем ещё `hops-1` прокси, которые
    не удалось описать сетью (прежний смысл TRUSTED_PROXY_HOPS). Первый оставшийся адрес — клиент. Нет цепочки — адрес TCP-соединения.
    """
    chain: list[str] = []
    for p in (x.strip() for x in (xff or "").split(",")):
        if p and _IP_RE.match(p):
            try:
                ipaddress.ip_address(p)
            except ValueError:
                continue
            chain.append(p)
    if not chain:
        return peer or "unknown"
    extra = max(0, hops - 1)
    for i in range(len(chain) - 1, -1, -1):
        addr = ipaddress.ip_address(chain[i])
        if any(addr in n for n in trusted):
            continue
        if extra > 0:
            extra -= 1
            continue
        return chain[i]
    return chain[0]  # вся цепочка из доверенных прокси — самый левый адрес


def client_ip(request: Request) -> str:
    """Адрес клиента (см. pick_client_ip); настройки — TRUSTED_PROXY_CIDRS и TRUSTED_PROXY_HOPS."""
    settings: Settings = request.app.state.settings
    nets = getattr(request.app.state, "trusted_nets", None)
    if nets is None:
        nets = request.app.state.trusted_nets = parse_networks(settings.trusted_proxy_cidrs)
    return pick_client_ip(request.headers.get("x-forwarded-for", ""), request.client.host if request.client else None, nets, settings.trusted_proxy_hops)


def _origin_ok(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin is None:
        return True  # не браузерный запрос; CSRF-токен всё равно обязателен
    return origin.rstrip("/") == settings.public_origin


async def current_session(request: Request) -> SessionUser | None:
    settings: Settings = request.app.state.settings
    sid = request.cookies.get(settings.cookie_name)
    store: SessionStore = request.app.state.sessions
    data = await store.get(sid)
    if data is None or sid is None:
        return None
    if await request.app.state.redis.exists(f"user:inactive:{data.user_id}"):
        await store.destroy(sid)  # администратор отключил пользователя — сессия прекращается
        return None
    return SessionUser.from_session(sid, data)


async def require_user(request: Request) -> SessionUser:
    su = await current_session(request)
    if su is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if su.must_change and request.url.path not in _ALLOWED_WHILE_CHANGING:
        # первичный/сброшенный пароль нужно сменить до любых других действий
        raise HTTPException(status_code=403, detail={"code": "password_change_required", "message": "Сначала смените пароль."})
    if request.method not in _SAFE_METHODS:
        if not _origin_ok(request):
            raise HTTPException(status_code=403, detail="Недопустимый Origin")
        token = request.headers.get("x-csrf-token", "")
        if not token or not hmac.compare_digest(token, su.csrf):
            raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    return su


async def require_admin(su: SessionUser = Depends(require_user)) -> SessionUser:
    if not su.is_admin:
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return su


async def require_internal(request: Request) -> None:
    """Внутренние вызовы (smoke-test, сервисы): Bearer INTERNAL_API_TOKEN."""
    settings: Settings = request.app.state.settings
    expected = settings.internal_api_token
    header = request.headers.get("authorization", "")
    given = header[7:] if header.lower().startswith("bearer ") else ""
    if not expected or not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="Недействительный сервисный токен")


DbSession = AsyncSession
