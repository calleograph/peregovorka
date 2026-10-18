"""Временные равноправные комнаты: создание по ссылке или кнопкой, управление ссылкой, сроком и составом (см. services/peer_rooms.py)."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import Room, User, utcnow
from ..services import peer_rooms as pr
from ..services.audit import write_audit
from ..services.temp_rooms import TempRoomError, close as close_room

router = APIRouter(prefix="/rooms/peer", tags=["rooms"])
LOOKUP_LIMIT, LOOKUP_WINDOW_S = 30, 60                 # проверок кодов в минуту на пользователя: перебор адресов невозможен и по частоте


def _out(room: Room, su: SessionUser, created: bool = False) -> dict:
    return {"id": str(room.id), "slug": room.slug, "name": room.name, "created": created, "lifecycle": room.lifecycle, "access": room.peer_access,
            "owner": pr.can_own(room, su), "link": f"/p/{room.slug.removeprefix(pr.PREFIX)}", "expires_at": room.auto_close_at,
            "invited": [{"name": e.display_name, "ref": e.subject_ref} for e in room.acl] if pr.can_own(room, su) else []}


async def _cfg(request: Request, db: AsyncSession):
    return await request.app.state.settings_svc.get(db, "general")


async def _owned(db: AsyncSession, su: SessionUser, room_id: uuid.UUID) -> Room:
    room = await db.get(Room, room_id)
    if room is None or not pr.is_peer(room) or not pr.can_own(room, su):
        raise HTTPException(status_code=404, detail="Комната не найдена или недоступна")      # чужую комнату от несуществующей не отличить
    return room


async def _raise(exc: TempRoomError):
    raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None


@router.get("/policy")
async def policy(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await pr.policy(db, await _cfg(request, db), su)


@router.post("", status_code=201)
async def create(request: Request, body: dict = Body(default={}), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Создать комнату со случайным адресом (кнопка «Равноправная комната»)."""
    s = request.app.state
    try:
        room = await pr.create(db, s.redis, su, await _cfg(request, db), str(body.get("name") or ""), default_text_days=s.settings.default_text_retention_days,
                               default_audio_days=s.settings.default_audio_retention_days)
    except TempRoomError as exc:
        await _raise(exc)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.create_peer", target_type="room", target_id=str(room.id), ip=client_ip(request),
                      details={"slug": room.slug})
    await db.commit()
    return _out(room, su, True)


