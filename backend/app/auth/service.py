"""Сценарий входа: throttle → AD → локальный пользователь → серверная сессия."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import User, utcnow
from ..security.passwords import verify_room_password
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
    "not_configured": ("Вход доменной учётной записью пока не настроен. Войдите локальным администратором и подключите каталог в разделе «LDAP и доступ».", 503),
}
_UNAVAILABLE = ("Служба входа временно недоступна. Повторите позже или обратитесь к администратору.", 503)


def apply_profile(user: User, ident: DirectoryIdentity) -> None:
    """Профиль из каталога (разрешённые атрибуты) в локальную запись; пустое значение в каталоге очищает поле (человек сменил должность)."""
    def cut(v: str | None, n: int) -> str | None:
        return (v or "").strip()[:n] or None
    user.title, user.department, user.phone = cut(ident.title, 300), cut(ident.department, 300), cut(ident.phone, 64)
    user.profile_synced_at = utcnow()


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int, retry_after: int | None = None):
        super().__init__(code)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after
        self.reason = ""


@dataclass
class AccessDecision:
    """Решение о допуске после успешной проверки пароля: пустой список групп допуска — ограничения нет (вход всем)."""

    allowed: bool
    is_admin: bool
    reason: str                    # ok | not_in_allowed_groups | connection_not_for_users
    restricted: bool               # включён ли список групп допуска
    via: list[str]                 # группы допуска, в которые входит пользователь
    admin_via: list[str]           # группы администраторов, в которые входит пользователь


@dataclass
class LoginResult:
    session_id: str
    session: SessionData
    user: User
    decision: AccessDecision | None = None


class AuthService:
    def __init__(self, settings: Settings, directory: DirectoryClient, throttle: LoginThrottle, sessions: SessionStore):
        self._s = settings
        self._dir = directory
        self._throttle = throttle
        self._sessions = sessions
        self.settings_svc = None  # services.settings.SettingsService — группы доступа из веб-настроек; задаётся при запуске

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

        local = await self._local_login(db, norm, password, ip)
        if local is not None:
            return local

        try:
            identity: DirectoryIdentity = await asyncio.to_thread(self._dir.authenticate, login.strip() if "\\" in login or "@" in login else norm, password)
        except DirectoryError as exc:
            code = "invalid_credentials" if exc.code in ("user_not_found", "ambiguous_user") else exc.code
            if code == "invalid_credentials":
                await self._throttle.record_failure(norm, ip)
            if code in _MESSAGES:
                log.info("Вход не выполнен", extra={"reason": code})
                raise AuthError(code, *_MESSAGES[code]) from None
            log.error("Ошибка каталога при входе", extra={"directory_code": exc.code})
            raise AuthError("directory_unavailable", *_UNAVAILABLE) from None

        # Порядок: пароль в каталоге → правила допуска → только потом сессия. Без допуска — ни сессии, ни данных.
        decision = await self.access_decision(db, identity)
        is_admin = decision.is_admin
        if not decision.allowed:
            await self._throttle.record_success(norm)  # пароль верный — это не перебор
            err = AuthError("access_denied", *_MESSAGES["access_denied"])
            err.reason = decision.reason               # для журнала (в ответ пользователю не попадает)
            raise err

        await self._throttle.record_success(norm)
        user = await self._upsert_user(db, identity, is_admin)
        if not user.is_active:  # отключён администратором в системе (независимо от AD)
            raise AuthError("account_disabled_local", "Учётная запись отключена администратором системы.", 403)
        sid, data = await self._sessions.create(
            user_id=str(user.id), ad_guid=user.ad_guid, sam=user.sam_account_name,
            display_name=user.display_name, is_admin=is_admin, groups=sorted(identity.groups),
        )
        log.info("Вход выполнен", extra={"user_id": str(user.id), "is_admin": is_admin})
        return LoginResult(sid, data, user, decision)

    async def access_decision(self, db: AsyncSession, identity: DirectoryIdentity) -> AccessDecision:
        """Допуск пользователя каталога. Администраторы (по группам администраторов) допускаются всегда; локальный администратор
        сюда не попадает вовсе. Группы берутся из «LDAP и доступ» и из .env; читаются при каждом входе — перезапуск не нужен."""
        admin_groups = {g.strip().lower() for g in [self._s.ldap_admin_group_dn] if g.strip()}
        access_groups = {g.strip().lower() for g in [self._s.ldap_access_group_dn] if g.strip()}
        if self.settings_svc is not None:
            acc = await self.settings_svc.get(db, "access")
            admin_groups |= {g.lower() for g in acc.admin_groups}      # type: ignore[attr-defined]
            access_groups |= {g.lower() for g in acc.user_groups}      # type: ignore[attr-defined]
        admin_via = sorted(admin_groups & identity.groups)
        via = sorted(access_groups & identity.groups)
        is_admin = bool(admin_via) and identity.for_admins
        if not identity.for_users and not is_admin:
            return AccessDecision(False, False, "connection_not_for_users", bool(access_groups), via, admin_via)
        if access_groups and not via and not is_admin:
            return AccessDecision(False, False, "not_in_allowed_groups", True, via, admin_via)
        return AccessDecision(True, is_admin, "ok", bool(access_groups), via, admin_via)

    async def _local_login(self, db: AsyncSession, norm: str, password: str, ip: str) -> LoginResult | None:
        """Локальный (аварийный) администратор: проверяется ПЕРВЫМ и не зависит от каталога. Нет такого логина — None (идём в каталог)."""
        row = (await db.execute(select(User).where(User.auth_source == "local", func.lower(User.sam_account_name) == norm.lower()))).scalar_one_or_none()
        if row is None or not row.password_hash:
            return None
        if not verify_room_password(row.password_hash, password):
            await self._throttle.record_failure(norm, ip)
            return None   # возможно, это одноимённый доменный пользователь — пусть решает каталог
        if not row.is_active:
            raise AuthError("account_disabled_local", "Учётная запись отключена администратором системы.", 403)
        await self._throttle.record_success(norm)
        row.last_login_at = utcnow()
        await db.commit()
        sid, data = await self._sessions.create(user_id=str(row.id), ad_guid=row.ad_guid, sam=row.sam_account_name, display_name=row.display_name,
                                                is_admin=True, groups=[], local=True, must_change=row.must_change_password)
        log.info("Вход локального администратора", extra={"user_id": str(row.id)})
        return LoginResult(sid, data, row)

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
        apply_profile(user, ident)
        user.last_is_admin = is_admin
        user.last_login_at = utcnow()
        await db.commit()
        await db.refresh(user)
        return user
