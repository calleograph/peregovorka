"""Управляемый набор CA-сертификатов (общий для LDAPS и, по желанию, SMTP).

Администратор загружает корневой/промежуточный сертификат в формате PEM или DER (файлом либо текстом PEM). Перед сохранением сертификат
разбирается и показывается: Subject, Issuer, серийный номер, отпечаток SHA-256, срок действия, признак CA. Закрытые ключи НЕ принимаются:
всё, где есть PRIVATE KEY (в любом виде), отклоняется целиком и нигде не сохраняется.

Из набора собирается файл `<DATA_DIR>/ca/bundle.pem` (его читают ldap3 и ssl); если в окружении задан прежний `LDAP_CA_FILE`, его сертификаты
добавляются к набору (прежние установки продолжают работать).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtensionOID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CaCertificate

log = logging.getLogger("app.ca")

MAX_UPLOAD = 256 * 1024
MAX_CERTS_PER_UPLOAD = 20
_PEM_BLOCK = re.compile(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)
_PRIVATE = re.compile(rb"PRIVATE KEY|ENCRYPTED PRIVATE|BEGIN RSA PRIVATE|BEGIN EC PRIVATE|BEGIN OPENSSH PRIVATE|PKCS12|BEGIN PGP", re.I)


class CertError(Exception):
    """Ошибка загрузки сертификата — текст безопасен для показа."""


@dataclass
class CertInfo:
    subject: str
    issuer: str
    serial: str
    sha256: str
    not_before: datetime
    not_after: datetime
    is_ca: bool
    self_signed: bool
    pem: str

    def public(self) -> dict:
        now = datetime.now(timezone.utc)
        return {"subject": self.subject, "issuer": self.issuer, "serial": self.serial, "sha256": self.sha256,
                "not_before": self.not_before.isoformat(), "not_after": self.not_after.isoformat(), "is_ca": self.is_ca,
                "self_signed": self.self_signed, "expired": self.not_after < now, "not_yet_valid": self.not_before > now,
                "type": ("корневой (самоподписанный) CA" if self.self_signed and self.is_ca else "промежуточный CA" if self.is_ca
                         else "самоподписанный сертификат (не CA)" if self.self_signed else "сертификат сервера/конечный (не CA)")}


def _rfc4514(name: x509.Name) -> str:
    return name.rfc4514_string()


def _info(cert: x509.Certificate) -> CertInfo:
    der = cert.public_bytes(serialization.Encoding.DER)
    try:
        bc = cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
        is_ca = bool(bc.ca)
    except x509.ExtensionNotFound:
        is_ca = False
    nb = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(tzinfo=timezone.utc)
    na = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
    return CertInfo(subject=_rfc4514(cert.subject), issuer=_rfc4514(cert.issuer), serial=format(cert.serial_number, "X"),
                    sha256=hashlib.sha256(der).hexdigest(), not_before=nb, not_after=na, is_ca=is_ca,
                    self_signed=cert.subject == cert.issuer,
                    pem=cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))


def parse_certificates(data: bytes) -> list[CertInfo]:
    """PEM (один или несколько сертификатов) либо DER. Закрытые ключи и контейнеры PKCS#12 отклоняются."""
    if not data or not data.strip():
        raise CertError("Файл пуст.")
    if len(data) > MAX_UPLOAD:
        raise CertError("Файл слишком большой для сертификата (больше 256 КБ).")
    if _PRIVATE.search(data):
        raise CertError("В файле есть закрытый ключ или контейнер с ключом. Закрытые ключи не принимаются — загрузите только сертификат (открытую часть).")
    certs: list[x509.Certificate] = []
    blocks = _PEM_BLOCK.findall(data)
    try:
        if blocks:
            for b in blocks[:MAX_CERTS_PER_UPLOAD + 1]:
                certs.append(x509.load_pem_x509_certificate(b))
        else:
            certs.append(x509.load_der_x509_certificate(data))
    except ValueError:
        raise CertError("Не удалось прочитать сертификат: ожидается PEM (-----BEGIN CERTIFICATE-----) или DER.") from None
    if len(certs) > MAX_CERTS_PER_UPLOAD:
        raise CertError(f"В одном файле больше {MAX_CERTS_PER_UPLOAD} сертификатов.")
    seen: set[str] = set()
    out: list[CertInfo] = []
    for c in certs:
        info = _info(c)
        if info.sha256 not in seen:
            seen.add(info.sha256)
            out.append(info)
    return out


def decode_upload(body: dict) -> bytes:
    """Тело запроса: {"pem": "текст"} либо {"data_base64": "…"} (файл, в том числе DER)."""
    if isinstance(body.get("pem"), str) and body["pem"].strip():
        return body["pem"].encode("utf-8", "ignore")
    raw = body.get("data_base64")
    if isinstance(raw, str) and raw:
        try:
            return base64.b64decode(raw, validate=False)
        except Exception:  # noqa: BLE001
            raise CertError("Файл передан в неверной кодировке.") from None
    raise CertError("Вставьте сертификат текстом или выберите файл.")


