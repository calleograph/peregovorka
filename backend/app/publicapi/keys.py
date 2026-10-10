"""Ключи API: генерация, разбор, хеш. В базе — SHA-256 секрета (секрет случайный, 256 бит: медленный хеш пароля не нужен), открытая часть — key_id."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets

PREFIX = "pgk"
_KEY_RE = re.compile(r"^pgk_([0-9a-f]{8})_([A-Za-z0-9_-]{43})$")


def new_key() -> tuple[str, str, str, str]:
    """(полный ключ, key_id, sha256 секрета, последние 4 символа)."""
    key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return f"{PREFIX}_{key_id}_{secret}", key_id, hashlib.sha256(secret.encode()).hexdigest(), secret[-4:]


def parse_key(raw: str) -> tuple[str, str] | None:
    m = _KEY_RE.match(raw or "")
    return (m.group(1), m.group(2)) if m else None


def secret_matches(secret: str, secret_hash: str) -> bool:
    return hmac.compare_digest(hashlib.sha256(secret.encode()).hexdigest(), secret_hash)


def masked(key_id: str, last4: str) -> str:
    return f"{PREFIX}_{key_id}_••••••••{last4}"


def normalize_allowlist(values: list[str] | None) -> list[str]:
    """Нормализованный список адресов/сетей; ошибка в записи — ValueError (при сохранении), а не молчаливый пропуск."""
    return [str(ipaddress.ip_network(str(v).strip(), strict=False)) for v in values or [] if str(v).strip()]


def ip_allowed(ip: str, allowlist: list[str] | None) -> bool:
    if not allowlist:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in allowlist:
        try:
            if addr in ipaddress.ip_network(str(n), strict=False):
                return True
        except ValueError:
            continue
    return False
