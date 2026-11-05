"""Хэширование паролей комнат (argon2id). Пароли пользователей AD не хранятся нигде."""
from __future__ import annotations

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_room_password(password: str) -> str:
    return _hasher.hash(password)


def verify_room_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def check_password(stored_hash: str, password: str) -> bool:
    """Argon2 занимает десятки миллисекунд процессора: в цикле событий он останавливал бы все запросы процесса, а параллельный подбор — весь сервер."""
    return await asyncio.to_thread(verify_room_password, stored_hash, password)
