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
    must_totp: bool = False        # политика требует 2FA, а пользователь её ещё не настроил: до настройки доступны только /auth/totp/*
    device: str = ""               # «Chrome 130 · Windows 10/11 (компьютер)» — подпись в списке «Активные устройства»
    ip: str = ""                   # адрес входа (для списка устройств)
    pub: str = ""                  # публичный номер сессии: по нему сессию показывают и завершают; сам идентификатор сессии (cookie) наружу не отдаётся


def _key(session_id: str) -> str:
    return "sess:" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def pub_of(key: str) -> str:
    """Публичный номер сессии из её ключа в Redis: необратим и не годится как cookie."""
    return key[5:17]


class SessionLimit(Exception):
    """У учётной записи уже максимум одновременных сессий. Существующие НЕ завершаются; `sessions` — их краткое описание для выбора, какую завершить."""

    def __init__(self, sessions: list[dict], limit: int):
        super().__init__("session_limit")
        self.sessions, self.limit = sessions, limit


TRUST_PREFIX = "pgd_"


def _tkey(token: str) -> str:
    return "dtrust:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings):
        self._r = redis
        self._s = settings

    @staticmethod
    def _idx(user_id: str) -> str:
        return f"uidx:{user_id}"

    async def list_user(self, user_id: str, current: str | None = None) -> list[dict]:
        """Действующие сессии пользователя (устаревшие записи индекса вычищаются): для «Активные устройства» и для проверки лимита."""
        cur_key = _key(current) if current else None
        out = []
        for k in await self._r.zrange(self._idx(user_id), 0, -1):
            raw = await self._r.get(k)
            if raw is None:
                await self._r.zrem(self._idx(user_id), k)
                continue
            d = json.loads(raw)
            out.append({"id": pub_of(k), "device": d.get("device") or "неизвестное устройство", "ip": d.get("ip") or "", "created_at": d.get("created_at"),
                        "last_seen": d.get("last_seen"), "current": k == cur_key, "_key": k})
        return out

    async def create(self, *, user_id: str, ad_guid: str, sam: str, display_name: str,
                     is_admin: bool, groups: list[str], local: bool = False, must_change: bool = False, must_totp: bool = False,
                     device: str = "", ip: str = "", limit: int = 0, free: str | None = None) -> tuple[str, SessionData]:
        """Новая сессия. `limit` — сколько сессий допустимо одновременно (0 — без ограничения). Если места нет, существующие сессии не трогаются: SessionLimit; завершить одну из них
        можно заранее (`free` — публичный номер сессии этого же пользователя) — только после подтверждения личности (пароль и 2FA уже проверены вызывающим)."""
        uid = str(user_id)
        if free:
            await self.destroy_pub(uid, free)
        sid = secrets.token_urlsafe(32)
        now = time.time()
        k = _key(sid)
        data = SessionData(user_id, ad_guid, sam, display_name, is_admin, groups,
                           secrets.token_urlsafe(24), now, now, local=local, must_change=must_change, must_totp=must_totp, device=device[:120], ip=ip[:64], pub=pub_of(k))
        await self._r.set(k, json.dumps(asdict(data), ensure_ascii=False), ex=self._s.session_idle_timeout_seconds)
        await self._r.zadd(self._idx(uid), {k: now})
        await self._r.expire(self._idx(uid), self._s.session_absolute_timeout_seconds)
        if local:   # прежний индекс локального администратора (сброс пароля завершает все его сессии) — оставлен для совместимости
            await self._r.sadd(f"usess:{user_id}", k)
            await self._r.expire(f"usess:{user_id}", self._s.session_absolute_timeout_seconds)
        if limit > 0:
            # проверка ПОСЛЕ записи: два одновременных входа не проскочат оба; проигравший откатывается, чужие сессии не затрагиваются
            live = await self.list_user(uid)
            if len(live) > limit:
                await self._r.delete(k)
                await self._r.zrem(self._idx(uid), k)
                raise SessionLimit([{kk: v for kk, v in x.items() if kk != "_key"} for x in live if x["_key"] != k], limit)
        return sid, data

    async def destroy_pub(self, user_id: str, pub: str) -> bool:
        """Завершает сессию пользователя по публичному номеру (чужую завершить нельзя: ищется только среди сессий этого пользователя)."""
        for x in await self.list_user(user_id):
            if x["id"] == pub:
                await self._r.delete(x["_key"])
                await self._r.zrem(self._idx(user_id), x["_key"])
                await self._r.srem(f"usess:{user_id}", x["_key"])
                await self.trust_revoke(user_id, pub=pub)
                return True
        return False

    async def update(self, session_id: str, **changes) -> None:
        raw = await self._r.get(_key(session_id))
        if raw is None:
            return
        d = json.loads(raw)
        d.update(changes)
        ttl = await self._r.ttl(_key(session_id))
        await self._r.set(_key(session_id), json.dumps(d, ensure_ascii=False), ex=ttl if ttl and ttl > 0 else self._s.session_idle_timeout_seconds)

    async def destroy_user(self, user_id: str, *, keep: str | None = None) -> int:
        """Завершает все сессии пользователя (кроме `keep`): смена пароля, отключение учётной записи, принудительный выход администратором."""
        keep_key = _key(keep) if keep else None
        await self.trust_revoke(user_id, keep_pub=pub_of(keep_key) if keep_key else None)
        keys = set(await self._r.smembers(f"usess:{user_id}")) | set(await self._r.zrange(self._idx(user_id), 0, -1))
        n = 0
        for k in keys:
            if k != keep_key:
                n += await self._r.delete(k)
                await self._r.srem(f"usess:{user_id}", k)
                await self._r.zrem(self._idx(user_id), k)
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
        """Обычный выход: завершается ТОЛЬКО эта сессия, остальные устройства пользователя продолжают работать."""
        if session_id:
            k = _key(session_id)
            raw = await self._r.get(k)
            await self._r.delete(k)
            if raw:
                uid = json.loads(raw).get("user_id")
                if uid:
                    await self._r.zrem(self._idx(uid), k)
                    await self.trust_revoke(uid, pub=pub_of(k))       # выход на устройстве отзывает и его доверие

    # ------------------------------------------------------------------ доверенное устройство (настольный клиент)
    # Клиент Windows не хранит пароль: после обычного входа он получает одноразовый токен устройства (в Redis — только его хэш) и при следующем запуске
    # обменивает его на новую сессию и новый токен. Токен привязан к сессии (`pub`): её завершение, выход, смена пароля, отключение учётной записи отзывают его.
    # Срок — от входа ПАРОЛЕМ (`born`), продление токена срок не сдвигает.
    async def trust_issue(self, user_id: str, pub: str, *, days: int, params: dict, born: float | None = None, pwd: str | None = None) -> tuple[str, float]:
        uid = str(user_id)
        await self.trust_revoke(uid, pub=pub)                                  # одна запись на сессию: повторная выдача заменяет прежнюю
        born = born or time.time()
        exp = born + days * 86400
        ttl = int(exp - time.time())
        if ttl < 60:
            raise ValueError("trust_expired")
        token = TRUST_PREFIX + secrets.token_urlsafe(32)
        k = _tkey(token)
        await self._r.set(k, json.dumps({"uid": uid, "pub": pub, "born": born, "exp": exp, "params": params, "pwd": pwd}, ensure_ascii=False), ex=ttl)
        await self._r.hset(f"dtrust:u:{uid}", k, pub)
        await self._r.expire(f"dtrust:u:{uid}", max(ttl, await self._r.ttl(f"dtrust:u:{uid}") or 0))
        return token, exp

    async def trust_get(self, token: str) -> dict | None:
        if not isinstance(token, str) or not token.startswith(TRUST_PREFIX) or len(token) > 100:
            return None
        raw = await self._r.get(_tkey(token))
        if raw is None:
            return None
        d = json.loads(raw)
        return d if float(d.get("exp") or 0) > time.time() else None

    async def trust_claim(self, token: str) -> dict | None:
        """Забрать запись токена атомарно (GETDEL): два одновременных возобновления по одному токену не откроют две сессии."""
        if not isinstance(token, str) or not token.startswith(TRUST_PREFIX) or len(token) > 100:
            return None
        raw = await self._r.getdel(_tkey(token))
        if raw is None:
            return None
        d = json.loads(raw)
        await self._r.hdel(f"dtrust:u:{d['uid']}", _tkey(token))
        return d if float(d.get("exp") or 0) > time.time() else None

    async def trust_restore(self, token: str, d: dict) -> None:
        """Вернуть забранную запись (временный отказ: каталог недоступен, нет места в лимите сессий)."""
        ttl = int(float(d["exp"]) - time.time())
        if ttl > 0:
            await self._r.set(_tkey(token), json.dumps(d, ensure_ascii=False), ex=ttl)
            await self._r.hset(f"dtrust:u:{d['uid']}", _tkey(token), d.get("pub") or "")

    async def trust_forget(self, token: str) -> None:
        d = await self.trust_get(token)
        await self._r.delete(_tkey(token))
        if d:
            await self._r.hdel(f"dtrust:u:{d['uid']}", _tkey(token))

    async def trust_revoke(self, user_id: str, *, pub: str | None = None, keep_pub: str | None = None) -> int:
        """Отзыв доверия устройств пользователя: одной сессии (`pub`) или всех, кроме `keep_pub`."""
        idx = f"dtrust:u:{user_id}"
        n = 0
        for k, p in (await self._r.hgetall(idx)).items():
            if (pub is not None and p != pub) or (keep_pub is not None and p == keep_pub):
                continue
            n += await self._r.delete(k)
            await self._r.hdel(idx, k)
        return n
