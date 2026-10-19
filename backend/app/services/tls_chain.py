"""Разбор цепочки сертификатов публичного HTTPS-адреса: что отдаёт сервер/прокси, чему доверяет этот контейнер, где нужно исправление.

Принципы безопасности:
* доверие определяется только системным хранилищем контейнера (плюс необязательный файл администратора OUTLOOK_CHECK_CA_FILE); сертификат сайта никогда не становится доверенным;
* проверка TLS не отключается: соединение без проверки (CERT_NONE) читает цепочку только для разбора и никогда не даёт «успех»;
* недостающие промежуточные сертификаты берутся только из расширения AIA самого сертификата, по http(s) на ПУБЛИЧНЫЕ адреса (внутренние, loopback и link-local отклоняются),
  ограниченного размера; полученный сертификат принимается, только если он подписал предыдущий и вся цепочка доходит до системного корня;
* собранная полная цепочка нужна, чтобы администратор поставил её на прокси; ничего на внешних устройствах эта служба не меняет.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID, NameOID

AIA_MAX_BYTES = 128 * 1024
AIA_MAX_FETCH = 4


def system_context(extra_ca_file: str | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context()                      # системное хранилище + check_hostname
    if extra_ca_file:
        try:
            ctx.load_verify_locations(cafile=extra_ca_file)  # CA организации, добавленный администратором ЯВНО (не сертификат проверяемого сайта)
        except (OSError, ssl.SSLError):
            pass
    return ctx


def _cn(n: x509.Name) -> str:
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME):
        v = n.get_attributes_for_oid(oid)
        if v:
            return str(v[0].value)
    return n.rfc4514_string()[:80]


def _utc(c: x509.Certificate) -> datetime:
    return c.not_valid_after_utc if hasattr(c, "not_valid_after_utc") else c.not_valid_after.replace(tzinfo=timezone.utc)


def fingerprint(c: x509.Certificate) -> str:
    return c.fingerprint(hashes.SHA256()).hex()


def describe(c: x509.Certificate, role: str) -> dict:
    return {"role": role, "subject": _cn(c.subject), "issuer": _cn(c.issuer), "not_after": _utc(c).isoformat(timespec="seconds"),
            "days_left": (_utc(c) - datetime.now(timezone.utc)).days, "self_signed": c.subject == c.issuer, "sha256": fingerprint(c)}


def to_pem(certs: list[x509.Certificate]) -> str:
    return "".join(c.public_bytes(serialization.Encoding.PEM).decode("ascii") for c in certs)


def presented_chain(host: str, port: int, timeout: float) -> list[x509.Certificate]:
    """Цепочка в том виде, в каком её ОТДАЁТ сервер (лист первым). Соединение без проверки — только чтение, доверие не выдаётся."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
        try:
            ders = [c.public_bytes(ssl._ssl.ENCODING_DER) for c in s._sslobj.get_unverified_chain()]     # CPython ≥3.10; публичный аналог появился в 3.13
        except Exception:  # noqa: BLE001
            ders = []
        if not ders:
            der = s.getpeercert(binary_form=True)
            ders = [der] if der else []
    return [x509.load_der_x509_certificate(d) for d in ders]


def system_roots(ctx: ssl.SSLContext) -> list[x509.Certificate]:
    out = []
    for d in ctx.get_ca_certs(binary_form=True):
        try:
            out.append(x509.load_der_x509_certificate(d))
        except ValueError:
            continue
    return out


def _verify(leaf: x509.Certificate, intermediates: list[x509.Certificate], roots: list[x509.Certificate], host: str) -> bool:
    from cryptography.x509.verification import PolicyBuilder, Store, VerificationError  # noqa: PLC0415

    if not roots:
        return False
    try:
        name = x509.IPAddress(ipaddress.ip_address(host)) if _is_ip(host) else x509.DNSName(host)
        PolicyBuilder().store(Store(roots)).build_server_verifier(name).verify(leaf, intermediates)
        return True
    except (VerificationError, ValueError):
        return False


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def name_matches(leaf: x509.Certificate, host: str) -> bool:
    try:
        san = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    except x509.ExtensionNotFound:
        return False
    if _is_ip(host):
        return ipaddress.ip_address(host) in san.get_values_for_type(x509.IPAddress)
    h = host.lower().rstrip(".")
    for n in san.get_values_for_type(x509.DNSName):
        n = n.lower()
        if n == h or (n.startswith("*.") and h.count(".") >= 1 and h.split(".", 1)[1] == n[2:] and h.split(".", 1)[0] != ""):
            return True
    return False


def aia_urls(c: x509.Certificate) -> list[str]:
    try:
        ext = c.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in ext if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS and isinstance(d.access_location, x509.UniformResourceIdentifier)]


def _public_host(host: str) -> bool:
    """Только публичные адреса: AIA-ссылка приходит из чужого сертификата, внутрь сети через неё ходить нельзя (SSRF)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
    return bool(infos)


def fetch_aia(url: str, timeout: float = 5.0, opener=None) -> x509.Certificate | None:
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or not _public_host(u.hostname):
        return None
    try:
        with (opener or urllib.request.build_opener(urllib.request.HTTPRedirectHandler())).open(urllib.request.Request(url, headers={"User-Agent": "Peregovorka-tls-check"}), timeout=timeout) as r:
            data = r.read(AIA_MAX_BYTES + 1)
        if len(data) > AIA_MAX_BYTES:
            return None
        try:
            return x509.load_der_x509_certificate(data)
        except ValueError:
            return x509.load_pem_x509_certificate(data)
    except (OSError, ValueError):
        return None


def complete_via_aia(chain: list[x509.Certificate], roots: list[x509.Certificate], host: str, fetcher=fetch_aia) -> list[x509.Certificate] | None:
    """Достраивает цепочку недостающими промежуточными из AIA. Возвращает полную цепочку (лист … последний промежуточный), только если она доходит до системного корня."""
    work = list(chain)
    for _ in range(AIA_MAX_FETCH):
        if _verify(work[0], work[1:], roots, host):
            return work
        last = work[-1]
        nxt = None
        for url in aia_urls(last):
            cert = fetcher(url)
            if cert is not None and cert.subject == last.issuer:
                try:
                    last.verify_directly_issued_by(cert)
                except Exception:  # noqa: BLE001
                    continue
                nxt = cert
                break
        if nxt is None or any(fingerprint(nxt) == fingerprint(c) for c in work):
            return None
        work.append(nxt)
    return work if _verify(work[0], work[1:], roots, host) else None
