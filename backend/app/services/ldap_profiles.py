"""Подключения к каталогу (LDAPS), настраиваемые из веб-интерфейса, и «составной» каталог для входа.

`LdapService` — CRUD профилей (пароль сервисной учётной записи шифруется, обратно не отдаётся), `ProfileDirectory` — реализация
`DirectoryClient`, которая ходит по включённым профилям по порядку. Если профилей в базе нет, а в окружении (`.env`) задан прежний
`LDAP_*`, он используется как «основное подключение» — установки до 0.3.0 продолжают работать без изменений.
"""
from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.directory import DirectoryError, DirectoryIdentity, LdapConfig, LdapDirectory, diagnose_ldap
from ..config import Settings
from ..models import LdapProfile, utcnow
from .ca_bundle import CaBundleService
from .settings import SettingsError, SettingsService

log = logging.getLogger("app.ldap")

_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_ATTR = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,62}$")
_DOMAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")
FIELDS = ("name", "enabled", "host", "port", "protocol", "base_dn", "upn_suffix", "netbios_domain", "timeout_s", "bind_dn",
          "login_attribute", "display_name_attribute", "email_attribute", "use_for_users", "use_for_admins")


class LdapService:
    def __init__(self, svc: SettingsService, ca: CaBundleService):
        self._svc = svc
        self._ca = ca

    # ------------------------------------------------------------------------- секрет
    def _aad(self, pid) -> bytes:
        return f"ldap_profile.{pid}".encode()

    def _encrypt(self, pid, secret: str) -> str:
        if not secret:
            return ""
        if self._svc.box is None:
            raise SettingsError("APP_MASTER_KEY не задан — пароль сохранить нельзя")
        return self._svc.box.encrypt(secret, aad=self._aad(pid))

    def _decrypt(self, row: LdapProfile) -> str:
        if not row.secret_enc or self._svc.box is None:
            return ""
        try:
            return self._svc.box.decrypt(row.secret_enc, aad=self._aad(row.id))
        except Exception:  # noqa: BLE001
            return ""

    # ----------------------------------------------------------------------- проверка
    @staticmethod
    def _clean(data: dict, *, partial: bool, current: LdapProfile | None = None) -> dict:
        out: dict = {}
        g = lambda k, d=None: data[k] if k in data else (getattr(current, k) if current is not None else d)  # noqa: E731
        if not partial or "name" in data:
            name = str(g("name", "")).strip()
            if not name or len(name) > 120:
                raise SettingsError("Название: 1–120 символов")
            out["name"] = name
        if not partial or "host" in data:
            host = str(g("host", "")).strip()
            host = re.sub(r"^ldaps?://", "", host, flags=re.I).split("/")[0]
            if ":" in host:   # порт, вписанный в адрес, переносим в поле «Порт»
                host, _, p = host.rpartition(":")
                if p.isdigit():
                    out["port"] = int(p)
            if not _HOST.match(host):
                raise SettingsError("Сервер: имя или IP без схемы и слэшей (например, dc1.example.local)")
            out["host"] = host
        if "port" not in out and (not partial or "port" in data):
            try:
                out["port"] = int(g("port", 636))
            except (TypeError, ValueError):
                raise SettingsError("Порт: число от 1 до 65535") from None
        if "port" in out and not 1 <= out["port"] <= 65535:
            raise SettingsError("Порт: число от 1 до 65535")
        if not partial or "protocol" in data:
            proto = str(g("protocol", "ldaps"))
            if proto not in ("ldaps", "starttls"):
                raise SettingsError("Протокол: ldaps (порт 636) или starttls (порт 389). Нешифрованный LDAP не поддерживается")
            out["protocol"] = proto
        if not partial or "base_dn" in data:
            base = str(g("base_dn", "")).strip()
            if not base or len(base) > 500 or "=" not in base:
                raise SettingsError("Base DN: например, DC=example,DC=local")
            out["base_dn"] = base
        if not partial or "bind_dn" in data:
            bind = str(g("bind_dn", "")).strip()
            if not bind or len(bind) > 500:
                raise SettingsError("Сервисная учётная запись: DN или user@домен")
            out["bind_dn"] = bind
        for k in ("upn_suffix", "netbios_domain"):
            if not partial or k in data:
                v = str(g(k, "") or "").strip()
                if v and not _DOMAIN.match(v):
                    raise SettingsError("Домен/суффикс: латиница, цифры, точка, дефис")
                out[k] = v
        defaults = {"login_attribute": "sAMAccountName", "display_name_attribute": "displayName", "email_attribute": "mail"}
        for k in ("login_attribute", "display_name_attribute", "email_attribute"):
            if not partial or k in data:
                v = str(g(k, defaults[k]) or defaults[k]).strip()
                if not _ATTR.match(v):
                    raise SettingsError("Имя атрибута LDAP: латиница и цифры (например, sAMAccountName, displayName, mail)")
                out[k] = v
        if not partial or "timeout_s" in data:
            try:
                t = int(g("timeout_s", 5))
            except (TypeError, ValueError):
                raise SettingsError("Таймаут: число секунд от 1 до 60") from None
            if not 1 <= t <= 60:
                raise SettingsError("Таймаут: от 1 до 60 секунд")
            out["timeout_s"] = t
        for k in ("enabled", "use_for_users", "use_for_admins"):
            if not partial or k in data:
                out[k] = bool(g(k, True))
        return out

    def _public(self, r: LdapProfile) -> dict:
        d = {k: getattr(r, k) for k in FIELDS}
        d.update(id=str(r.id), secret_set=bool(r.secret_enc), position=r.position, uri=self.uri(r))
        return d

    @staticmethod
    def uri(r: LdapProfile) -> str:
        return f"ldaps://{r.host}:{r.port}" if r.protocol == "ldaps" else f"ldap://{r.host}:{r.port} (STARTTLS)"

    # ------------------------------------------------------------------------- CRUD
    async def list(self, db: AsyncSession) -> list[dict]:
        rows = (await db.execute(select(LdapProfile).order_by(LdapProfile.position, LdapProfile.created_at))).scalars().all()
        return [self._public(r) for r in rows]

    async def row(self, db: AsyncSession, pid: str) -> LdapProfile:
        try:
            r = await db.get(LdapProfile, uuid.UUID(str(pid)))
        except ValueError:
            r = None
        if r is None:
            raise SettingsError("Подключение не найдено")
        return r

    async def create(self, db: AsyncSession, data: dict, secret: str) -> dict:
        clean = self._clean(data, partial=False)
        if not secret:
            raise SettingsError("Укажите пароль сервисной учётной записи")
        if (await db.execute(select(LdapProfile.id).where(LdapProfile.name == clean["name"]))).first():
            raise SettingsError("Подключение с таким названием уже есть")
        pos = (await db.execute(select(func.coalesce(func.max(LdapProfile.position), 0)))).scalar_one() + 1
        pid = uuid.uuid4()
        r = LdapProfile(id=pid, position=pos, secret_enc=self._encrypt(pid, secret), **clean)
        db.add(r)
        await db.flush()
        return self._public(r)

    async def update(self, db: AsyncSession, pid: str, data: dict, secret: str | None) -> dict:
        r = await self.row(db, pid)
        clean = self._clean(data, partial=True, current=r)
        if "name" in clean and clean["name"] != r.name and (await db.execute(select(LdapProfile.id).where(LdapProfile.name == clean["name"]))).first():
            raise SettingsError("Подключение с таким названием уже есть")
        for k, v in clean.items():
            setattr(r, k, v)
        if secret is not None:            # None — не менять; пустая строка — запрещена (без пароля подключение бесполезно)
            if secret == "":
                raise SettingsError("Пароль нельзя оставить пустым. Чтобы не менять пароль, не заполняйте поле")
            r.secret_enc = self._encrypt(r.id, secret)
        r.updated_at = utcnow()
        await db.flush()
        return self._public(r)

    async def delete(self, db: AsyncSession, pid: str) -> LdapProfile:
        r = await self.row(db, pid)
        await db.delete(r)
        await db.flush()
        return r

    async def move(self, db: AsyncSession, pid: str, direction: int) -> None:
        rows = list((await db.execute(select(LdapProfile).order_by(LdapProfile.position, LdapProfile.created_at))).scalars().all())
        i = next((k for k, x in enumerate(rows) if str(x.id) == pid), None)
        if i is None:
            raise SettingsError("Подключение не найдено")
        j = i + direction
        if 0 <= j < len(rows):
            rows[i], rows[j] = rows[j], rows[i]
        for n, x in enumerate(rows):
            x.position = n

    # ----------------------------------------------------------------------- рабочие
    def config(self, r: LdapProfile) -> LdapConfig:
        return LdapConfig(
            ldap_uri_list=[f"ldap{'s' if r.protocol == 'ldaps' else ''}://{r.host}:{r.port}"], ldap_base_dn=r.base_dn, ldap_bind_dn=r.bind_dn,
            ldap_bind_password=self._decrypt(r), ldap_ca_file=self._ca.current_path(), ldap_login_attribute=r.login_attribute,
            ldap_display_name_attribute=r.display_name_attribute, ldap_email_attribute=r.email_attribute, ldap_timeout_seconds=r.timeout_s,
            starttls=r.protocol == "starttls", name=r.name, netbios_domain=r.netbios_domain, upn_suffix=r.upn_suffix,
            use_for_users=r.use_for_users, use_for_admins=r.use_for_admins)

    def diagnose(self, r: LdapProfile) -> dict:
        """Блокирующая проверка (вызывать в потоке)."""
        return diagnose_ldap(self.config(r))


