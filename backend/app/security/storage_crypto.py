"""Шифрование хранимых материалов (чат и стенограммы): AES-256-GCM, ключи данных под защитой мастер-ключа.

Устройство.
* Ключ данных (DEK) — 256 случайных бит. В базе (`storage_keys`) лежит только его «обёртка»: DEK зашифрован AES-256-GCM ключом-обёрткой (KEK), который выводится
  из `APP_MASTER_KEY` (HKDF-SHA256, отдельная метка). Мастер-ключ в базе не хранится, поэтому дамп базы без него бесполезен.
* Шифртекст значения: `pgenc1:<номер ключа>:<base64(nonce12 + шифртекст + тег16)>`. Номер ключа позволяет менять активный ключ, не теряя доступ к прежним записям.
* AAD привязывает шифртекст к таблице и встрече (и к идентификатору реплики): перенос шифртекста в другую встречу даст ошибку проверки подлинности.
* Включается для комнаты (`rooms.encrypt_storage`); запись шифруется перед вставкой, читается прозрачно (события ORM, см. models/crypto_events.py). Прежние записи
  не меняются при включении режима — для них есть явное «зашифровать существующие».
Граница защиты: шифруется содержимое в базе и в её резервных копиях. Приложение расшифровывает записи в памяти при чтении (стенограмма, документы, поиск) —
администратор сервера с доступом к процессу и мастер-ключу содержимое получить может; это защита хранилища, а не сквозное шифрование.
"""
from __future__ import annotations

import base64
import os
import threading

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = "pgenc1:"
_INFO = b"peregovorka-storage-kek-v1"


class StorageCryptoError(Exception):
    pass


def derive_kek(master_key_b64: str) -> bytes:
    try:
        raw = base64.b64decode(master_key_b64 or "", validate=True)
    except (ValueError, TypeError):
        raw = b""
    if len(raw) != 32:
        raise StorageCryptoError("APP_MASTER_KEY не задан или некорректен: шифровать хранилище нельзя")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(raw)


def wrap_key(kek: bytes, key_id: int, dek: bytes) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(kek).encrypt(nonce, dek, f"storage_key.{key_id}".encode())).decode("ascii")


def unwrap_key(kek: bytes, key_id: int, wrapped: str) -> bytes:
    raw = base64.b64decode(wrapped)
    try:
        return AESGCM(kek).decrypt(raw[:12], raw[12:], f"storage_key.{key_id}".encode())
    except Exception as exc:  # noqa: BLE001
        raise StorageCryptoError(f"ключ хранилища {key_id} не расшифрован: неверный APP_MASTER_KEY или повреждённые данные") from exc


class Keyring:
    """Ключи данных в памяти процесса. Читается из разных потоков (события ORM выполняются в потоках драйвера), поэтому доступ под замком."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[int, AESGCM] = {}
        self.active: int | None = None

    def load(self, items: list[tuple[int, bytes, bool]]) -> None:
        """items: (номер, ключ, активный). Заменяет содержимое целиком."""
        with self._lock:
            self._keys = {i: AESGCM(k) for i, k, _ in items}
            act = [i for i, _, a in items if a]
            self.active = max(act) if act else (max(self._keys) if self._keys else None)

    def clear(self) -> None:
        with self._lock:
            self._keys, self.active = {}, None

    @property
    def ready(self) -> bool:
        return self.active is not None

    def seal(self, plain: str, aad: bytes) -> str:
        with self._lock:
            if self.active is None:
                raise StorageCryptoError("Ключ шифрования хранилища не загружен")
            kid, aes = self.active, self._keys[self.active]
        nonce = os.urandom(12)
        return f"{MAGIC}{kid}:" + base64.b64encode(nonce + aes.encrypt(nonce, plain.encode("utf-8"), aad)).decode("ascii")

    def open_(self, value: str, aad: bytes) -> str:
        if not is_sealed(value):
            return value
        try:
            _, kid_s, body = value.split(":", 2)
            kid = int(kid_s)
            raw = base64.b64decode(body)
        except (ValueError, TypeError) as exc:
            raise StorageCryptoError("повреждённый шифртекст") from exc
        with self._lock:
            aes = self._keys.get(kid)
        if aes is None:
            raise StorageCryptoError(f"нет ключа хранилища №{kid}")
        try:
            return aes.decrypt(raw[:12], raw[12:], aad).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise StorageCryptoError("не удалось расшифровать запись (неверный ключ или подмена данных)") from exc


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(MAGIC)


def chat_aad(meeting_id) -> bytes:
    return f"chat:{meeting_id}".encode()


def segment_aad(meeting_id, segment_uid) -> bytes:
    return f"segment:{meeting_id}:{segment_uid}".encode()


keyring = Keyring()          # общий для процесса; загружается при запуске приложения и после смены ключа
