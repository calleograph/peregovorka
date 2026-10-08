"""Серверные сессии в Redis. В cookie — только случайный идентификатор.

Портировано по идее из legacy/php/src/Security/SessionManager.php (HttpOnly/Secure,
idle- и абсолютный таймауты), но состояние хранится в Redis, а не в файлах.
В Redis ключом служит sha256(session_id) — дамп Redis не даёт готовых cookie.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

from ..config import Settings


@dataclass
class SessionData:
    user_id: str
    ad_guid: str
    sam_account_name: str
    display_name: str
    is_admin: bool
    groups: list[str]  # DN в нижнем регистре
    csrf: str
    created_at: float
    last_seen: float
    local: bool = False            # локальный (аварийный) администратор
    must_change: bool = False      # нужно сменить первичный/сброшенный пароль


def _key(session_id: str) -> str:
    return "sess:" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings):
        self._r = redis
        self._s = settings

    async def create(self, *, user_id: str, ad_guid: str, sam: str, display_name: str,
                     is_admin: bool, groups: list[str], local: bool = False, must_change: bool = False) -> tuple[str, SessionData]:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        data = SessionData(user_id, ad_guid, sam, display_name, is_admin, groups,
                           secrets.token_urlsafe(24), now, now, local=local, must_change=must_change)
        await self._r.set(_key(sid), json.dumps(asdict(data), ensure_ascii=False), ex=self._s.session_idle_timeout_seconds)
        if local:   # индекс сессий локального администратора: сброс пароля завершает их все
            await self._r.sadd(f"usess:{user_id}", _key(sid))
            await self._r.expire(f"usess:{user_id}", self._s.session_absolute_timeout_seconds)
        return sid, data

    async def update(self, session_id: str, **changes) -> None:
        raw = await self._r.get(_key(session_id))
        if raw is None:
            return
        d = json.loads(raw)
        d.update(changes)
        ttl = await self._r.ttl(_key(session_id))
        await self._r.set(_key(session_id), json.dumps(d, ensure_ascii=False), ex=ttl if ttl and ttl > 0 else self._s.session_idle_timeout_seconds)

    async def destroy_user(self, user_id: str, *, keep: str | None = None) -> int:
        """Завершает все сессии локального пользователя (кроме `keep`)."""
        keys = await self._r.smembers(f"usess:{user_id}")
        keep_key = _key(keep) if keep else None
        n = 0
        for k in keys:
            if k != keep_key:
                n += await self._r.delete(k)
                await self._r.srem(f"usess:{user_id}", k)
        return n

    async def get(self, session_id: str | None, *, touch: bool = True) -> SessionData | None:
        if not session_id or len(session_id) > 200:
            return None
        raw = await self._r.get(_key(session_id))
        if raw is None:
            return None
        data = SessionData(**json.loads(raw))
        now = time.time()
        if now - data.created_at > self._s.session_absolute_timeout_seconds or \
                now - data.last_seen > self._s.session_idle_timeout_seconds:
            await self._r.delete(_key(session_id))
            return None
        if touch and now - data.last_seen > 30:  # не пишем в Redis на каждый запрос
            data.last_seen = now
            remaining = int(self._s.session_absolute_timeout_seconds - (now - data.created_at))
            ttl = max(1, min(self._s.session_idle_timeout_seconds, remaining))
            await self._r.set(_key(session_id), json.dumps(asdict(data), ensure_ascii=False), ex=ttl)
        return data

    async def destroy(self, session_id: str | None) -> None:
        if session_id:
            await self._r.delete(_key(session_id))
