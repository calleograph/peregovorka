"""Шифрованный контейнер резервной копии конфигурации.

Формат файла: МЕТКА(7 байт) | длина заголовка (4 байта, big-endian) | заголовок (JSON) | шифртекст.
Заголовок открыт, но защищён от подмены (входит в AAD): версия формата и схемы, версия приложения, дата, параметры вывода ключа, nonce — никаких секретов и идентификаторов.
Шифрование — AES-256-GCM (аутентифицированное: при неверном пароле или повреждении расшифровка не удаётся целиком), ключ — scrypt от пароля со случайной солью.
Пароль — 20 случайных символов (алфавит без похожих знаков), генерируется сервером и показывается один раз; на сервере не сохраняется.
Защита от «архивных атак»: предел размера файла и размера после распаковки (степень сжатия ограничена), предел параметров scrypt из заголовка (чужой файл не заставит сервер занять гигабайты памяти).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
import zlib
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .registry import FORMAT_VERSION, SCHEMA_VERSION

MAGIC = b"PGCFG\x01\n"
KIND = "peregovorka-config"
MAX_FILE_BYTES = 64 << 20          # архив настроек — мегабайты; 64 МиБ с большим запасом
MAX_PLAIN_BYTES = 256 << 20        # предел после распаковки
MAX_HEADER_BYTES = 8 << 10
KDF_N, KDF_R, KDF_P = 1 << 15, 8, 1
KDF_LIMITS = {"n": 1 << 17, "r": 16, "p": 4}
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"       # без 0 O 1 I l
PASSWORD_LEN = 20


class ContainerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def generate_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(PASSWORD_LEN))


def normalize_password(p: str) -> str:
    return "".join(ch for ch in (p or "") if not ch.isspace() and ch not in "-–—")


def _key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(normalize_password(password).encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=256 << 20)


def seal(payload: dict, password: str, *, app_version: str) -> bytes:
    """Упаковать и зашифровать. Всё делается в памяти: на диск ничего не пишется."""
    plain = zlib.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    salt, nonce = os.urandom(16), os.urandom(12)
    header = {"kind": KIND, "format": FORMAT_VERSION, "schema": SCHEMA_VERSION, "app_version": app_version, "created_at": datetime.now(timezone.utc).isoformat(),
              "kdf": {"alg": "scrypt", "n": KDF_N, "r": KDF_R, "p": KDF_P, "salt": base64.b64encode(salt).decode()}, "cipher": "AES-256-GCM", "nonce": base64.b64encode(nonce).decode()}
    hb = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    prefix = MAGIC + struct.pack(">I", len(hb)) + hb
    ct = AESGCM(_key(password, salt, KDF_N, KDF_R, KDF_P)).encrypt(nonce, plain, prefix)
    return prefix + ct


def read_header(blob: bytes) -> tuple[dict, bytes]:
    """Заголовок (без пароля) и «префикс» для проверки подлинности. Проверяет только формат и версии."""
    if len(blob) > MAX_FILE_BYTES:
        raise ContainerError("too_large", f"Файл слишком большой ({len(blob) >> 20} МиБ): архив настроек не бывает больше {MAX_FILE_BYTES >> 20} МиБ.")
    if len(blob) < len(MAGIC) + 4 or not blob.startswith(MAGIC):
        raise ContainerError("not_archive", "Это не архив конфигурации Peregovorka (неверный формат файла).")
    (hl,) = struct.unpack(">I", blob[len(MAGIC):len(MAGIC) + 4])
    if hl > MAX_HEADER_BYTES or len(blob) < len(MAGIC) + 4 + hl:
        raise ContainerError("bad_header", "Архив повреждён: некорректный заголовок.")
    raw = blob[len(MAGIC) + 4:len(MAGIC) + 4 + hl]
    try:
        h = json.loads(raw.decode("utf-8"))
        kdf = h["kdf"]
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        base64.b64decode(kdf["salt"], validate=True)
        base64.b64decode(h["nonce"], validate=True)
        int(h["format"]), int(h["schema"])
    except Exception:  # noqa: BLE001
        raise ContainerError("bad_header", "Архив повреждён: некорректный заголовок.") from None
    if h.get("kind") != KIND or kdf.get("alg") != "scrypt" or h.get("cipher") != "AES-256-GCM":
        raise ContainerError("not_archive", "Это не архив конфигурации Peregovorka.")
    if n > KDF_LIMITS["n"] or r > KDF_LIMITS["r"] or p > KDF_LIMITS["p"] or n < 2 or n & (n - 1):
        raise ContainerError("bad_header", "Архив отклонён: недопустимые параметры шифрования.")
    if int(h["format"]) > FORMAT_VERSION:
        raise ContainerError("too_new", f"Архив создан более новой версией Peregovorka (формат {h['format']}, эта версия понимает до {FORMAT_VERSION}). Обновите сервер до версии {h.get('app_version', '—')} или новее и повторите импорт.")
    if int(h["schema"]) > SCHEMA_VERSION:
        raise ContainerError("too_new", f"Архив создан более новой версией Peregovorka (схема настроек {h['schema']}, эта версия понимает до {SCHEMA_VERSION}). Обновите сервер до версии {h.get('app_version', '—')} или новее и повторите импорт.")
    return h, blob[:len(MAGIC) + 4 + hl]


def open_(blob: bytes, password: str) -> tuple[dict, dict]:
    """Расшифровать: возвращает (заголовок, содержимое). Неверный пароль и повреждение неотличимы (так устроено аутентифицированное шифрование)."""
    h, prefix = read_header(blob)
    kdf = h["kdf"]
    key = _key(password, base64.b64decode(kdf["salt"]), int(kdf["n"]), int(kdf["r"]), int(kdf["p"]))
    try:
        plain = AESGCM(key).decrypt(base64.b64decode(h["nonce"]), blob[len(prefix):], prefix)
    except InvalidTag:
        raise ContainerError("bad_password", "Неверный пароль или архив повреждён. Проверьте пароль (20 символов, показан при скачивании) и целостность файла.") from None
    try:
        d = zlib.decompressobj()
        raw = d.decompress(plain, MAX_PLAIN_BYTES + 1)
        if len(raw) > MAX_PLAIN_BYTES or d.unconsumed_tail:
            raise ContainerError("too_large", "Содержимое архива слишком велико после распаковки.")
        payload = json.loads(raw.decode("utf-8"))
    except ContainerError:
        raise
    except Exception:  # noqa: BLE001
        raise ContainerError("corrupted", "Архив повреждён: содержимое не читается.") from None
    if not isinstance(payload, dict):
        raise ContainerError("corrupted", "Архив повреждён: неожиданная структура содержимого.")
    return h, payload
