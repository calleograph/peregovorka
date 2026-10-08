"""Локальный (аварийный) администратор: создаётся при установке, не зависит от LDAP, пароль — только в виде хэша argon2id.

Первичный пароль генерируется случайно и показывается один раз в терминале установки; заранее заданного пароля нет. Сброс — официальным
сценарием `scripts/admin-reset.sh` (см. app/cli.py): он пишет событие в аудит и завершает все сессии этого администратора.
"""
from __future__ import annotations

import re
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog, User, utcnow
from ..security.passwords import hash_room_password

DEFAULT_USERNAME = "admin"
_USERNAME = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")
# без похожих символов (0/O, 1/l/I): пароль переписывают с экрана
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MIN_PASSWORD = 12


class LocalAdminError(Exception):
    """Сообщение безопасно для показа."""


def generate_password(groups: int = 4, size: int = 6) -> str:
    """Например: `Kd7mQx-Wp3hRt-Ns9VbZ-Gc4yJf` — 24 случайных символа (~137 бит), группы через дефис."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(size)) for _ in range(groups))


def validate_username(name: str) -> str:
    name = (name or "").strip().lower()
    if not _USERNAME.match(name):
        raise LocalAdminError("Имя локального администратора: 3–32 символа, строчные латинские буквы, цифры, «.», «_», «-», начинается с буквы.")
    return name


def validate_new_password(password: str, username: str, old: str | None = None) -> None:
    if len(password or "") < MIN_PASSWORD or len(password) > 256:
        raise LocalAdminError(f"Пароль должен быть не короче {MIN_PASSWORD} символов.")
    if username.lower() in password.lower():
        raise LocalAdminError("Пароль не должен содержать имя пользователя.")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-zа-я]", r"[A-ZА-Я]", r"\d", r"[^\w\s]|_"))
    if classes < 2:
        raise LocalAdminError("Используйте не менее двух видов символов: строчные и заглавные буквы, цифры, знаки.")
    if old is not None and password == old:
        raise LocalAdminError("Новый пароль должен отличаться от прежнего.")


async def find_local(db: AsyncSession, username: str | None = None) -> User | None:
    stmt = select(User).where(User.auth_source == "local")
    if username:
        stmt = stmt.where(func.lower(User.sam_account_name) == username.lower())
    return (await db.execute(stmt.order_by(User.created_at))).scalars().first()


async def create_local_admin(db: AsyncSession, username: str, password: str, *, must_change: bool = True) -> User:
    username = validate_username(username)
    if await find_local(db, username) is not None:
        raise LocalAdminError("Локальный администратор с таким именем уже существует.")
    uid = uuid.uuid4()
    user = User(id=uid, ad_guid=f"local-{uid}", sam_account_name=username, display_name="Локальный администратор", is_active=True, last_is_admin=True,
                auth_source="local", password_hash=hash_room_password(password), must_change_password=must_change)
    db.add(user)
    await db.flush()
    return user


async def set_password(db: AsyncSession, user: User, password: str, *, must_change: bool) -> None:
    user.password_hash = hash_room_password(password)
    user.must_change_password = must_change
    user.password_changed_at = utcnow()
    user.is_active = True
    await db.flush()


def audit(db: AsyncSession, actor: str, action: str, target: str, details: dict | None = None) -> None:
    db.add(AuditLog(actor_user_id=None, actor_name=actor, action=action, target_type="user", target_id=target, details=details or {}))
