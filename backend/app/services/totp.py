"""Одноразовые коды (TOTP, RFC 6238): HMAC-SHA1, 6 цифр, шаг 30 с — совместимо с Google/Microsoft Authenticator, FreeOTP, Aegis и др.

Только стандартная библиотека, кроме QR-картинки (segno, чистый Python). Секрет пользователя хранится зашифрованным (ключ APP_MASTER_KEY, AAD привязан к
пользователю), резервные коды — только в виде хэша. Повтор уже принятого кода отклоняется: сохраняется номер последнего принятого шага.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

STEP_S = 30
DIGITS = 6
WINDOW = 1                       # допускаются соседние шаги (расхождение часов до ±30 с)
BACKUP_COUNT = 10
_pepper: bytes = b""             # ключ HMAC для хэшей резервных кодов (производный от APP_MASTER_KEY); без него хэш — просто SHA-256
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"           # без похожих знаков (0/o, 1/l/i)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10**DIGITS).zfill(DIGITS)


def code_at(secret: str, at: float | None = None) -> str:
    return _hotp(secret, int((time.time() if at is None else at) // STEP_S))


def verify(secret: str, code: str, last_step: int | None = None, at: float | None = None) -> int | None:
    """Номер принятого шага или None. Шаг не больше `last_step` (уже использованный) не принимается — защита от повтора перехваченного кода."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((time.time() if at is None else at) // STEP_S)
    hit: int | None = None
    for step in range(now - WINDOW, now + WINDOW + 1):          # без раннего выхода: время проверки не зависит от совпадения
        if hmac.compare_digest(_hotp(secret, step), code) and (last_step is None or step > last_step):
            hit = step
    return hit


def otpauth_uri(issuer: str, account: str, secret: str) -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP_S}"


def qr_svg(data: str) -> str:
    import segno  # noqa: PLC0415

    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False, svgns=True, nl=False)
    return buf.getvalue().decode("utf-8")


# ----------------------------------------------------------------------------------------------- резервные коды
def new_backup_codes(n: int = BACKUP_COUNT) -> list[str]:
    return ["".join(secrets.choice(_ALPHABET) for _ in range(10)) for _ in range(n)]


def format_backup(code: str) -> str:
    return f"{code[:5]}-{code[5:]}"


def normalize_backup(raw: str) -> str:
    return "".join(ch for ch in raw.lower() if ch.isalnum())


def looks_like_backup(raw: str) -> bool:
    return len(normalize_backup(raw)) == 10


def set_pepper(master_key_b64: str | None) -> None:
    global _pepper
    try:
        raw = base64.b64decode(master_key_b64 or "", validate=True)
    except (ValueError, TypeError):
        raw = b""
    _pepper = hmac.new(raw, b"peregovorka-backup-code-v1", hashlib.sha256).digest() if raw else b""


def hash_backup(user_id: str, code: str) -> str:
    """HMAC-SHA256 резервного кода с ключом сервера и привязкой к пользователю: утечка одной базы не даёт проверить догадки, а 50 бит энтропии плюс
    ограничение попыток делают перебор нереальным."""
    return hmac.new(_pepper or b"peregovorka-backup", f"{user_id}:{normalize_backup(code)}".encode(), hashlib.sha256).hexdigest()
