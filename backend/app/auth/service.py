"""Сценарий входа: throttle → AD → локальный пользователь → серверная сессия."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import User, utcnow
from .directory import DirectoryClient, DirectoryError, DirectoryIdentity, parse_login
from .sessions import SessionData, SessionStore
from .throttle import LoginThrottle, ThrottledError

log = logging.getLogger("app.auth")

# Безопасные сообщения для пользователя. Для неверного логина и неверного пароля — одно и то же.
_MESSAGES = {
    "invalid_credentials": ("Неверный логин или пароль.", 401),
    "account_locked": ("Учётная запись заблокирована в домене. Обратитесь в службу поддержки.", 403),
    "account_disabled": ("Учётная запись отключена или просрочена.", 403),
    "password_expired": ("Срок действия пароля истёк. Смените пароль в домене.", 403),
    "password_must_change": ("Требуется сменить пароль в домене.", 403),
    "account_restriction": ("Вход этой учётной записи ограничен политикой домена.", 403),
    "access_denied": ("Нет разрешения на вход в систему.", 403),
    "throttled": ("Слишком много неудачных попыток. Повторите позже.", 429),
}
_UNAVAILABLE = ("Служба входа временно недоступна. Повторите позже или обратитесь к администратору.", 503)


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int, retry_after: int | None = None):
        super().__init__(code)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


@dataclass
class LoginResult:
    session_id: str
    session: SessionData
    user: User


class AuthService:
    def __init__(self, settings: Settings, directory: DirectoryClient, throttle: LoginThrottle, sessions: SessionStore):
        self._s = settings
        self._dir = directory
        self._throttle = throttle
        self._sessions = sessions

    async def login(self, db: AsyncSession, login: str, password: str, ip: str) -> LoginResult:
        try:
            norm, _ = parse_login(login)
        except DirectoryError:
            raise AuthError("invalid_credentials", *_MESSAGES["invalid_credentials"]) from None
        if not password or len(password) > 512:
            raise AuthError("invalid_credentials", *_MESSAGES["invalid_credentials"])

        try:
            await self._throttle.check(norm, ip)
        except ThrottledError as exc:
            log.warning("Вход отклонён throttle", extra={"login_hash_only": True, "retry_after": exc.retry_after})
            raise AuthError("throttled", _MESSAGES["throttled"][0], 429, retry_after=exc.retry_after) from None

        try:
            identity: DirectoryIdentity = await asyncio.to_thread(self._dir.authenticate, norm, password)
        except DirectoryError as exc:
            code = "invalid_credentials" if exc.code in ("user_not_found", "ambiguous_user") else exc.code
            if code == "invalid_credentials":
                await self._throttle.record_failure(norm, ip)
            if code in _MESSAGES:
                log.info("Вход не выполнен", extra={"reason": code})
                raise AuthError(code, *_MESSAGES[code]) from None
            log.error("Ошибка каталога при входе", extra={"directory_code": exc.code})
            raise AuthError("directory_unavailable", *_UNAVAILABLE) from None

        # Доступ к системе в целом (необязательная группа).
        access_dn = self._s.ldap_access_group_dn.strip().lower()
        admin_dn = self._s.ldap_admin_group_dn.strip().lower()
        is_admin = bool(admin_dn) and admin_dn in identity.groups
        if access_dn and access_dn not in identity.groups and not is_admin:
            await self._throttle.record_success(norm)  # пароль верный — это не перебор
            raise AuthError("access_denied", *_MESSAGES["access_denied"])

        await self._throttle.record_success(norm)
        user = await self._upsert_user(db, identity, is_admin)
        if not user.is_active:  # отключён администратором в системе (независимо от AD)
            raise AuthError("account_disabled_local", "Учётная запись отключена администратором системы.", 403)
        sid, data = await self._sessions.create(
            user_id=str(user.id), ad_guid=user.ad_guid, sam=user.sam_account_name,
            display_name=user.display_name, is_admin=is_admin, groups=sorted(identity.groups),
        )
        log.info("Вход выполнен", extra={"user_id": str(user.id), "is_admin": is_admin})
        return LoginResult(sid, data, user)

    @staticmethod
    async def _upsert_user(db: AsyncSession, ident: DirectoryIdentity, is_admin: bool) -> User:
        user = (await db.execute(select(User).where(User.ad_guid == ident.ad_guid))).scalar_one_or_none()
        if user is None:
            user = User(ad_guid=ident.ad_guid, sam_account_name=ident.sam_account_name,
                        display_name=ident.display_name, is_active=True)
            db.add(user)
        user.sam_account_name = ident.sam_account_name
        user.upn = ident.upn
        user.display_name = ident.display_name
        user.email = ident.email
        user.last_is_admin = is_admin
        user.last_login_at = utcnow()
        await db.commit()
        await db.refresh(user)
        return user
