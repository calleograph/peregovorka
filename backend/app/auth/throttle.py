"""Защита от перебора доменных паролей.

Главная цель — чтобы САМ сервис не вызвал массовую блокировку учёток в AD:
после N неудач на логин (N < порога блокировки AD) новые попытки для этого
логина отклоняются БЕЗ обращения к AD на время блокировки. Дополнительно —
лимит по IP. Портировано по идее из legacy/php/src/Security/RateLimiter.php
(там — SQLite и экспоненциальная задержка, здесь — Redis и фиксированное окно).

Осознанный компромисс: злоумышленник может «залочить» чужой логин в нашем
сервисе (но не в AD) — это безопаснее, чем заблокировать учётку в домене.
"""
from __future__ import annotations

import hashlib

from redis.asyncio import Redis

from ..config import Settings


class ThrottledError(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"retry_after={retry_after}")
        self.retry_after = retry_after


def _h(value: str) -> str:
    # хэш вместо логина в ключах Redis — логины не светятся в дампах/мониторинге
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]


class LoginThrottle:
    def __init__(self, redis: Redis, settings: Settings):
        self._r = redis
        self._s = settings

    def _keys(self, login: str, ip: str) -> tuple[str, str, str]:
        return f"login:fail:user:{_h(login)}", f"login:lock:user:{_h(login)}", f"login:fail:ip:{ip}"

    async def check(self, login: str, ip: str) -> None:
        fail_u, lock_u, fail_ip = self._keys(login, ip)
        ttl = await self._r.ttl(lock_u)
        if ttl and ttl > 0:
            raise ThrottledError(ttl)
        ip_count = await self._r.get(fail_ip)
        if ip_count is not None and int(ip_count) >= self._s.login_max_failures_per_ip:
            raise ThrottledError(max(1, await self._r.ttl(fail_ip)))

    async def record_failure(self, login: str, ip: str) -> None:
        fail_u, lock_u, fail_ip = self._keys(login, ip)
        window = self._s.login_failure_window_seconds
        n = await self._r.incr(fail_u)
        if n == 1:
            await self._r.expire(fail_u, window)
        if n >= self._s.login_max_failures_per_user:
            await self._r.set(lock_u, "1", ex=self._s.login_lockout_seconds)
            await self._r.delete(fail_u)
        m = await self._r.incr(fail_ip)
        if m == 1:
            await self._r.expire(fail_ip, window)

    async def record_success(self, login: str) -> None:
        fail_u, lock_u, _ = self._keys(login, "")
        await self._r.delete(fail_u, lock_u)
