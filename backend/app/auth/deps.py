"""FastAPI-зависимости: сессия, роли, CSRF, IP клиента, состояние приложения."""
from __future__ import annotations

import hmac
import re
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from .sessions import SessionData, SessionStore

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
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

    @classmethod
    def from_session(cls, sid: str, data: SessionData) -> "SessionUser":
        return cls(sid, uuid.UUID(data.user_id), data.ad_guid, data.sam_account_name, data.display_name,
                   data.is_admin, frozenset(data.groups), data.csrf)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request):
    async with request.app.state.session_maker() as session:
        yield session


def client_ip(request: Request) -> str:
    """Адрес клиента с учётом TRUSTED_PROXY_HOPS (число прокси перед web-контейнером)."""
    settings: Settings = request.app.state.settings
    xff = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts and len(parts) >= settings.trusted_proxy_hops:
        candidate = parts[-settings.trusted_proxy_hops]
        if _IP_RE.match(candidate):
            return candidate
    return request.client.host if request.client else "unknown"


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