class CaBundleService:
    def __init__(self, data_dir: str, legacy_ca_file: str = ""):
        self._dir = Path(data_dir) / "ca"
        self._legacy = legacy_ca_file

    @property
    def bundle_path(self) -> str:
        return str(self._dir / "bundle.pem")

    def _legacy_pems(self) -> list[str]:
        if not self._legacy:
            return []
        try:
            return [b.decode("ascii", "ignore") + "\n" for b in _PEM_BLOCK.findall(Path(self._legacy).read_bytes())]
        except OSError:
            return []

    async def rebuild(self, db: AsyncSession) -> str:
        """Пересобирает файл набора (атомарно). Возвращает путь; если сертификатов нет — пустую строку."""
        rows = (await db.execute(select(CaCertificate).order_by(CaCertificate.created_at))).scalars().all()
        pems = [r.pem for r in rows] + self._legacy_pems()
        if not pems:
            try:
                Path(self.bundle_path).unlink(missing_ok=True)
            except OSError:
                pass
            return ""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._dir / "bundle.pem.tmp"
        tmp.write_text("".join(p if p.endswith("\n") else p + "\n" for p in pems), encoding="ascii")
        os.replace(tmp, self.bundle_path)
        return self.bundle_path

    async def stage(self, db: AsyncSession) -> tuple[Path | None, Path]:
        """Подготовить новый набор рядом с боевым по строкам базы (в т.ч. ещё не зафиксированным в текущей транзакции): (временный файл или None, если набор должен исчезнуть; боевой)."""
        rows = (await db.execute(select(CaCertificate).order_by(CaCertificate.created_at))).scalars().all()
        pems = [r.pem for r in rows] + self._legacy_pems()
        final = Path(self.bundle_path)
        if not pems:
            return None, final
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = final.with_name(f"{final.name}.import-{os.urandom(4).hex()}.tmp")
        tmp.write_text("".join(p if p.endswith("\n") else p + "\n" for p in pems), encoding="ascii")
        return tmp, final

    def current_path(self) -> str:
        """Путь к собранному файлу для синхронного кода (ldap3/ssl); пусто — набора нет."""
        p = Path(self.bundle_path)
        return str(p) if p.is_file() else (self._legacy if self._legacy and Path(self._legacy).is_file() else "")

    # ------------------------------------------------------------------------ операции
    async def list(self, db: AsyncSession) -> list[dict]:
        rows = (await db.execute(select(CaCertificate).order_by(CaCertificate.created_at))).scalars().all()
        now = datetime.now(timezone.utc)
        out = [{"id": str(r.id), "label": r.label, "subject": r.subject, "issuer": r.issuer, "serial": r.serial, "sha256": r.sha256,
                "not_before": r.not_before.isoformat(), "not_after": r.not_after.isoformat(), "is_ca": r.is_ca, "self_signed": r.self_signed,
                "expired": r.not_after < now, "added_by": r.added_by, "created_at": r.created_at.isoformat(), "source": "web"} for r in rows]
        for pem in self._legacy_pems():
            try:
                info = parse_certificates(pem.encode())[0]
            except CertError:
                continue
            out.append({"id": f"file:{info.sha256[:12]}", "label": "Файл LDAP_CA_FILE (прежняя настройка)", "subject": info.subject, "issuer": info.issuer,
                        "serial": info.serial, "sha256": info.sha256, "not_before": info.not_before.isoformat(), "not_after": info.not_after.isoformat(),
                        "is_ca": info.is_ca, "self_signed": info.self_signed, "expired": info.not_after < now, "added_by": None, "created_at": None, "source": "file"})
        return out

    async def add(self, db: AsyncSession, data: bytes, label: str, actor: str, *, confirm_non_ca: bool = False) -> tuple[list[CertInfo], list[CertInfo]]:
        """Возвращает (добавленные, уже были). Сертификат конечного сервера (не CA) добавляется только с подтверждением."""
        infos = parse_certificates(data)
        non_ca = [i for i in infos if not i.is_ca and not i.self_signed]
        if non_ca and not confirm_non_ca:
            raise CertError("Это сертификат сервера, а не удостоверяющего центра (CA). Для проверки цепочки нужен корневой или промежуточный CA. "
                            "Если вы уверены, подтвердите добавление.")
        expired = [i for i in infos if i.not_after < datetime.now(timezone.utc)]
        if expired and len(expired) == len(infos):
            raise CertError("Срок действия сертификата истёк — добавлять его нет смысла.")
        label = (label or "").strip()[:200]
        added, existing = [], []
        have = {r for r in (await db.execute(select(CaCertificate.sha256))).scalars().all()}
        for i in infos:
            if i.sha256 in have:
                existing.append(i)
                continue
            db.add(CaCertificate(label=label or i.subject[:200], subject=i.subject[:1000], issuer=i.issuer[:1000], serial=i.serial[:100], sha256=i.sha256,
                                 not_before=i.not_before, not_after=i.not_after, is_ca=i.is_ca, self_signed=i.self_signed, pem=i.pem, added_by=actor))
            added.append(i)
        await db.flush()
        await self.rebuild(db)
        return added, existing

    async def remove(self, db: AsyncSession, cert_id: str) -> CaCertificate:
        import uuid  # noqa: PLC0415

        try:
            row = await db.get(CaCertificate, uuid.UUID(cert_id))
        except ValueError:
            row = None
        if row is None:
            raise CertError("Сертификат не найден.")
        await db.delete(row)
        await db.flush()
        await self.rebuild(db)
        return row
