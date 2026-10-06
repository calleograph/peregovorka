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
from dataclasses import dataclass
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


class DirectoryClient(Protocol):
    def authenticate(self, login: str, password: str) -> DirectoryIdentity:  # blocking
        ...

    def check_service_account(self) -> None:  # blocking; для readiness/диагностики
        ...

    def search(self, kind: str, query: str, limit: int = 20) -> list[dict]:  # blocking; kind: group | user
        ...


_LOGIN_FORBIDDEN = re.compile(r'[\s"/\\\[\]:;|=,+*?<>\x00()]')


def parse_login(raw: str) -> tuple[str, bool]:
    """Нормализует введённый логин.

    Возвращает (значение, is_upn). Принимает `user`, `DOMAIN\\user`, `user@domain`.
    Для формы user@domain поиск идёт по userPrincipalName.
    """
    login = (raw or "").strip()
    if "\\" in login:
        login = login.split("\\", 1)[1]
    is_upn = "@" in login
    check = login.split("@", 1)[0] if is_upn else login
    if not login or len(login) > 256 or _LOGIN_FORBIDDEN.search(check) or login.count("@") > 1:
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

    def __init__(self, settings: Settings):
        if not settings.ldap_uri_list:
            raise DirectoryError("config_error", "LDAP_URIS не задан")
        if not settings.ldap_ca_file:
            raise DirectoryError("config_error", "LDAP_CA_FILE не задан: проверка сертификата обязательна")
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
                Server(parts.hostname, port=parts.port or 636, use_ssl=True, tls=tls,
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
                     self._s.ldap_display_name_attribute, "mail", "userAccountControl"]
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
                email=first("mail"),
                groups=frozenset(groups),
            )
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
                   f"(userPrincipalName=*{q}*)(mail=*{q}*)))")
            attrs = ["objectGUID", "distinguishedName", "sAMAccountName", self._s.ldap_display_name_attribute, "mail"]
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
                                    "sam": one("sAMAccountName"), "email": one("mail")})
            return out[:limit]
        finally:
            try:
                svc.unbind()
            except Exception:  # noqa: BLE001
                pass
