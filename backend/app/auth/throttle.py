"""Защита от перебора паролей: адаптер между сценариями входа и центральной защитой (security/loginguard.py).

Главная цель прежняя — чтобы САМ сервис не вызвал массовую блокировку учёток в AD: после серии неудач попытки с неизвестных адресов не доходят до каталога.
Новое: учётная запись целиком не блокируется (иначе чужую запись можно было бы «залочить» несколькими запросами), а счётчики общие для всех экземпляров backend (Redis)."""
from __future__ import annotations

import logging

from redis.asyncio import Redis

from ..config import Settings


log = logging.getLogger("app.auth.throttle")


class ThrottledError(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"retry_after={retry_after}")
        self.retry_after = retry_after


class LoginThrottle:
    """Прежнее имя и интерфейс для сценариев входа (AuthService); вся логика — в security/loginguard.py (счётчики по записи, адресу и паре, блокировки адресов, защита каталога).
    Прежние настройки .env (`LOGIN_MAX_FAILURES_*`) больше ничего не определяют: пороги задаются в «Администрирование → Безопасность → Защита входа» и «CAPTCHA»."""

    def __init__(self, redis: Redis, settings: Settings, guard=None):
        self._r = redis
        self._s = settings
        self.guard = guard

    async def check(self, login: str, ip: str) -> None:
        from ..security.loginguard import GuardUnavailable  # noqa: PLC0415

        try:
            v = await self.guard.check_block(ip, login)
        except GuardUnavailable:
            e = ThrottledError(30)
            e.unavailable = True                       # type: ignore[attr-defined]
            raise e from None
        if not v.allow:
            raise ThrottledError(v.retry_after)

    async def record_failure(self, login: str, ip: str) -> None:
        from ..security.loginguard import GuardUnavailable  # noqa: PLC0415

        try:
            await self.guard.failure(ip, login)
        except GuardUnavailable:
            log.error("Неудачный вход не записан: хранилище защиты недоступно")

    async def record_success(self, login: str, ip: str = "") -> None:
        await self.guard.success(ip, login)


async def within_limit(redis: Redis, key: str, limit: int, window_s: int) -> bool:
    """Не больше `limit` действий за `window_s` секунд на ключ (фиксированное окно). Ключ содержит пользователя/встречу, не персональные данные.
    Сбой Redis не блокирует действие: ограничение — защита от злоупотреблений, а не условие работы."""
    try:
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, window_s)
        return n <= limit
    except Exception:  # noqa: BLE001
        return True
