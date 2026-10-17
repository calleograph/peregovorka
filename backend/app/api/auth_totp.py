"""Двухфакторная аутентификация (TOTP): настройка пользователем, резервные коды, сброс администратором.

Включение: пароль → секрет и QR → код из приложения → резервные коды (показываются один раз). Секрет хранится зашифрованным, до подтверждения кода — только в Redis.
Выключить 2FA нельзя, если политика требует её для этого пользователя. Любое действие записывается в аудит.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin, require_user
from ..auth.service import AuthError, totp_required
from ..models import User, UserBackupCode, utcnow
from ..services import totp
from ..services.audit import write_audit

router = APIRouter(tags=["auth"])
PENDING_TTL_S = 600
CODE_FAILS, CODE_FAIL_WINDOW_S = 10, 600


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class PasswordCodeIn(PasswordIn, CodeIn):
    pass


async def _cfg(request: Request, db: AsyncSession):
    return await request.app.state.settings_svc.get(db, "auth_security")


def _box(request: Request):
    box = request.app.state.settings_svc.box
    if box is None:
        raise HTTPException(status_code=503, detail="APP_MASTER_KEY не задан: секрет 2FA сохранить нельзя.")
    return box


async def _user(db: AsyncSession, su: SessionUser) -> User:
    u = await db.get(User, su.user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return u


async def _reauth(request: Request, db: AsyncSession, su: SessionUser, password: str) -> None:
    try:
        ok = await request.app.state.auth.verify_current(db, su, password, client_ip(request))
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    if not ok:
        raise HTTPException(status_code=403, detail="Неверный пароль.")


async def _code_ok(request: Request, db: AsyncSession, su: SessionUser, user: User, code: str) -> tuple[bool, bool]:
    """Проверка кода с ограничением числа неудач на пользователя (общее окно 10 мин)."""
    r = request.app.state.redis
    key = f"totp:codefail:{user.id}"
    if int(await r.get(key) or 0) >= CODE_FAILS:
        raise HTTPException(status_code=429, detail="Слишком много неверных кодов. Повторите через несколько минут.")
    ok, backup = await request.app.state.auth.check_code(db, user, code)
    if not ok:
        if await r.incr(key) == 1:
            await r.expire(key, CODE_FAIL_WINDOW_S)
        await write_audit(db, actor_user_id=user.id, actor_name=user.display_name, action="auth.totp_failed", target_type="user", target_id=str(user.id), ip=client_ip(request),
                          result="failure", details={"where": "self_service"})
        await db.commit()
    else:
        await r.delete(key)
    return ok, backup


async def _new_codes(db: AsyncSession, user: User) -> list[str]:
    await db.execute(delete(UserBackupCode).where(UserBackupCode.user_id == user.id))
    codes = totp.new_backup_codes()
    for c in codes:
        db.add(UserBackupCode(user_id=user.id, code_hash=totp.hash_backup(str(user.id), c)))
    return [totp.format_backup(c) for c in codes]


@router.get("/auth/totp/status")
async def status(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    cfg, user = await _cfg(request, db), await _user(db, su)
    left = (await db.execute(select(func.count()).select_from(UserBackupCode).where(UserBackupCode.user_id == user.id, UserBackupCode.used_at.is_(None)))).scalar_one()
    return {"available": cfg.totp_policy != "off" and request.app.state.settings_svc.box is not None, "policy": cfg.totp_policy, "enabled": user.totp_enabled,
            "required": cfg.totp_policy != "off" and totp_required(cfg, is_admin=su.is_admin, groups=su.groups), "backup_left": left if user.totp_enabled else 0,
            "enabled_at": user.totp_enabled_at}


@router.post("/auth/totp/setup")
async def setup(body: PasswordIn, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    cfg, user = await _cfg(request, db), await _user(db, su)
    if cfg.totp_policy == "off":
        raise HTTPException(status_code=403, detail="Двухфакторная аутентификация отключена администратором.")
    _box(request)
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="Двухфакторная аутентификация уже включена.")
    await _reauth(request, db, su, body.password)
    secret = totp.new_secret()
    await request.app.state.redis.set(f"totp:pending:{user.id}", secret, ex=PENDING_TTL_S)
    uri = totp.otpauth_uri(cfg.totp_issuer, user.sam_account_name, secret)
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": uri, "qr_svg": totp.qr_svg(uri), "expires_in": PENDING_TTL_S}


@router.post("/auth/totp/enable")
async def enable(body: CodeIn, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    user = await _user(db, su)
    secret = await request.app.state.redis.get(f"totp:pending:{user.id}")
    if not secret:
        raise HTTPException(status_code=409, detail="Сначала начните настройку заново: время ожидания истекло.")
    r = request.app.state.redis
    fk = f"totp:codefail:{user.id}"
    if int(await r.get(fk) or 0) >= CODE_FAILS:
        raise HTTPException(status_code=429, detail="Слишком много неверных кодов. Повторите через несколько минут.")
    step = totp.verify(secret, body.code)
    if step is None:
        if await r.incr(fk) == 1:
            await r.expire(fk, CODE_FAIL_WINDOW_S)
        raise HTTPException(status_code=422, detail="Код не подошёл. Проверьте время на телефоне и введите текущий код.")
    user.totp_secret_enc = _box(request).encrypt(secret, aad=f"user_totp.{user.id}".encode())
    user.totp_enabled, user.totp_enabled_at, user.totp_last_step = True, utcnow(), step
    codes = await _new_codes(db, user)
    await write_audit(db, actor_user_id=user.id, actor_name=user.display_name, action="auth.totp_enable", target_type="user", target_id=str(user.id), ip=client_ip(request))
    await db.commit()
    await r.delete(f"totp:pending:{user.id}", fk)
    await request.app.state.sessions.update(su.session_id, must_totp=False)
    return {"backup_codes": codes}


@router.post("/auth/totp/disable", status_code=204)
async def disable(body: PasswordCodeIn, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    cfg, user = await _cfg(request, db), await _user(db, su)
    if not user.totp_enabled:
        return
    if cfg.totp_policy != "off" and totp_required(cfg, is_admin=su.is_admin, groups=su.groups):
        raise HTTPException(status_code=403, detail="Политика безопасности требует двухфакторную аутентификацию: отключить её нельзя.")
    await _reauth(request, db, su, body.password)
    ok, _ = await _code_ok(request, db, su, user, body.code)
    if not ok:
        raise HTTPException(status_code=403, detail="Неверный код.")
    await _clear(db, user)
    await write_audit(db, actor_user_id=user.id, actor_name=user.display_name, action="auth.totp_disable", target_type="user", target_id=str(user.id), ip=client_ip(request))
    await db.commit()


@router.post("/auth/totp/backup-codes")
async def regenerate(body: PasswordCodeIn, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Новые резервные коды (старые перестают действовать): пароль и текущий код из приложения."""
    user = await _user(db, su)
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="Двухфакторная аутентификация не включена.")
    await _reauth(request, db, su, body.password)
    ok, backup = await _code_ok(request, db, su, user, body.code)
    if not ok or backup:
        raise HTTPException(status_code=403, detail="Нужен текущий код из приложения.")
    codes = await _new_codes(db, user)
    await write_audit(db, actor_user_id=user.id, actor_name=user.display_name, action="auth.totp_backup_regenerate", target_type="user", target_id=str(user.id), ip=client_ip(request))
    await db.commit()
    return {"backup_codes": codes}


async def _clear(db: AsyncSession, user: User) -> None:
    user.totp_enabled, user.totp_secret_enc, user.totp_last_step, user.totp_enabled_at = False, None, None, None
    await db.execute(delete(UserBackupCode).where(UserBackupCode.user_id == user.id))


@router.post("/admin/users/{user_id}/totp/reset", status_code=204)
async def admin_reset(user_id: uuid.UUID, body: PasswordIn, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Восстановление доступа при потере устройства: сброс 2FA пользователя администратором (с паролем администратора); пользователь настроит её заново при входе."""
    await _reauth(request, db, su, body.password)
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    await _clear(db, user)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="auth.totp_reset", target_type="user", target_id=str(user.id), ip=client_ip(request),
                      details={"user": user.sam_account_name})
    await db.commit()
    if user.auth_source == "local":
        await request.app.state.sessions.destroy_user(str(user.id))
    await request.app.state.redis.delete(f"totp:pending:{user.id}", f"totp:codefail:{user.id}")
