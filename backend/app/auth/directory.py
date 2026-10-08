"""Доступ к Active Directory по LDAPS.

Портировано из legacy/php/src/Ldap/LdapClient.php и Llm/Domain/ChatAuthenticator.php:
  * проверка сертификата ОБЯЗАТЕЛЬНА (CERT_REQUIRED + CA-файл + имя хоста), обхода нет;
  * пароль проверяется bind'ом от имени пользователя, нигде не сохраняется;
  * группы (включая вложенные) — LDAP_MATCHING_RULE_IN_CHAIN 1.2.840.113556.1.4.1941;
  * коды ошибок AD (data 52e/532/533/773/775/52f) классифицируются.
Сервисная учётка используется только на чтение.

Бизнес-логика зависит от протокола DirectoryClient, а не от ldap3 — в unit-тестах
LDAP подменяется.
"""
from __future__ import annotations

import logging
import re
import ssl
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from ..config import Settings

log = logging.getLogger("app.auth.directory")

LDAP_MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"
_UAC_DISABLED = 0x2


class DirectoryError(Exception):
    """Безопасная (без секретов) ошибка каталога с машинным кодом."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class DirectoryIdentity:
    ad_guid: str
    dn: str
    sam_account_name: str
    upn: str | None
    display_name: str
    email: str | None
    groups: frozenset[str]  # DN групп в нижнем регистре, включая вложенные
    source: str = ""         # название подключения, через которое найден пользователь
    for_users: bool = True   # подключение используется для входа обычных пользователей
    for_admins: bool = True  # … и для административного входа


@dataclass
class LdapConfig:
    """Параметры одного подключения к каталогу. Имена полей совпадают с Settings.ldap_*, поэтому LdapDirectory работает с обоими."""

    ldap_uri_list: list[str]
    ldap_base_dn: str
    ldap_bind_dn: str
    ldap_bind_password: str = field(default="", repr=False)
    ldap_ca_file: str = ""
    ldap_login_attribute: str = "sAMAccountName"
    ldap_display_name_attribute: str = "displayName"
    ldap_email_attribute: str = "mail"
    ldap_timeout_seconds: int = 5
    starttls: bool = False
    name: str = ""
    netbios_domain: str = ""
    upn_suffix: str = ""
    use_for_users: bool = True
    use_for_admins: bool = True

    @classmethod
    def from_settings(cls, s: Settings) -> "LdapConfig":
        return cls(ldap_uri_list=s.ldap_uri_list, ldap_base_dn=s.ldap_base_dn, ldap_bind_dn=s.ldap_bind_dn, ldap_bind_password=s.ldap_bind_password,
                   ldap_ca_file=s.ldap_ca_file, ldap_login_attribute=s.ldap_login_attribute, ldap_display_name_attribute=s.ldap_display_name_attribute,
                   ldap_timeout_seconds=s.ldap_timeout_seconds, name="Основное (из настроек установки)")


class DirectoryClient(Protocol):
    def authenticate(self, login: str, password: str) -> DirectoryIdentity:  # blocking
        ...

    def check_service_account(self) -> None:  # blocking; для readiness/диагностики
        ...

    def search(self, kind: str, query: str, limit: int = 20) -> list[dict]:  # blocking; kind: group | user
        ...


# Белый список: буквы любых алфавитов, цифры, «_», «.», «-». Всё остальное (пробелы, кавычки, скобки, «*», «\» внутри, управляющие и
# невидимые символы Unicode) в логине недопустимо — это закрывает попытки инъекции в LDAP-фильтр и «грязные» записи в журналах.
_LOGIN_PART = re.compile(r"^\w[\w.\-]{0,63}$")
_UPN_DOMAIN = re.compile(r"^\w[\w.\-]{0,252}$")


def parse_login(raw: str) -> tuple[str, bool]:
    """Нормализует введённый логин.

    Возвращает (значение, is_upn). Принимает `user`, `DOMAIN\\user`, `user@domain`.
    Для формы user@domain поиск идёт по userPrincipalName.
    """
    login = (raw or "").strip()
    if "\\" in login:
        domain, login = login.split("\\", 1)
        if not _LOGIN_PART.match(domain):
            raise DirectoryError("invalid_credentials", "недопустимый формат логина")
    is_upn = "@" in login
    if login.count("@") > 1:
        raise DirectoryError("invalid_credentials", "недопустимый формат логина")
    name, _, dom = login.partition("@")
    if len(login) > 256 or not _LOGIN_PART.match(name) or (is_upn and not _UPN_DOMAIN.match(dom)):
        raise DirectoryError("invalid_credentials", "недопустимый формат логина")
    return login, is_upn


def classify_bind_failure(result: dict | None) -> str:
    """Код ошибки по результату bind (ldap3 connection.result)."""
    result = result or {}
    desc = str(result.get("description", "")).lower()
    message = str(result.get("message", "")).lower()
    if "invalidcredentials" in desc or "invalid_credentials" in desc or result.get("result") == 49:
        for needle, code in (
            ("data 775", "account_locked"),
            ("data 533", "account_disabled"),
            ("data 701", "account_disabled"),
            ("data 532", "password_expired"),
            ("data 773", "password_must_change"),
            ("data 52f", "account_restriction"),
            ("data 530", "account_restriction"),
            ("data 531", "account_restriction"),
        ):
            if needle in message:
                return code
        return "invalid_credentials"
    if result.get("result") in (50,) or "insufficientaccess" in desc:
        return "insufficient_access"
    if result.get("result") in (51, 52, 81) or "unavailable" in desc or "busy" in desc:
        return "server_unavailable"
    return "unknown"


def guid_from_bytes(raw: bytes) -> str:
    """objectGUID (little-endian, 16 байт) → каноническая строка UUID."""
    return str(uuid.UUID(bytes_le=raw))


class LdapDirectory:
    """Реализация DirectoryClient на ldap3."""

    def __init__(self, settings: "Settings | LdapConfig"):
        if isinstance(settings, Settings):
            settings = LdapConfig.from_settings(settings)
        if not settings.ldap_uri_list:
            raise DirectoryError("config_error", "Адрес LDAP не задан")
        if not settings.ldap_ca_file:
            raise DirectoryError("config_error", "Нет CA-сертификата: проверка сертификата обязательна (загрузите CA в разделе «LDAP и доступ»)")
        self._s = settings

    # ------------------------------------------------------------ соединения
    def _server_pool(self):
        from urllib.parse import urlsplit

        from ldap3 import ROUND_ROBIN, Server, ServerPool, Tls

        tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT, ca_certs_file=self._s.ldap_ca_file)
        servers = []
        for uri in self._s.ldap_uri_list:
            parts = urlsplit(uri)
            servers.append(
                Server(parts.hostname, port=parts.port or (389 if self._s.starttls else 636), use_ssl=not self._s.starttls, tls=tls,
                       connect_timeout=self._s.ldap_timeout_seconds, get_info=None)
            )
        return ServerPool(servers, ROUND_ROBIN, active=True, exhaust=True)

    def _connection(self, user: str, password: str):
        from ldap3 import SIMPLE, Connection
        from ldap3.core.exceptions import LDAPException

        conn = Connection(
            self._server_pool(), user=user, password=password, authentication=SIMPLE,
            auto_bind=False, raise_exceptions=False, read_only=True,
            receive_timeout=self._s.ldap_timeout_seconds,
        )
        try:
            if self._s.starttls:
                conn.open()
                if not conn.start_tls():
                    raise DirectoryError("tls_error", "STARTTLS не выполнен")
            ok = conn.bind()
        except LDAPException as exc:
            raise self._transport_error(exc) from None
        if not ok:
            raise DirectoryError(classify_bind_failure(conn.result))
        return conn

    @staticmethod
    def _transport_error(exc: Exception) -> DirectoryError:
        text = f"{type(exc).__name__}: {exc}".lower()
        if "ssl" in text or "certificate" in text or "tls" in text or "hostname" in text:
            return DirectoryError("tls_error", type(exc).__name__)
        return DirectoryError("server_unavailable", type(exc).__name__)

    # -------------------------------------------------------------- операции
    def _service_connection(self):
        """Bind сервисной учётки. Её ошибки НЕ выдаются за ошибки пароля пользователя."""
        try:
            return self._connection(self._s.ldap_bind_dn, self._s.ldap_bind_password)
        except DirectoryError as exc:
            if exc.code in ("server_unavailable", "tls_error"):
                raise
            log.error("Ошибка bind сервисной учётки каталога", extra={"directory_code": exc.code})
            raise DirectoryError("service_account_error") from None

    def check_service_account(self) -> None:
        self._service_connection().unbind()

    def authenticate(self, login: str, password: str) -> DirectoryIdentity:
        from ldap3 import SUBTREE
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars

        if not password:
            raise DirectoryError("invalid_credentials", "пустой пароль")  # пустой пароль = анонимный bind!
        value, is_upn = parse_login(login)
        attr = "userPrincipalName" if is_upn else self._s.ldap_login_attribute

        svc = self._service_connection()
        try:
            flt = f"(&(objectCategory=person)(objectClass=user)({attr}={escape_filter_chars(value)}))"
            attrs = ["objectGUID", "distinguishedName", "sAMAccountName", "userPrincipalName",
                     self._s.ldap_display_name_attribute, self._s.ldap_email_attribute, "userAccountControl"]
            try:
                svc.search(self._s.ldap_base_dn, flt, search_scope=SUBTREE, attributes=attrs, size_limit=2)
            except LDAPException as exc:
                raise self._transport_error(exc) from None
            entries = [e for e in (svc.response or []) if e.get("type") == "searchResEntry"]
            if len(entries) == 0:
                raise DirectoryError("user_not_found")
            if len(entries) > 1:
                raise DirectoryError("ambiguous_user")
            entry = entries[0]
            dn = entry["dn"]
            raw = entry.get("raw_attributes", {})
            vals = entry.get("attributes", {})

            guid_raw = (raw.get("objectGUID") or [b""])[0]
            if len(guid_raw) != 16:
                raise DirectoryError("directory_data_error", "objectGUID отсутствует")
            uac = vals.get("userAccountControl")
            if isinstance(uac, int) and uac & _UAC_DISABLED:
                raise DirectoryError("account_disabled")

            # Проверка пароля: bind от имени самого пользователя.
            user_conn = self._connection(dn, password)
            user_conn.unbind()

            # Группы с учётом вложенности — одним запросом.
            groups: set[str] = set()
            try:
                gflt = f"(&(objectClass=group)(member:{LDAP_MATCHING_RULE_IN_CHAIN}:={escape_filter_chars(dn)}))"
                for item in svc.extend.standard.paged_search(
                    self._s.ldap_base_dn, gflt, search_scope=SUBTREE,
                    attributes=["distinguishedName"], paged_size=500, generator=True,
                ):
                    if item.get("type") == "searchResEntry":
                        groups.add(str(item["dn"]).lower())
            except LDAPException as exc:
                raise self._transport_error(exc) from None

            def first(name: str) -> str | None:
                v = vals.get(name)
                if isinstance(v, list):
                    v = v[0] if v else None
                v = str(v).strip() if v not in (None, "") else None
                return v

            sam = first("sAMAccountName") or value
            return DirectoryIdentity(
                ad_guid=guid_from_bytes(guid_raw),
                dn=dn,
                sam_account_name=sam,
                upn=first("userPrincipalName"),
                display_name=first(self._s.ldap_display_name_attribute) or sam,
                email=first(self._s.ldap_email_attribute),
                groups=frozenset(groups),
                source=self._s.name, for_users=self._s.use_for_users, for_admins=self._s.use_for_admins,
            )
        finally:
            try:
                svc.unbind()
            except Exception:  # noqa: BLE001
                pass

    def members(self, group_dn: str, limit: int = 300) -> list[dict]:
        """Участники группы (с учётом вложенных): имя, логин и адрес электронной почты — для рассылки материалов руководителям-группам."""
        from ldap3 import SUBTREE
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars

        svc = self._service_connection()
        try:
            flt = (f"(&(objectCategory=person)(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
                   f"(memberOf:{LDAP_MATCHING_RULE_IN_CHAIN}:={escape_filter_chars(group_dn)}))")
            attrs = ["objectGUID", "sAMAccountName", self._s.ldap_display_name_attribute, self._s.ldap_email_attribute]
            try:
                svc.search(self._s.ldap_base_dn, flt, search_scope=SUBTREE, attributes=attrs, size_limit=limit)
            except LDAPException as exc:
                raise self._transport_error(exc) from None
            out: list[dict] = []
            for e in svc.response or []:
                if e.get("type") != "searchResEntry":
                    continue
                vals = e.get("attributes", {})

                def one(name: str) -> str:
                    v = vals.get(name)
                    v = v[0] if isinstance(v, list) and v else v
                    return str(v) if v not in (None, "") else ""

                out.append({"name": one(self._s.ldap_display_name_attribute) or one("sAMAccountName"), "sam": one("sAMAccountName"), "email": one(self._s.ldap_email_attribute)})
            return out[:limit]
        finally:
            try:
                svc.unbind()
            except Exception:  # noqa: BLE001
                pass

    def search(self, kind: str, query: str, limit: int = 20) -> list[dict]:
        """Поиск групп/пользователей для настройки доступа (сервисная учётка, только чтение)."""
        from ldap3 import SUBTREE
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars

        q = escape_filter_chars(query.strip())
        if len(q) < 2:
            return []
        if kind == "group":
            flt = f"(&(objectClass=group)(|(cn=*{q}*)(sAMAccountName=*{q}*)(displayName=*{q}*)))"
            attrs = ["distinguishedName", "cn", "description"]
        elif kind == "user":
            flt = (f"(&(objectCategory=person)(objectClass=user)(|(sAMAccountName=*{q}*)(displayName=*{q}*)"
                   f"(userPrincipalName=*{q}*)({self._s.ldap_email_attribute}=*{q}*)))")
            attrs = ["objectGUID", "distinguishedName", "sAMAccountName", self._s.ldap_display_name_attribute, self._s.ldap_email_attribute]
        else:
            raise DirectoryError("bad_kind")
        svc = self._service_connection()
        try:
            try:
                svc.search(self._s.ldap_base_dn, flt, search_scope=SUBTREE, attributes=attrs, size_limit=limit)
            except LDAPException as exc:
                raise self._transport_error(exc) from None
            out: list[dict] = []
            for e in svc.response or []:
                if e.get("type") != "searchResEntry":
                    continue
                vals, raw = e.get("attributes", {}), e.get("raw_attributes", {})

                def one(name: str) -> str:
                    v = vals.get(name)
                    v = v[0] if isinstance(v, list) and v else v
                    return str(v) if v not in (None, "") else ""

                if kind == "group":
                    out.append({"kind": "group", "ref": str(e["dn"]), "name": one("cn"), "description": one("description")})
                else:
                    g = (raw.get("objectGUID") or [b""])[0]
                    if len(g) == 16:
                        out.append({"kind": "user", "ref": guid_from_bytes(g), "name": one(self._s.ldap_display_name_attribute) or one("sAMAccountName"),
                                    "sam": one("sAMAccountName"), "email": one(self._s.ldap_email_attribute)})
            return out[:limit]
        finally:
            try:
                svc.unbind()
            except Exception:  # noqa: BLE001
                pass


def diagnose_ldap(cfg: LdapConfig) -> dict:
    """Пошаговая проверка подключения: DNS → TCP → TLS (цепочка и имя узла) → вход сервисной учётной записи → чтение Base DN.
    Каждый этап — с понятной причиной; секреты в ответ не попадают. Реальная проверка сертификата выполняется по набору CA."""
    import socket  # noqa: PLC0415
    import time  # noqa: PLC0415
    from urllib.parse import urlsplit  # noqa: PLC0415

    stages: list[dict] = []

    def add(name: str, ok: bool | None, message: str, ms: int = 0) -> bool:
        stages.append({"stage": name, "ok": ok, "message": message, "ms": ms})
        return bool(ok)

    if not cfg.ldap_uri_list:
        add("config", False, "Адрес сервера не задан.")
        return {"ok": False, "stages": stages}
    if not cfg.ldap_ca_file:
        add("config", False, "Не загружен CA-сертификат: проверка цепочки невозможна. Добавьте корневой сертификат в разделе «Сертификаты (CA)».")
        return {"ok": False, "stages": stages}
    add("config", True, f"Сервер(ы): {', '.join(cfg.ldap_uri_list)}")
    parts = urlsplit(cfg.ldap_uri_list[0])
    host, port = parts.hostname or "", parts.port or (389 if cfg.starttls else 636)

    t = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        add("dns", True, f"{host} → {infos[0][4][0]}", int((time.monotonic() - t) * 1000))
    except OSError as exc:
        add("dns", False, f"DNS: имя «{host}» не найдено ({exc.strerror or type(exc).__name__}). Проверьте имя сервера и DNS на сервере приложения.")
        return {"ok": False, "stages": stages}

    t = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=cfg.ldap_timeout_seconds)
        add("tcp", True, f"Соединение с {host}:{port} установлено.", int((time.monotonic() - t) * 1000))
    except OSError as exc:
        add("tcp", False, f"TCP: нет соединения с {host}:{port} ({exc.strerror or type(exc).__name__}). Проверьте порт, межсетевой экран и маршрут.")
        return {"ok": False, "stages": stages}

    if not cfg.starttls:
        t = time.monotonic()
        try:
            ctx = ssl.create_default_context(cafile=cfg.ldap_ca_file)
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
                add("tls", True, f"TLS {tls.version()}: сертификат сервера проверен по загруженным CA (узел «{host}»).", int((time.monotonic() - t) * 1000))
                na = cert.get("notAfter")
                if na:
                    add("certificate", True, f"Сертификат сервера действителен до {na}.")
        except ssl.SSLCertVerificationError as exc:
            reason = (getattr(exc, "verify_message", "") or str(exc)).lower()
            hint = ("имя узла не совпадает с сертификатом — подключайтесь по имени из сертификата (SAN)" if "hostname" in reason or "match" in reason
                    else "сертификат просрочен" if "expired" in reason
                    else "цепочка не доверена: загрузите корневой (и промежуточные) сертификаты CA, которым подписан сертификат сервера"
                    if "unable to get" in reason or "self" in reason or "unknown" in reason else getattr(exc, "verify_message", "") or "проверка не пройдена")
            add("tls", False, f"Проверка сертификата не пройдена: {hint}.")
            return {"ok": False, "stages": stages}
        except (ssl.SSLError, OSError) as exc:
            add("tls", False, f"TLS: не удалось установить защищённое соединение ({type(exc).__name__}). Убедитесь, что на порту {port} работает LDAPS.")
            return {"ok": False, "stages": stages}
    else:
        sock.close()
        add("tls", None, "STARTTLS проверяется вместе со входом сервисной учётной записи.")

    t = time.monotonic()
    try:
        d = LdapDirectory(cfg)
        conn = d._service_connection()  # noqa: SLF001
        add("bind", True, "Сервисная учётная запись вошла успешно.", int((time.monotonic() - t) * 1000))
    except DirectoryError as exc:
        msg = {"service_account_error": "Вход сервисной учётной записи отклонён: проверьте DN/логин и пароль.",
               "tls_error": "TLS/сертификат не принят при входе (проверьте CA и имя узла).",
               "server_unavailable": "Сервер каталога не отвечает на запрос входа."}.get(exc.code, f"Ошибка входа ({exc.code}).")
        add("bind", False, msg)
        return {"ok": False, "stages": stages}

    try:
        from ldap3 import BASE  # noqa: PLC0415

        t = time.monotonic()
        ok = conn.search(cfg.ldap_base_dn, "(objectClass=*)", search_scope=BASE, attributes=["distinguishedName"], size_limit=1)
        if ok or conn.result.get("result") == 0:
            add("base_dn", True, "Base DN доступен для чтения.", int((time.monotonic() - t) * 1000))
        else:
            add("base_dn", False, f"Base DN недоступен: {conn.result.get('description', 'ошибка поиска')}. Проверьте значение и права сервисной учётной записи.")
    except Exception as exc:  # noqa: BLE001
        add("base_dn", False, f"Чтение Base DN не удалось ({type(exc).__name__}).")
    finally:
        try:
            conn.unbind()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": all(s["ok"] is not False for s in stages), "stages": stages}
