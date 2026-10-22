"""Сценарий входа: throttle → AD → локальный пользователь → серверная сессия."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import User, UserBackupCode, utcnow
from ..profiles.manual import apply_manual
from ..profiles.service import record_ad
from ..security.passwords import verify_room_password
from .directory import DirectoryClient, DirectoryError, DirectoryIdentity, parse_login
from .devices import device_label
from .sessions import SessionData, SessionLimit, SessionStore
from .throttle import LoginThrottle, ThrottledError
from ..services import totp

CHALLENGE_TTL_S = 300
CHALLENGE_ATTEMPTS = 5

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


def apply_profile(user: User, ident: DirectoryIdentity, *, clear_missing: bool = False) -> None:
    """Профиль из каталога (разрешённые атрибуты) в локальную запись при входе: ПРИХОДЯЩИЕ значения обновляют поля, а отсутствующий атрибут рабочее значение не стирает
    (каталог мог временно не вернуть необязательный атрибут). Очищать поля по отсутствующему атрибуту можно только при ручном «Обновить данные» (`clear_missing=True`)."""
    def cut(v: str | None, n: int) -> str | None:
        return (v or "").strip()[:n] or None
    got: dict[str, str | None] = {}
    for field, raw, n in (("title", ident.title, 300), ("department", ident.department, 300), ("phone", ident.phone, 64)):
        val = cut(raw, n)
        got[field] = val
        if val is not None or clear_missing:
            setattr(user, field, val)
    record_ad(user, got, clear_missing=clear_missing)        # значения каталога запоминаются отдельно: итог карточки считается по приоритетам источников
    user.profile_synced_at = utcnow()


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int, retry_after: int | None = None):
        super().__init__(code)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after
        self.reason = ""
        self.extra: dict = {}            # дополнительные поля ответа (например, список сессий при достижении лимита)


def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def uuid_of(v: str):
    import uuid  # noqa: PLC0415

    return uuid.UUID(v)


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
    session: SessionData | None
    user: User
    decision: AccessDecision | None = None
    challenge: str | None = None        # пароль верен, но нужен код 2FA: сессии ещё нет, её откроет /auth/login/totp
    params: dict | None = None          # без сессии (open_session=False): права для выдачи токена надстройки — is_admin, groups, local
    needs_browser: str | None = None    # без сессии: нужен вход в браузере — 2FA, её настройка или смена пароля
    backup_used: bool = False


def totp_required(cfg, *, is_admin: bool, groups) -> bool:
    """Обязательна ли двухфакторная защита этому пользователю по политике (для off и optional — нет)."""
    p = cfg.totp_policy
    return p == "all" or (p == "admins" and is_admin) or (p == "groups" and bool(cfg.group_set() & {g.lower() for g in groups}))


class AuthService:
    def __init__(self, settings: Settings, directory: DirectoryClient, throttle: LoginThrottle, sessions: SessionStore):
        self._s = settings
        self._dir = directory
        self._throttle = throttle
        self._sessions = sessions
        self.settings_svc = None  # services.settings.SettingsService — группы доступа из веб-настроек; задаётся при запуске

    async def login(self, db: AsyncSession, login: str, password: str, ip: str, ua: str = "", *, open_session: bool = True) -> LoginResult:
        """open_session=False — только проверка личности и допуска (вход в боковой панели Outlook: сеанс браузера не открывается и не занимает место в лимите сеансов)."""
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

        local = await self._local_login(db, norm, password, ip, ua, open_session=open_session)
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
        res = await self._open(db, user, is_admin=is_admin, groups=sorted(identity.groups), decision=decision, ip=ip, ua=ua, open_session=open_session)
        log.info("Вход выполнен" if not res.challenge else "Пароль принят, ожидается код 2FA", extra={"user_id": str(user.id), "is_admin": is_admin})
        return res

    # ------------------------------------------------------------------------------ второй фактор
    async def _totp_cfg(self, db: AsyncSession):
        if self.settings_svc is None:
            return None
        cfg = await self.settings_svc.get(db, "auth_security")
        await db.commit()          # закрыть читающую транзакцию: на SQLite она бы блокировала запись аудита из другого сеанса (ожидание 5 с)
        return cfg

    async def _open(self, db: AsyncSession, user: User, *, is_admin: bool, groups: list[str], ip: str, decision: AccessDecision | None = None,
                    local: bool = False, must_change: bool = False, ua: str = "", open_session: bool = True) -> LoginResult:
        """Пароль проверен: открыть сессию сразу, потребовать код 2FA (сессии пока нет) или открыть сессию «только для настройки 2FA»."""
        cfg = await self._totp_cfg(db)
        on = cfg is not None and cfg.totp_policy != "off"
        if not open_session:
            need = "totp" if on and user.totp_enabled and user.totp_secret_enc else "totp_setup" if on and totp_required(cfg, is_admin=is_admin, groups=groups) and not user.totp_enabled \
                else "password_change" if must_change else None
            return LoginResult("", None, user, decision, params=dict(is_admin=is_admin, groups=groups, local=local), needs_browser=need)
        if on and user.totp_enabled and user.totp_secret_enc:
            token = secrets.token_urlsafe(24)
            await self._sessions._r.set(f"totp:chal:{_th(token)}", json.dumps({"user_id": str(user.id), "is_admin": is_admin, "groups": groups, "local": local, "must_change": must_change,
                                                                           "ip": ip, "ua": ua[:400]}), ex=CHALLENGE_TTL_S)
            return LoginResult("", None, user, decision, challenge=token)
        must = bool(on and totp_required(cfg, is_admin=is_admin, groups=groups) and not user.totp_enabled)
        sid, data = await self._create_session(user, cfg, dict(is_admin=is_admin, groups=groups, local=local, must_change=must_change, must_totp=must), ip=ip, ua=ua)
        return LoginResult(sid, data, user, decision)

    async def _create_session(self, user: User, cfg, params: dict, *, ip: str, ua: str, free: str | None = None, force: bool = False) -> tuple[str, SessionData]:
        """Открывает сессию с учётом лимита одновременных входов (`auth_security.max_sessions`, по умолчанию 5). Лимит достигнут — существующие сессии НЕ завершаются: клиент получает 409
        с их списком и одноразовым правом `free_token` завершить одну из них (подтверждение личности — пароль и 2FA — уже пройдено) и войти (`finish_free_slot`)."""
        limit = int(getattr(cfg, "max_sessions", 5)) if cfg is not None else 5
        try:
            return await self._sessions.create(user_id=str(user.id), ad_guid=user.ad_guid, sam=user.sam_account_name, display_name=user.display_name, device=device_label(ua),
                                               ip=ip, limit=0 if force else limit, free=free, **params)
        except SessionLimit as exc:
            token = secrets.token_urlsafe(32)
            await self._sessions._r.set(f"sess:free:{_th(token)}", json.dumps({"user_id": str(user.id), "params": params, "ip": ip, "ua": ua[:400]}), ex=CHALLENGE_TTL_S)
            err = AuthError("session_limit", f"Открыто максимальное число сеансов этой учётной записи ({exc.limit}). Завершите один из них, чтобы войти здесь — остальные продолжат работать.", 409)
            err.extra = {"free_token": token, "limit": exc.limit, "sessions": exc.sessions}
            raise err from None

    async def finish_free_slot(self, db: AsyncSession, token: str, pub: str) -> LoginResult:
        """Завершает выбранную сессию пользователя и открывает новую (после session_limit). Токен одноразовый и привязан к пользователю, уже подтвердившему личность."""
        key = f"sess:free:{_th(token)}"
        raw = await self._sessions._r.get(key)
        if raw is None:
            raise AuthError("free_expired", "Время подтверждения истекло. Войдите заново.", 401)
        ch = json.loads(raw)
        user = await db.get(User, uuid_of(ch["user_id"]))
        if user is None or not user.is_active:
            await self._sessions._r.delete(key)
            raise AuthError("free_expired", "Время подтверждения истекло. Войдите заново.", 401)
        if not await self._sessions.destroy_pub(str(user.id), pub):
            raise AuthError("session_not_found", "Эта сессия уже завершена или не принадлежит вам. Обновите список.", 404)
        await self._sessions._r.delete(key)
        cfg = await self._totp_cfg(db)
        sid, data = await self._create_session(user, cfg, ch["params"], ip=ch["ip"], ua=ch["ua"])
        return LoginResult(sid, data, user, None)

    async def complete_totp(self, db: AsyncSession, token: str, code: str, ip: str) -> LoginResult:
        """Второй шаг входа: код из приложения или резервный код. Пять попыток на вход, общий счётчик неудач с паролем, повтор принятого кода отклоняется."""
        r = self._sessions._r
        key = f"totp:chal:{_th(token)}"
        raw = await r.get(key)
        if raw is None:
            raise AuthError("totp_expired", "Время подтверждения истекло. Войдите заново.", 401)
        ch = json.loads(raw)
        user = await db.get(User, uuid_of(ch["user_id"]))
        if user is None or not user.is_active or not user.totp_enabled or not user.totp_secret_enc:
            await r.delete(key)
            raise AuthError("totp_expired", "Время подтверждения истекло. Войдите заново.", 401)
        norm = user.sam_account_name.lower()
        try:
            await self._throttle.check(norm, ip)
        except ThrottledError as exc:
            raise AuthError("throttled", _MESSAGES["throttled"][0], 429, retry_after=exc.retry_after) from None
        attempts = await r.incr(f"totp:att:{_th(token)}")
        if attempts == 1:
            await r.expire(f"totp:att:{_th(token)}", CHALLENGE_TTL_S)
        if attempts > CHALLENGE_ATTEMPTS:
            await r.delete(key)
            raise AuthError("totp_expired", "Слишком много неверных кодов. Войдите заново.", 429)
        ok, used_backup = await self.check_code(db, user, code)
        if not ok:
            await self._throttle.record_failure(norm, ip)
            raise AuthError("invalid_code", "Неверный код. Проверьте время на телефоне или используйте резервный код.", 401)
        await r.delete(key, f"totp:att:{_th(token)}")
        await self._throttle.record_success(norm)
        user.last_login_at = utcnow()
        await db.commit()
        cfg2 = await self._totp_cfg(db)
        sid, data = await self._create_session(user, cfg2, dict(is_admin=ch["is_admin"], groups=ch["groups"], local=ch["local"], must_change=ch["must_change"], must_totp=False),
                                               ip=ch.get("ip", ip), ua=ch.get("ua", ""))
        return LoginResult(sid, data, user, None, backup_used=used_backup)

    async def check_code(self, db: AsyncSession, user: User, code: str) -> tuple[bool, bool]:
        """Код из приложения (с защитой от повтора) или резервный код (гасится при использовании). Возвращает (принят, это_резервный_код)."""
        if totp.looks_like_backup(code):
            h = totp.hash_backup(str(user.id), code)
            from sqlalchemy import update as sa_update  # noqa: PLC0415

            used = await db.execute(sa_update(UserBackupCode).where(UserBackupCode.user_id == user.id, UserBackupCode.code_hash == h, UserBackupCode.used_at.is_(None))
                                    .values(used_at=utcnow()))               # погашение одним условным UPDATE: два одновременных входа с одним кодом не пройдут оба
            await db.commit()
            return used.rowcount == 1, used.rowcount == 1
        secret = self._decrypt_totp(user)
        step = totp.verify(secret, code, user.totp_last_step) if secret else None
        if step is None:
            return False, False
        from sqlalchemy import update  # noqa: PLC0415

        upd = await db.execute(update(User).where(User.id == user.id, (User.totp_last_step.is_(None)) | (User.totp_last_step < step)).values(totp_last_step=step))
        await db.commit()
        return upd.rowcount == 1, False                    # параллельный запрос с тем же кодом проиграет

    def _decrypt_totp(self, user: User) -> str:
        box = self.settings_svc.box if self.settings_svc is not None else None
        if box is None or not user.totp_secret_enc:
            return ""
        try:
            return box.decrypt(user.totp_secret_enc, aad=f"user_totp.{user.id}".encode())
        except Exception:  # noqa: BLE001 — повреждённый секрет = «кода нет», а не падение входа
            return ""

    async def verify_current(self, db: AsyncSession, su, password: str, ip: str) -> bool:
        """Повторное подтверждение пароля уже вошедшего администратора (для опасных действий, например выгрузки секретов). Сессия не создаётся.
        Локальный администратор — по хэшу; пользователь каталога — повторной проверкой пароля в каталоге и сверкой идентификатора. Неудачи считаются как неудачные входы (ограничение перебора)."""
        norm = (su.sam_account_name or "").lower()
        if not password or len(password) > 512:
            return False
        try:
            await self._throttle.check(norm, ip)
        except ThrottledError as exc:
            raise AuthError("throttled", _MESSAGES["throttled"][0], 429, retry_after=exc.retry_after) from None
        user = await db.get(User, su.user_id)
        ok = False
        if user is not None and user.is_active:
            if user.auth_source == "local":
                ok = bool(user.password_hash) and verify_room_password(user.password_hash, password)
            else:
                try:
                    ident = await asyncio.to_thread(self._dir.authenticate, norm, password)
                    ok = bool(ident.ad_guid) and ident.ad_guid == user.ad_guid
                except DirectoryError:
                    ok = False
        if ok:
            await self._throttle.record_success(norm)
        else:
            await self._throttle.record_failure(norm, ip)
        return ok

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

    async def _local_login(self, db: AsyncSession, norm: str, password: str, ip: str, ua: str = "", *, open_session: bool = True) -> LoginResult | None:
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
        res = await self._open(db, row, is_admin=True, groups=[], local=True, must_change=row.must_change_password, ip=ip, ua=ua, open_session=open_session)
        log.info("Вход локального администратора", extra={"user_id": str(row.id)})
        return res

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
        record_ad(user, {"display_name": ident.display_name, "email": ident.email}, clear_missing=True)
        apply_profile(user, ident)
        apply_manual(user)                                        # правки пользователя в личном кабинете сильнее данных каталога
        user.last_is_admin = is_admin
        user.last_login_at = utcnow()
        await db.commit()
        await db.refresh(user)
        return user
