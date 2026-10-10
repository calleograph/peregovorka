"""Личный кабинет: свой профиль, обновление данных из Active Directory, аватарка. Карточка другого участника — в api/collab.py (только участникам той же встречи)."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.directory import DirectoryError
from ..auth.service import apply_profile
from ..models import User, utcnow
from ..services import avatars
from ..services.audit import write_audit

router = APIRouter(tags=["profile"])


def avatar_url(u: User) -> str | None:
    return f"/api/v1/users/{u.id}/avatar?v={int(u.avatar_updated_at.timestamp())}" if u.avatar_mime and u.avatar_updated_at else None


def profile_out(u: User, su: SessionUser) -> dict:
    return {"id": str(u.id), "display_name": u.display_name, "login": u.sam_account_name, "email": u.email, "title": u.title, "department": u.department,
            "phone": u.phone, "source": "local" if u.auth_source == "local" else "ad", "avatar_url": avatar_url(u),
            "synced_at": u.profile_synced_at.isoformat() if u.profile_synced_at else None, "is_admin": bool(su.is_admin)}


async def _me(db: AsyncSession, su: SessionUser) -> User:
    u = await db.get(User, su.user_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return u


@router.get("/profile")
async def get_profile(su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return profile_out(await _me(db, su), su)


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
    await request.app.state.enrichment.on_login(db, u)
    request.app.state.enrichment.spawn(u.id, force=True)
    await write_audit(db, actor_user_id=u.id, actor_name=u.display_name, action="profile.refresh", target_type="user", target_id=str(u.id), ip=client_ip(request), details={})
    await db.commit()
    return profile_out(u, su)


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
    return profile_out(u, su)


@router.delete("/profile/avatar")
async def delete_avatar(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    u = await _me(db, su)
    request.app.state.avatars.delete(u.id)
    u.avatar_mime, u.avatar_updated_at, u.avatar_source = None, None, None
    await db.commit()
    return profile_out(u, su)


@router.get("/users/{user_id}/avatar")
async def get_avatar(user_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Файл аватарки (только вошедшим сотрудникам). Адрес содержит версию (?v=), поэтому браузер кэширует его надолго."""
    u = await db.get(User, user_id)
    data = request.app.state.avatars.read(user_id) if u and u.avatar_mime else None
    if data is None:
        raise HTTPException(status_code=404, detail="Аватарки нет")
    return Response(content=data, media_type=avatars.MIME, headers={"Cache-Control": "private, max-age=604800, immutable", "X-Content-Type-Options": "nosniff"})
