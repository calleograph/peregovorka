"""Гостевой доступ: сессия гостя и «актор» (пользователь AD либо гость) для эндпоинтов встречи.

Гость — отдельный тип участника (`participant_type=guest`), а не фиктивный пользователь AD. Он получает короткоживущую сессию, привязанную к
ОДНОЙ встрече; токен сессии хранится в Redis по хешу и передаётся заголовком `X-Guest-Token` (в cookie не кладётся — иначе вход гостем в том же
браузере затёр бы сессию AD). Гостю недоступно всё, что требует `require_user` (список комнат, история, админка): он работает только с
эндпоинтами встречи через `require_actor`.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from dataclasses import asdict, dataclass

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from .deps import SessionUser, _origin_ok, current_session

GUEST_TTL_SECONDS = 12 * 3600
GUEST_HEADER = "x-guest-token"


@dataclass
class GuestSession:
    guest_id: str
    meeting_id: str
    room_id: str
    display_name: str
    created_at: float


def _key(token: str) -> str:
    return "guest:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


class GuestSessionStore:
    def __init__(self, redis: Redis, ttl: int = GUEST_TTL_SECONDS):
        self._r, self._ttl = redis, ttl

    async def create(self, *, guest_id: uuid.UUID, meeting_id: uuid.UUID, room_id: uuid.UUID, display_name: str) -> str:
        token = secrets.token_urlsafe(32)
        data = GuestSession(str(guest_id), str(meeting_id), str(room_id), display_name, time.time())
        await self._r.set(_key(token), json.dumps(asdict(data), ensure_ascii=False), ex=self._ttl)
        return token

    async def get(self, token: str | None) -> GuestSession | None:
        if not token or len(token) > 200:
            return None
        raw = await self._r.get(_key(token))
        return GuestSession(**json.loads(raw)) if raw else None

    async def destroy(self, token: str | None) -> None:
        if token:
            await self._r.delete(_key(token))


@dataclass(frozen=True)
class Actor:
    """Тот, кто обращается к встрече: пользователь AD (`user`) или гость (`guest`)."""

    kind: str                      # "user" | "guest"
    id: uuid.UUID
    display_name: str
    is_admin: bool
    user: SessionUser | None = None
    guest: GuestSession | None = None

    @property
    def is_guest(self) -> bool:
        return self.kind == "guest"

    @property
    def label(self) -> str:
        """Имя для показа другим участникам: у гостя — с пометкой."""
        return f"{self.display_name} (гость)" if self.is_guest else self.display_name

    @property
    def login(self) -> str:
        return self.user.sam_account_name if self.user else f"guest:{self.display_name}"


async def current_actor(request: Request) -> Actor | None:
    """Явный гостевой токен в заголовке имеет приоритет над cookie AD (гостевая вкладка в браузере, где открыт и AD-вход)."""
    token = request.headers.get(GUEST_HEADER)
    if token:
        g = await request.app.state.guest_sessions.get(token)
        if g is None or await request.app.state.redis.exists(f"guest:revoked:{g.guest_id}"):
            return None  # сессии нет или гостевая ссылка отозвана — гость отключён
        return Actor("guest", uuid.UUID(g.guest_id), g.display_name, False, guest=g)
    su = await current_session(request)
    if su is None:
        return None
    return Actor("user", su.user_id, su.display_name, su.is_admin, user=su)


async def require_actor(request: Request) -> Actor:
    actor = await current_actor(request)
    if actor is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not _origin_ok(request):
            raise HTTPException(status_code=403, detail="Недопустимый Origin")
        if actor.user is not None:
            import hmac  # noqa: PLC0415

            token = request.headers.get("x-csrf-token", "")
            if not token or not hmac.compare_digest(token, actor.user.csrf):
                raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
        # гость: сам заголовок X-Guest-Token (его нельзя подставить из чужого сайта) играет роль CSRF-токена
    return actor
