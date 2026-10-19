"""Значок комнаты: выбор из встроенной библиотеки, загрузка своего изображения, сброс на стандартный.

Менять значок могут администратор и руководитель комнаты (`roles.can_manage_room`); обычному участнику ответ неотличим от «нет такой комнаты». Картинку видят все, кто видит комнату.
Адрес картинки содержит версию (хэш): браузер кэширует её надолго и подхватывает замену сам. Каждое изменение записывается в аудит (и попадает в журнал комнаты): кто, когда, что выбрал.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import Room
from ..services import room_icons, roles
from ..services.audit import write_audit
from ..services.rooms import acl_allows

router = APIRouter(prefix="/rooms/{room_id}/icon", tags=["room-icons"])


async def _manageable(db: AsyncSession, room_id: uuid.UUID, su: SessionUser) -> Room:
    room = await db.get(Room, room_id)
    if room is None or not roles.can_manage_room(room, su):
        raise HTTPException(status_code=404, detail="Комната не найдена или нет прав на изменение")
    return room


async def _audit(request: Request, db: AsyncSession, su: SessionUser, room: Room, **details) -> None:
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.icon", target_type="room", target_id=str(room.id), ip=client_ip(request),
                      details={"room": room.slug, **details})


@router.get("")
async def get_icon(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await db.get(Room, room_id)
    if room is None or room.icon_kind != "image" or not (su.is_admin or acl_allows(room, su) or roles.can_manage_room(room, su)):
        raise HTTPException(status_code=404, detail="Значка нет")
    data = await asyncio.to_thread(request.app.state.room_icons.read, room.id)
    if data is None:
        raise HTTPException(status_code=404, detail="Значка нет")
    etag = f'"{room.icon_rev}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(content=data, media_type=room_icons.MIME, headers={"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


@router.put("")
async def upload_icon(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Своё изображение: тело запроса — сама картинка (PNG, JPG или WEBP). Сервер приводит её к квадрату 256×256 со скруглёнными углами; исходный файл не сохраняется."""
    room = await _manageable(db, room_id, su)
    declared = int(request.headers.get("content-length") or 0)
    if declared > room_icons.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Файл больше {room_icons.MAX_UPLOAD_BYTES // (1024 * 1024)} МБ: уменьшите картинку.")
    data = await request.body()
    try:
        webp = await asyncio.to_thread(room_icons.process, data)
    except room_icons.RoomIconError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    request.app.state.room_icons.save(room.id, webp)
    room.icon_kind, room.icon_key, room.icon_rev = "image", None, room_icons.revision(webp)
    await _audit(request, db, su, room, mode="image", size_bytes=len(webp), rev=room.icon_rev)
    await db.commit()
    return {"icon": room_icons.icon_out(room)}


@router.put("/library")
async def choose_library_icon(room_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _manageable(db, room_id, su)
    key = str(body.get("key") or "")
    if key not in room_icons.library_keys():
        raise HTTPException(status_code=422, detail="Такого значка нет в библиотеке.")
    request.app.state.room_icons.delete(room.id)                      # загруженная картинка больше не нужна
    room.icon_kind, room.icon_key, room.icon_rev = "library", key, None
    await _audit(request, db, su, room, mode="library", key=key)
    await db.commit()
    return {"icon": room_icons.icon_out(room)}


@router.delete("", status_code=200)
async def reset_icon(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _manageable(db, room_id, su)
    request.app.state.room_icons.delete(room.id)
    was = room.icon_kind
    room.icon_kind = room.icon_key = room.icon_rev = None
    if was:
        await _audit(request, db, su, room, mode="reset")
    await db.commit()
    return {"icon": None}
