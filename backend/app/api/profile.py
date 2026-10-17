"""Личный кабинет: свой профиль, обновление данных из Active Directory, аватарка. Карточка другого участника — в api/collab.py (только участникам той же встречи)."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.directory import DirectoryError
from ..auth.service import apply_profile
from ..models import User, utcnow
from ..services import avatars
from ..profiles import manual
from ..services.audit import write_audit

router = APIRouter(tags=["profile"])


def avatar_url(u: User) -> str | None:
    return f"/api/v1/users/{u.id}/avatar?v={int(u.avatar_updated_at.timestamp())}" if u.avatar_mime and u.avatar_updated_at else None


def profile_out(u: User, su: SessionUser, policy=None) -> dict:
    return {"fields": manual.field_view(u, policy), "restorable": u.auth_source != "local", "id": str(u.id), "display_name": u.display_name, "login": u.sam_account_name, "email": u.email, "title": u.title, "department": u.department,
            "phone": u.phone, "source": "local" if u.auth_source == "local" else "ad", "avatar_url": avatar_url(u),
            "synced_at": u.profile_synced_at.isoformat() if u.profile_synced_at else None, "is_admin": bool(su.is_admin)}


async def _me(db: AsyncSession, su: SessionUser) -> User:
    u = await db.get(User, su.user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return u


async def _out(request: Request, db: AsyncSession, u: User, su: SessionUser) -> dict:
    return profile_out(u, su, await request.app.state.settings_svc.get(db, "profile_policy"))


@router.get("/profile")
async def get_profile(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await _out(request, db, await _me(db, su), su)


@router.patch("/profile")
async def edit_profile(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Правка своих данных: только поля, разрешённые администратором. Значение пользователя сильнее каталога и сохраняется при синхронизации."""
    u = await _me(db, su)
    policy = await request.app.state.settings_svc.get(db, "profile_policy")
    changes = body.get("fields")
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(status_code=422, detail="Не переданы поля для изменения.")
    try:
        done = manual.set_manual(u, {str(k): str(v or "") for k, v in changes.items()}, manual.editable_set(policy))
    except manual.ManualError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    if done:
        await write_audit(db, actor_user_id=u.id, actor_name=u.display_name, action="profile.edit", target_type="user", target_id=str(u.id), ip=client_ip(request),
                          details={"fields": {f: {"old": (o or "")[:120], "new": n[:120]} for f, (o, n) in done.items()}})
        await db.commit()
        if "display_name" in done:
            await request.app.state.sessions.update(su.session_id, display_name=u.display_name)
    return await _out(request, db, u, su)


@router.post("/profile/restore")
async def restore_profile(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """«Восстановить из LDAP»: снимает правки пользователя по указанным полям (или по всем) — снова действуют данные каталога и приоритеты источников."""
    u = await _me(db, su)
    if u.auth_source == "local":
        raise HTTPException(status_code=409, detail="У локальной учётной записи нет данных в каталоге.")
    fields = body.get("fields")
    names = [str(x) for x in fields] if isinstance(fields, list) and fields else list(manual.manual_fields(u))
    done = manual.restore(u, names)
    if done:
        await write_audit(db, actor_user_id=u.id, actor_name=u.display_name, action="profile.restore", target_type="user", target_id=str(u.id), ip=client_ip(request),
                          details={"fields": done})
        await db.commit()
        if "display_name" in done:
            await request.app.state.sessions.update(su.session_id, display_name=u.display_name)
    return await _out(request, db, u, su)


@router.post("/profile/refresh")
async def refresh_profile(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """«Обновить данные из Active Directory»: перечитывает разрешённые атрибуты (ФИО, e-mail, должность, подразделение, телефон) сервисной учётной записью.
    Аватарка и пароль не затрагиваются. У локального администратора каталога нет."""
    u = await _me(db, su)
    if u.auth_source == "local":
        raise HTTPException(status_code=409, detail="У локальной учётной записи нет данных в каталоге.")
    directory = request.app.state.directory
    if not hasattr(directory, "lookup"):
        raise HTTPException(status_code=501, detail="Каталог не поддерживает обновление профиля")
    try:
        ident = await asyncio.to_thread(directory.lookup, u.upn or u.sam_account_name)
    except DirectoryError as exc:
        if exc.code in ("user_not_found", "ambiguous_user"):
            raise HTTPException(status_code=404, detail="Учётная запись не найдена в каталоге.") from None
        raise HTTPException(status_code=503, detail="Каталог сейчас недоступен. Повторите позже.") from None
    if ident.ad_guid and ident.ad_guid != u.ad_guid:
        raise HTTPException(status_code=409, detail="В каталоге найдена другая учётная запись с таким логином.")
    u.display_name, u.email = ident.display_name or u.display_name, ident.email
    apply_profile(u, ident, clear_missing=True)
    manual.apply_manual(u)                                   # правки пользователя сильнее обновления из каталога
    await request.app.state.enrichment.on_login(db, u)
    request.app.state.enrichment.spawn(u.id, force=True)
    await write_audit(db, actor_user_id=u.id, actor_name=u.display_name, action="profile.refresh", target_type="user", target_id=str(u.id), ip=client_ip(request), details={})
    await db.commit()
    return await _out(request, db, u, su)


@router.put("/profile/avatar")
async def put_avatar(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Аватарка: тело запроса — сама картинка (JPEG, PNG или WebP до 600 КБ). Сервер приводит её к квадрату 256×256 и хранит у себя."""
    declared = int(request.headers.get("content-length") or 0)
    if declared > avatars.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Файл больше {avatars.MAX_UPLOAD_BYTES // 1024} КБ: уменьшите картинку.")
    data = await request.body()
    try:
        webp = await asyncio.to_thread(avatars.process, data)
    except avatars.AvatarError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    u = await _me(db, su)
    request.app.state.avatars.save(u.id, webp)
    u.avatar_mime, u.avatar_updated_at, u.avatar_source = avatars.MIME, utcnow(), "manual"      # своё фото портал не заменяет
    await db.commit()
    return await _out(request, db, u, su)


@router.delete("/profile/avatar")
async def delete_avatar(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    u = await _me(db, su)
    request.app.state.avatars.delete(u.id)
    u.avatar_mime, u.avatar_updated_at, u.avatar_source = None, None, None
    await db.commit()
    return await _out(request, db, u, su)


@router.get("/users/{user_id}/avatar")
async def get_avatar(user_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Файл аватарки (только вошедшим сотрудникам). Адрес содержит версию (?v=), поэтому браузер кэширует его надолго."""
    u = await db.get(User, user_id)
    data = request.app.state.avatars.read(user_id) if u and u.avatar_mime else None
    if data is None:
        raise HTTPException(status_code=404, detail="Аватарки нет")
    return Response(content=data, media_type=avatars.MIME, headers={"Cache-Control": "private, max-age=604800, immutable", "X-Content-Type-Options": "nosniff"})
