"""Шифрование секретов в состоянии покоя (AES-256-GCM), мастер-ключ — APP_MASTER_KEY.

Портировано из legacy/php/src/Security/SecretBox.php (там — файл var/data/master.key).
Здесь ключ приходит только из окружения (compose/.env), в БД не хранится.
Формат: base64( version(1) | nonce(12) | ciphertext+tag ).
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"


class SecretBoxError(Exception):
    pass


class SecretBox:
    def __init__(self, master_key_b64: str):
        try:
            key = base64.b64decode(master_key_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise SecretBoxError("APP_MASTER_KEY: некорректный base64") from exc
        if len(key) != 32:
            raise SecretBoxError("APP_MASTER_KEY должен быть base64 от ровно 32 байт")
        self._aes = AESGCM(key)

    def encrypt(self, plaintext: str, *, aad: bytes = b"") -> str:
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), aad or None)
        return base64.b64encode(_VERSION + nonce + ct).decode("ascii")

    def decrypt(self, token: str, *, aad: bytes = b"") -> str:
        try:
            raw = base64.b64decode(token, validate=True)
            if raw[:1] != _VERSION:
                raise SecretBoxError("неизвестная версия формата")
            return self._aes.decrypt(raw[1:13], raw[13:], aad or None).decode("utf-8")
        except SecretBoxError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SecretBoxError("не удалось расшифровать (неверный ключ или повреждённые данные)") from exc