@router.post("/open")
async def open_by_link(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Переход по ссылке `/p/<код>`: комната есть — открыть; нет — создать (если разрешено политикой). Неверный формат кода и чужая закрытая комната — 404."""
    s = request.app.state
    code = str(body.get("code") or "").strip().lower()
    if not pr.CODE_RE.match(code):
        raise HTTPException(status_code=404, detail="Комната не найдена или недоступна")
    key = f"peer:lookup:{su.user_id}"
    if await s.redis.incr(key) == 1:
        await s.redis.expire(key, LOOKUP_WINDOW_S)
    if int(await s.redis.get(key) or 0) > LOOKUP_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много обращений. Подождите минуту.", headers={"Retry-After": str(LOOKUP_WINDOW_S)})
    room = (await db.execute(select(Room).where(Room.slug == pr.slug_of(code)))).scalar_one_or_none()
    if room is not None:
        if not pr.is_peer(room) or room.lifecycle == "closed" or not pr.link_access(room, su):
            raise HTTPException(status_code=404, detail="Комната не найдена или недоступна")
        return _out(room, su, False)
    if await s.redis.exists(f"peer:dead:{code}"):          # перевыпущенная ссылка: по прежнему адресу новую комнату не создаём
        raise HTTPException(status_code=404, detail="Комната не найдена или недоступна")
    cfg = await _cfg(request, db)
    try:
        room = await pr.create(db, s.redis, su, cfg, "", code=code, default_text_days=s.settings.default_text_retention_days, default_audio_days=s.settings.default_audio_retention_days)
    except TempRoomError as exc:
        if exc.code in ("disabled", "link_disabled", "forbidden"):
            raise HTTPException(status_code=404, detail="Комната не найдена или недоступна") from None      # создание запрещено: наличие функции не раскрываем
        if exc.code == "exists":
            # два одновременных перехода по одной ссылке (двойной щелчок, повторная загрузка страницы): комнату уже создал параллельный запрос — открываем её, а не отвечаем ошибкой
            room = (await db.execute(select(Room).where(Room.slug == pr.slug_of(code)))).scalar_one_or_none()
            if room is not None and pr.is_peer(room) and room.lifecycle != "closed" and pr.link_access(room, su):
                return _out(room, su, False)
            raise HTTPException(status_code=404, detail="Комната не найдена или недоступна") from None
        await _raise(exc)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.create_peer", target_type="room", target_id=str(room.id), ip=client_ip(request),
                      details={"slug": room.slug, "via": "link"})
    await db.commit()
    return _out(room, su, True)


@router.get("/users/search")
async def search_users(q: str = "", su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Подбор приглашённых: сотрудники, уже входившие в систему (имя, логин, почта); не более 15 строк, поиск от 2 знаков."""
    q = q.strip().lower()
    if len(q) < 2:
        return []
    like = f"%{q.replace('%', '').replace('_', '')}%"
    rows = (await db.execute(select(User).where(User.is_active.is_(True), User.auth_source == "ad",
                                                or_(func.lower(User.display_name).like(like), func.lower(User.sam_account_name).like(like), func.lower(func.coalesce(User.email, "")).like(like)))
                             .order_by(User.display_name).limit(15))).scalars().all()
    return [{"id": str(u.id), "name": u.display_name, "login": u.sam_account_name, "department": u.department} for u in rows if u.id != su.user_id]


@router.get("/{room_id}")
async def get_owned(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return _out(await _owned(db, su, room_id), su)


@router.patch("/{room_id}")
async def update(room_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Технические права владельца: доступ (`access`: link | invited), приглашённые (`invited`: список id пользователей), продление срока (`extend_hours`), название."""
    room = await _owned(db, su, room_id)
    if room.lifecycle == "closed":
        raise HTTPException(status_code=409, detail="Комната уже закрыта.")
    cfg = await _cfg(request, db)
    changed: dict = {}
    if "access" in body:
        if body["access"] not in ("link", "invited"):
            raise HTTPException(status_code=422, detail="Доступ: link или invited")
        room.peer_access = body["access"]
        changed["access"] = body["access"]
    if "invited" in body:
        try:
            ids = [uuid.UUID(str(x)) for x in (body["invited"] or [])][:200]
        except ValueError:
            raise HTTPException(status_code=422, detail="Некорректный список приглашённых") from None
        changed["invited"] = await pr.set_invited(db, room, ids)
    if "invited_add" in body or "invited_remove" in body:
        try:
            add = [uuid.UUID(str(x)) for x in (body.get("invited_add") or [])][:200]
        except ValueError:
            raise HTTPException(status_code=422, detail="Некорректный список приглашённых") from None
        if add:
            changed["invited_add"] = await pr.add_invited(db, room, add)
        if body.get("invited_remove"):
            changed["invited_remove"] = pr.remove_invited(room, [str(x) for x in body["invited_remove"]][:200])
    if "extend_hours" in body:
        try:
            h = int(body["extend_hours"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Срок — число часов") from None
        if not 1 <= h <= cfg.peer_room_max_extend_hours:
            raise HTTPException(status_code=422, detail=f"Продлить можно на 1–{cfg.peer_room_max_extend_hours} часов.")
        room.auto_close_at = max(room.auto_close_at or utcnow(), utcnow()) + timedelta(hours=h)
        changed["extend_hours"] = h
    if "name" in body and str(body["name"]).strip():
        room.name = str(body["name"]).strip()[:200]
        changed["name"] = True
    if changed:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.update_peer", target_type="room", target_id=str(room.id), ip=client_ip(request),
                          details={"changed": list(changed)})
        await db.commit()
        await db.refresh(room)
    return _out(room, su)


@router.post("/{room_id}/rotate")
async def rotate(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Перевыпустить ссылку: прежняя перестаёт работать, идущая встреча остаётся в комнате у тех, кто уже вошёл."""
    room = await _owned(db, su, room_id)
    if room.lifecycle == "closed":
        raise HTTPException(status_code=409, detail="Комната уже закрыта.")
    for _ in range(5):
        code = pr.new_code()
        if not await pr.slug_taken(db, pr.slug_of(code)):
            await request.app.state.redis.set(f"peer:dead:{room.slug.removeprefix(pr.PREFIX)}", "1", ex=30 * 86400)
            room.slug = pr.slug_of(code)
            break
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.rotate_peer", target_type="room", target_id=str(room.id), ip=client_ip(request))
    await db.commit()
    await db.refresh(room)
    return _out(room, su)


@router.post("/{room_id}/close", status_code=204)
async def close(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Закрыть комнату (владелец или администратор — пресечение злоупотребления). История, записи и документы сохраняются по политике хранения."""
    room = await _owned(db, su, room_id)
    if close_room(room, "owner" if room.created_by_user_id == su.user_id else "admin"):
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.close_peer", target_type="room", target_id=str(room.id), ip=client_ip(request))
        await db.commit()