class ProfileDirectory:
    """Каталог из нескольких подключений. Методы синхронные (как у LdapDirectory) — вызываются в потоке."""

    def __init__(self, settings: Settings, service: LdapService):
        self._settings = settings
        self._service = service
        self._dirs: list[tuple[LdapConfig, LdapDirectory]] = []
        self.errors: dict[str, str] = {}

    async def reload(self, db: AsyncSession) -> None:
        rows = (await db.execute(select(LdapProfile).where(LdapProfile.enabled.is_(True)).order_by(LdapProfile.position, LdapProfile.created_at))).scalars().all()
        dirs: list[tuple[LdapConfig, LdapDirectory]] = []
        errors: dict[str, str] = {}
        for r in rows:
            cfg = self._service.config(r)
            try:
                dirs.append((cfg, LdapDirectory(cfg)))
            except DirectoryError as exc:
                errors[r.name] = exc.detail or exc.code
        if not rows and self._settings.ldap_uri_list and self._settings.ldap_ca_file:   # прежняя настройка через .env
            try:
                cfg = LdapConfig.from_settings(self._settings)
                dirs.append((cfg, LdapDirectory(cfg)))
            except DirectoryError as exc:
                errors["env"] = exc.detail or exc.code
        self._dirs, self.errors = dirs, errors

    @property
    def configured(self) -> bool:
        return bool(self._dirs)

    @staticmethod
    def _hint(login: str) -> tuple[str, str]:
        """(netbios, upn-суффикс) из введённого логина — чтобы не обращаться ко всем подключениям."""
        login = (login or "").strip()
        if "\\" in login:
            return login.split("\\", 1)[0].lower(), ""
        if "@" in login:
            return "", login.rsplit("@", 1)[1].lower()
        return "", ""

    def _candidates(self, login: str) -> list[tuple[LdapConfig, LdapDirectory]]:
        nb, suffix = self._hint(login)
        if nb:
            m = [d for d in self._dirs if d[0].netbios_domain.lower() == nb]
            if m:
                return m
        if suffix:
            m = [d for d in self._dirs if d[0].upn_suffix.lower() == suffix]
            if m:
                return m
        return list(self._dirs)

    def authenticate(self, login: str, password: str) -> DirectoryIdentity:
        cands = self._candidates(login)
        if not cands:
            raise DirectoryError("not_configured")
        unavailable: DirectoryError | None = None
        for _cfg, d in cands:
            try:
                return d.authenticate(login, password)
            except DirectoryError as exc:
                if exc.code in ("user_not_found",):
                    continue                       # пользователя нет в этом каталоге — пробуем следующий
                if exc.code in ("server_unavailable", "tls_error", "service_account_error", "config_error"):
                    unavailable = exc              # этот каталог сейчас недоступен — пробуем следующий, но запоминаем
                    continue
                raise                              # неверный пароль/блокировка: решение принято, дальше не ходим
        raise unavailable or DirectoryError("user_not_found")

    def lookup(self, login: str) -> DirectoryIdentity:
        """Пользователь и его группы без проверки пароля (проверка правил доступа в админке)."""
        cands = self._candidates(login)
        if not cands:
            raise DirectoryError("not_configured")
        unavailable: DirectoryError | None = None
        for _cfg, d in cands:
            try:
                return d.lookup(login)
            except DirectoryError as exc:
                if exc.code == "user_not_found":
                    continue
                if exc.code in ("server_unavailable", "tls_error", "service_account_error", "config_error"):
                    unavailable = exc
                    continue
                raise
        raise unavailable or DirectoryError("user_not_found")

    def check_service_account(self) -> None:
        if not self._dirs:
            raise DirectoryError("not_configured")
        for _cfg, d in self._dirs:
            d.check_service_account()

    def members(self, group_dn: str, limit: int = 300) -> list[dict]:
        """Состав группы: группа ищется во всех подключениях (где её нет — пустой ответ)."""
        out: list[dict] = []
        for _cfg, d in self._dirs:
            try:
                out += d.members(group_dn, limit)
            except DirectoryError:
                continue
        return out[:limit]

    def search(self, kind: str, query: str, limit: int = 20) -> list[dict]:
        if not self._dirs:
            raise DirectoryError("not_configured")
        out: list[dict] = []
        last: DirectoryError | None = None
        ok = False
        for _cfg, d in self._dirs:
            try:
                out += d.search(kind, query, limit)
                ok = True
            except DirectoryError as exc:
                last = exc
        if not ok and last is not None:
            raise last
        seen: set[str] = set()
        uniq = []
        for item in out:
            if item["ref"].lower() not in seen:
                seen.add(item["ref"].lower())
                uniq.append(item)
        return uniq[:limit]
