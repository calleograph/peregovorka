"""Корпоративные чаты: `/api/v1/chats/*`. Только для вошедших сотрудников; членство в переписке проверяется при каждом обращении к ней,
её сообщениям и файлам (services/chats.py). Изменения разлетаются событиями в личные каналы участников (WebSocket `/ws`, тип `subscribe_user`)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.directory import DirectoryError
from ..auth.throttle import within_limit
from ..models import Chat, ChatFile, ChatMember, ChatMessage, User, utcnow
from ..services import avatars
from ..services import chats as cs
from ..services.audit import audit_now, write_audit
from ..services.chat_files import INLINE_IMAGES, AttachmentError

router = APIRouter(prefix="/chats", tags=["chats"])

SEARCH_RATE = (40, 60)
FILE_RATE = (20, 60)
TYPING_RATE = (20, 10)


def _fail(exc: cs.ChatError):
    raise HTTPException(status_code=exc.status, detail=exc.message) from None


async def _cfg(request: Request, db: AsyncSession):
    cfg = await request.app.state.settings_svc.get(db, "chats")
    if not cfg.enabled:
        raise HTTPException(status_code=403, detail="Чаты отключены администратором")
    return cfg


async def _me(db: AsyncSession, su: SessionUser) -> User:
    u = await db.get(User, su.user_id)
    if u is None or not u.is_active:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return u


async def _member(db: AsyncSession, chat_id: uuid.UUID, su: SessionUser) -> tuple[Chat, ChatMember]:
    try:
        return await cs.require_member(db, chat_id, su.user_id)
    except cs.ChatError as exc:
        _fail(exc)


async def _limit(request: Request, kind: str, su: SessionUser, lw: tuple[int, int]) -> None:
    if not await within_limit(request.app.state.redis, f"rl:chat:{kind}:{su.user_id}", lw[0], lw[1]):
        raise HTTPException(status_code=429, detail="Слишком часто. Подождите немного.", headers={"Retry-After": str(lw[1])})


async def _broadcast(request: Request, db: AsyncSession, chat_id: uuid.UUID, event: dict) -> None:
    await cs.notify(request.app.state.redis, await cs.member_ids(db, chat_id), event)


def _uuid(v, what: str = "идентификатор") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Некорректный {what}") from None


# ------------------------------------------------------------------------------------------------ сводка, список, настройки
@router.get("/config")
async def config(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Что доступно клиенту: включены ли чаты, пределы, вложения, диктовка (и жив ли ASR), присутствие, личные настройки."""
    cfg = await request.app.state.settings_svc.get(db, "chats")
    files = await request.app.state.chat_files.settings(db)
    me = await _me(db, su)
    asr = await request.app.state.bridge.heartbeat() if cfg.dictation_enabled else None
    return {"enabled": cfg.enabled, "max_message_chars": cfg.max_message_chars, "max_group_members": cfg.max_group_members, "presence": cfg.presence_enabled,
            "files": {"enabled": files.enabled, "max_size_mb": files.max_size_mb, "max_files": files.max_files_per_message, "types": sorted(files.extensions())},
            "dictation": {"enabled": bool(cfg.dictation_enabled), "available": bool(asr and asr.get("model_loaded", True)), "max_seconds": cfg.dictation_max_seconds},
            "prefs": cs.prefs(me), "me": {"id": str(me.id), "name": me.display_name}, "reactions": list(cs.REACTIONS)}


@router.put("/prefs")
async def put_prefs(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Личные настройки чатов (на всех устройствах): показывать ли мой статус «в сети», звук новых сообщений."""
    me = await _me(db, su)
    cur = cs.prefs(me)
    for k in cs.DEFAULT_PREFS:
        if k in body:
            cur[k] = bool(body[k])
    me.chat_prefs = cur
    await db.commit()
    if not cur["presence"]:
        await cs.touch_presence(request.app.state.redis, me.id, False)           # скрыть сразу, а не по истечении срока
    await cs.notify(request.app.state.redis, [me.id], {"type": "chat_prefs", "prefs": cur})
    return cur


@router.get("")
async def list_chats(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    cfg = await _cfg(request, db)
    items = await cs.chat_list(db, su.user_id)
    if cfg.presence_enabled:
        peers = [it["peer"]["id"] for it in items if it["peer"]]
        on = await _visible_online(request, db, peers)
        for it in items:
            if it["peer"]:
                it["peer"]["online"] = it["peer"]["id"] in on
    return JSONResponse({"items": items, "unread": sum(i["unread"] for i in items if not i["muted"]), "server_time": utcnow().isoformat()})


@router.get("/unread")
async def unread(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Значок на кнопке «Чаты»: один лёгкий запрос."""
    cfg = await request.app.state.settings_svc.get(db, "chats")
    return {"unread": await cs.unread_total(db, su.user_id) if cfg.enabled else 0, "enabled": cfg.enabled}


async def _visible_online(request: Request, db: AsyncSession, ids: list[str]) -> set[str]:
    """Кто в сети — только среди тех, кто не скрыл свой статус."""
    if not ids:
        return set()
    on = await cs.online(request.app.state.redis, ids)
    if not on:
        return set()
    rows = (await db.execute(select(User.id, User.chat_prefs).where(User.id.in_([uuid.UUID(i) for i in on])))).all()
    return {str(r.id) for r in rows if (r.chat_prefs or {}).get("presence", True)}


# ------------------------------------------------------------------------------------------------ люди и создание переписок
@router.get("/people")
async def people(request: Request, q: str = Query("", max_length=100), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Поиск сотрудников: уже входившие (имя, логин, почта, подразделение) и корпоративный каталог (AD; отключённые учётные записи не показываются);
    группы каталога — для группового чата. Для локальной авторизации без каталога — только внутренний справочник."""
    await _cfg(request, db)
    await _limit(request, "people", su, SEARCH_RATE)
    q = q.strip()
    if len(q) < 2:
        return {"users": [], "groups": [], "directory": None}
    like = f"%{q.lower().replace('%', '').replace('_', '')}%"
    rows = (await db.execute(select(User).where(User.is_active.is_(True), User.id != su.user_id,
                                                or_(func.lower(User.display_name).like(like), func.lower(User.sam_account_name).like(like),
                                                    func.lower(func.coalesce(User.email, "")).like(like), func.lower(func.coalesce(User.department, "")).like(like)))
                             .order_by(User.display_name).limit(20))).scalars().all()
    users = [{"id": str(u.id), "ref": u.ad_guid if u.auth_source == "ad" else None, "name": u.display_name, "login": u.sam_account_name, "department": u.department,
              "title": u.title, "avatar_url": cs.user_avatar_url(u), "source": "local"} for u in rows]
    seen = {(x["ref"] or "").lower() for x in users}
    groups: list[dict] = []
    directory_state = "ok"
    try:
        found, groups = await asyncio.gather(asyncio.to_thread(request.app.state.directory.search, "user", q, 20),
                                             asyncio.to_thread(request.app.state.directory.search, "group", q, 10))
        me = (await db.get(User, su.user_id))
        refs = [str(e.get("ref") or "").lower() for e in found if e.get("ref")]
        blocked = set((await db.execute(select(func.lower(User.ad_guid)).where(func.lower(User.ad_guid).in_(refs), User.is_active.is_(False)))).scalars()) if refs else set()
        for e in found:
            ref = str(e.get("ref") or "").lower()
            if not ref or ref in seen or ref in blocked or (me and me.ad_guid.lower() == ref):     # отключённые в Peregovorka — тоже не показываются
                continue
            seen.add(ref)
            users.append({"id": None, "ref": ref, "name": e.get("name") or e.get("sam"), "login": e.get("sam"), "department": e.get("department"), "title": None,
                          "avatar_url": None, "source": "directory"})
    except DirectoryError as exc:
        directory_state = "not_configured" if exc.code == "not_configured" else "unavailable"
    return {"users": users[:30], "groups": [{"ref": g.get("ref"), "name": g.get("name"), "description": g.get("description")} for g in groups][:10],
            "directory": directory_state}


async def _resolve_user(request: Request, db: AsyncSession, item: dict) -> uuid.UUID:
    """Сотрудник по id (уже входил) или по записи каталога: клиент присылает её ссылку (objectGUID) и логин из результатов поиска, сервер заново ищет
    в каталоге по логину и сверяет objectGUID — подставить чужую или несуществующую учётную запись нельзя. Запись сотрудника заводится, если её ещё нет."""
    if item.get("id"):
        return _uuid(item["id"], "сотрудник")
    ref, login = str(item.get("ref") or "").lower(), str(item.get("login") or "")[:256]
    u = (await db.execute(select(User).where(func.lower(User.ad_guid) == ref))).scalar_one_or_none() if ref else None
    if u is not None:
        if not u.is_active:
            raise HTTPException(status_code=404, detail="Сотрудник не найден или учётная запись отключена")
        return u.id
    try:
        found = await asyncio.to_thread(request.app.state.directory.search, "user", login, 10) if len(login) >= 2 else []
    except DirectoryError:
        raise HTTPException(status_code=503, detail="Каталог недоступен") from None
    entry = next((e for e in found if ref and str(e.get("ref") or "").lower() == ref), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден в каталоге")
    try:
        return (await cs.user_from_directory(db, entry)).id
    except cs.ChatError as exc:
        _fail(exc)


@router.post("/direct")
async def open_direct(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Написать сотруднику: существующая личная переписка открывается, новая создаётся (дубля нет)."""
    await _cfg(request, db)
    other = await _resolve_user(request, db, body)
    try:
        chat, created = await cs.open_direct(db, su.user_id, other)
    except cs.ChatError as exc:
        _fail(exc)
    if created:
        await cs.notify(request.app.state.redis, [su.user_id, other], {"type": "chat_updated", "chat_id": str(chat.id)})
    return {"id": str(chat.id), "created": created}


@router.post("/groups/members-of")
async def group_members(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Участники группы каталога (AD) — для выбора в групповой чат. Отключённые учётные записи каталог не возвращает."""
    await _cfg(request, db)
    await _limit(request, "people", su, SEARCH_RATE)
    dn = str(body.get("ref") or "")[:500]
    if not dn:
        raise HTTPException(status_code=422, detail="Не указана группа")
    try:
        rows = await asyncio.to_thread(request.app.state.directory.members, dn, 300)
    except DirectoryError:
        raise HTTPException(status_code=503, detail="Каталог недоступен") from None
    return {"users": [{"id": None, "ref": r.get("ref"), "name": r.get("name") or r.get("sam"), "login": r.get("sam"), "source": "directory"} for r in rows if r.get("ref")]}


@router.post("/group", status_code=201)
async def create_group(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    cfg = await _cfg(request, db)
    raw = body.get("members") or []
    if not isinstance(raw, list) or len(raw) > cfg.max_group_members:
        raise HTTPException(status_code=422, detail=f"Участники: список до {cfg.max_group_members}")
    ids = [await _resolve_user(request, db, x if isinstance(x, dict) else {"id": x}) for x in raw]
    me = await _me(db, su)
    try:
        chat = await cs.create_group(db, me, str(body.get("title") or ""), ids, cfg)
    except cs.ChatError as exc:
        _fail(exc)
    await write_audit(db, actor_user_id=me.id, actor_name=me.display_name, action="chat.group_create", target_type="chat", target_id=str(chat.id), ip=client_ip(request),
                      details={"members": len(ids) + 1})
    await db.commit()
    await _broadcast(request, db, chat.id, {"type": "chat_updated", "chat_id": str(chat.id)})
    return {"id": str(chat.id)}


# ------------------------------------------------------------------------------------------------ одна переписка
@router.get("/{chat_id}")
async def get_chat(chat_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    cfg = await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    info = await cs.chat_info(db, chat, me)
    on = await _visible_online(request, db, [m["id"] for m in info["members"]]) if cfg.presence_enabled else set()
    for m in info["members"]:
        m["online"] = m["id"] in on
        # «уведомить о появлении в сети» — в личной переписке (у группы в сотни человек это сотни обращений к Redis)
        m["watch"] = chat.kind == "direct" and m["id"] != str(su.user_id) and await cs.watching(request.app.state.redis, su.user_id, uuid.UUID(m["id"]))
    info["prefs"] = {"muted": me.muted, "pinned": me.pinned_at is not None, "archived": me.archived, "folder_id": str(me.folder_id) if me.folder_id else None}
    return info


@router.patch("/{chat_id}")
async def update_chat(chat_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Название группы (только администраторы группы)."""
    await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    try:
        cs.require_admin(chat, me)
    except cs.ChatError as exc:
        _fail(exc)
    title = " ".join(str(body.get("title") or "").split())[:200]
    if not title:
        raise HTTPException(status_code=422, detail="Укажите название группы")
    if title != chat.title:
        old, chat.title = chat.title, title
        user = await _me(db, su)
        await cs.system_message(db, chat, user, f"{user.display_name} меняет название группы: «{title}»")
        await write_audit(db, actor_user_id=user.id, actor_name=user.display_name, action="chat.group_rename", target_type="chat", target_id=str(chat.id), ip=client_ip(request),
                          details={"from": old, "to": title})
        await db.commit()
        await _broadcast(request, db, chat.id, {"type": "chat_updated", "chat_id": str(chat.id)})
    return {"ok": True}


@router.put("/{chat_id}/avatar")
async def put_avatar(chat_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Изображение группы (JPEG, PNG или WebP до 600 КБ): сервер приводит его к квадрату 256×256."""
    await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    try:
        cs.require_admin(chat, me)
    except cs.ChatError as exc:
        _fail(exc)
    if int(request.headers.get("content-length") or 0) > avatars.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Файл больше {avatars.MAX_UPLOAD_BYTES // 1024} КБ")
    try:
        webp = await asyncio.to_thread(avatars.process, await request.body())
    except avatars.AvatarError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    request.app.state.avatars.save(chat.id, webp)
    chat.avatar_mime, chat.avatar_updated_at = avatars.MIME, utcnow()
    await db.commit()
    await _broadcast(request, db, chat.id, {"type": "chat_updated", "chat_id": str(chat.id)})
    return {"avatar_url": cs.chat_avatar_url(chat)}


@router.delete("/{chat_id}/avatar")
async def delete_avatar(chat_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    chat, me = await _member(db, chat_id, su)
    try:
        cs.require_admin(chat, me)
    except cs.ChatError as exc:
        _fail(exc)
    request.app.state.avatars.delete(chat.id)
    chat.avatar_mime = chat.avatar_updated_at = None
    await db.commit()
    await _broadcast(request, db, chat.id, {"type": "chat_updated", "chat_id": str(chat.id)})
    return {"ok": True}


@router.get("/{chat_id}/avatar")
async def get_avatar(chat_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    chat, _me_ = await _member(db, chat_id, su)
    data = request.app.state.avatars.read(chat.id) if chat.avatar_mime else None
    if data is None:
        raise HTTPException(status_code=404, detail="Изображения нет")
    return Response(content=data, media_type=avatars.MIME, headers={"Cache-Control": "private, max-age=604800, immutable", "X-Content-Type-Options": "nosniff"})


@router.post("/{chat_id}/members")
async def add_members(chat_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    cfg = await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    try:
        cs.require_admin(chat, me)
    except cs.ChatError as exc:
        _fail(exc)
    raw = body.get("members") or []
    if not isinstance(raw, list) or len(raw) > cfg.max_group_members:
        raise HTTPException(status_code=422, detail="Участники: список")
    ids = [await _resolve_user(request, db, x if isinstance(x, dict) else {"id": x}) for x in raw]
    actor = await _me(db, su)
    try:
        added = await cs.add_members(db, chat, actor, ids, cfg)
    except cs.ChatError as exc:
        _fail(exc)
    if added:
        await write_audit(db, actor_user_id=actor.id, actor_name=actor.display_name, action="chat.members_add", target_type="chat", target_id=str(chat.id), ip=client_ip(request),
                          details={"users": [str(u.id) for u in added]})
        await db.commit()
        await _broadcast(request, db, chat.id, {"type": "chat_updated", "chat_id": str(chat.id)})
    return {"added": len(added)}


@router.delete("/{chat_id}/members/{user_id}")
async def remove_member(chat_id: uuid.UUID, user_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Исключить участника (администратор группы) или выйти самому (свой id)."""
    await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    if chat.kind != "group":
        raise HTTPException(status_code=409, detail="Из личной переписки выйти нельзя — её можно удалить из своего списка")
    if user_id != su.user_id:
        try:
            cs.require_admin(chat, me)
        except cs.ChatError as exc:
            _fail(exc)
    before = await cs.member_ids(db, chat.id)
    actor = await _me(db, su)
    gone = await cs.remove_member(db, chat, actor, user_id)
    if gone is None:
        raise HTTPException(status_code=404, detail="Участник не найден")
    await write_audit(db, actor_user_id=actor.id, actor_name=actor.display_name, action="chat.leave" if user_id == su.user_id else "chat.members_remove",
                      target_type="chat", target_id=str(chat.id), ip=client_ip(request), details={"user": str(user_id)})
    await db.commit()
    await cs.notify(request.app.state.redis, before, {"type": "chat_updated", "chat_id": str(chat.id)})
    return {"ok": True}


@router.patch("/{chat_id}/members/{user_id}")
async def set_role(chat_id: uuid.UUID, user_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user),
                   db: AsyncSession = Depends(get_db)):
    """Назначить или снять администратора группы. Последнего администратора снять нельзя."""
    await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    try:
        cs.require_admin(chat, me)
    except cs.ChatError as exc:
        _fail(exc)
    role = body.get("role")
    if role not in ("admin", "member"):
        raise HTTPException(status_code=422, detail="Роль: admin или member")
    target = await cs.membership(db, chat.id, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Участник не найден")
    if role == "member" and target.role == "admin":
        admins = (await db.execute(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == chat.id, ChatMember.role == "admin"))).scalar_one()
        if admins <= 1:
            raise HTTPException(status_code=409, detail="В группе должен остаться хотя бы один администратор")
    if target.role != role:
        target.role = role
        actor = await _me(db, su)
        await write_audit(db, actor_user_id=actor.id, actor_name=actor.display_name, action="chat.role", target_type="chat", target_id=str(chat.id), ip=client_ip(request),
                          details={"user": str(user_id), "role": role})
        await db.commit()
        await _broadcast(request, db, chat.id, {"type": "chat_updated", "chat_id": str(chat.id)})
    return {"ok": True}


@router.patch("/{chat_id}/me")
async def my_prefs(chat_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Личное: без уведомлений, закреплён, в архиве, папка. У остальных участников ничего не меняется."""
    await _cfg(request, db)
    _chat, me = await _member(db, chat_id, su)
    try:
        await cs.set_member_prefs(db, me, body)
    except cs.ChatError as exc:
        _fail(exc)
    await cs.notify(request.app.state.redis, [su.user_id], {"type": "chat_updated", "chat_id": str(chat_id)})
    return {"ok": True}


@router.post("/{chat_id}/clear")
async def clear(chat_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Очистить историю у себя: у собеседников переписка остаётся."""
    chat, me = await _member(db, chat_id, su)
    await cs.clear_history(db, chat, me)
    await cs.notify(request.app.state.redis, [su.user_id], {"type": "chat_cleared", "chat_id": str(chat_id)})
    return {"ok": True}


@router.delete("/{chat_id}")
async def hide(chat_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Удалить переписку из своего списка (и очистить её у себя). Новое сообщение вернёт переписку в список; у остальных ничего не удаляется."""
    chat, me = await _member(db, chat_id, su)
    me.hidden, me.pinned_at, me.folder_id = True, None, None
    await cs.clear_history(db, chat, me)
    await cs.notify(request.app.state.redis, [su.user_id], {"type": "chat_updated", "chat_id": str(chat_id)})
    return {"ok": True}


# ------------------------------------------------------------------------------------------------ сообщения
@router.get("/{chat_id}/messages")
async def messages(chat_id: uuid.UUID, request: Request, before_id: int | None = Query(None, ge=1), after_id: int | None = Query(None, ge=0),
                   around_id: int | None = Query(None, ge=1), limit: int = Query(cs.PAGE, ge=1, le=cs.MAX_PAGE),
                   su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Страница переписки: последние; `before_id` — раньше (прокрутка вверх); `after_id` — новее (после переподключения); `around_id` — вокруг найденного."""
    await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    out = await cs.history(db, chat, me, before_id=before_id, after_id=after_id, around_id=around_id, limit=limit)
    out["last_read_id"] = me.last_read_id
    # отметки «прочитано» остальных участников — для галочек у своих сообщений (у больших групп — не перечисляем всех)
    others = (await db.execute(select(ChatMember.user_id, ChatMember.last_read_id).where(ChatMember.chat_id == chat.id, ChatMember.user_id != su.user_id).limit(51))).all()
    out["reads"] = {str(r.user_id): r.last_read_id for r in others} if len(others) <= 50 else {}
    return JSONResponse(out)


@router.post("/{chat_id}/messages", status_code=201)
async def send(chat_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Отправить сообщение. `client_id` (обязателен в новых клиентах) — повтор после сбоя сети не создаёт дубль; `reply_to` — ответ с цитатой;
    `files` — загруженные заранее вложения; `forward` — `{chat_id, message_id}` пересылаемого сообщения из другой своей переписки."""
    cfg = await _cfg(request, db)
    chat, _m = await _member(db, chat_id, su)
    files_cfg = await request.app.state.chat_files.settings(db)
    raw_files = body.get("files") or []
    if not isinstance(raw_files, list) or not all(isinstance(i, str) for i in raw_files):
        raise HTTPException(status_code=422, detail="files: список идентификаторов вложений")
    file_ids = [_uuid(i, "идентификатор вложения") for i in raw_files]
    forwarded, text = None, str(body.get("text") or "")
    fwd = body.get("forward")
    if isinstance(fwd, dict):
        src_chat, src_me = await _member(db, _uuid(fwd.get("chat_id"), "чат"), su)
        try:
            src = await cs.visible_message(db, src_chat, src_me, int(fwd.get("message_id") or 0))
        except (cs.ChatError, ValueError):
            raise HTTPException(status_code=404, detail="Пересылаемое сообщение не найдено") from None
        if src.deleted_at is not None or src.kind != "text":
            raise HTTPException(status_code=409, detail="Это сообщение переслать нельзя")
        forwarded = {"name": src.forwarded.get("name") if src.forwarded else src.author_name, "at": (src.forwarded or {}).get("at") or src.created_at.isoformat()}
        text = src.text                                    # вложения при пересылке не копируются: у получателей нет доступа к исходной переписке
    try:
        text = cs.clean_text(text, cfg.max_message_chars, allow_empty=bool(file_ids) or forwarded is not None)
        reply_to = int(body["reply_to"]) if body.get("reply_to") is not None else None
    except cs.ChatError as exc:
        _fail(exc)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="reply_to: номер сообщения") from None
    client_id = str(body["client_id"]) if body.get("client_id") else None
    if not await within_limit(request.app.state.redis, f"rl:chat:send:{su.user_id}", cfg.messages_per_minute, 60):
        raise HTTPException(status_code=429, detail="Слишком много сообщений подряд. Подождите минуту.", headers={"Retry-After": "60"})
    author = await _me(db, su)
    try:
        res = await cs.post(db, chat, author, text=text, client_id=client_id, reply_to=reply_to, file_ids=file_ids, forwarded=forwarded,
                            max_files=files_cfg.max_files_per_message)
    except cs.ChatError as exc:
        _fail(exc)
    out = (await cs.render(db, [res.message]))[0]
    if res.created:
        await _broadcast(request, db, chat.id, {"type": "chat_message", "chat_id": str(chat.id), "message": out})
    return JSONResponse(out, status_code=201 if res.created else 200)


@router.patch("/{chat_id}/messages/{message_id}")
async def edit(chat_id: uuid.UUID, message_id: int, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Изменить своё сообщение (право проверяет сервер)."""
    cfg = await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    try:
        msg = await cs.own_message(db, chat, me, message_id)
        has_files = bool((await db.execute(select(func.count()).select_from(ChatFile).where(ChatFile.message_id == msg.id))).scalar_one())
        text = cs.clean_text(body.get("text"), cfg.max_message_chars, allow_empty=has_files)
    except cs.ChatError as exc:
        _fail(exc)
    if text != msg.text:
        msg.text, msg.edited_at = text, utcnow()
        await db.commit()
        out = (await cs.render(db, [msg]))[0]
        await _broadcast(request, db, chat.id, {"type": "chat_message_updated", "chat_id": str(chat.id), "message": out})
        return out
    return (await cs.render(db, [msg]))[0]


@router.delete("/{chat_id}/messages/{message_id}")
async def delete_message(chat_id: uuid.UUID, message_id: int, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Удалить своё сообщение у всех: текст стирается, вложения удаляются из хранилища, в ленте остаётся отметка «сообщение удалено»."""
    await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    try:
        msg = await cs.own_message(db, chat, me, message_id)
    except cs.ChatError as exc:
        _fail(exc)
    files = list((await db.execute(select(ChatFile).where(ChatFile.message_id == msg.id))).scalars())
    if files:
        await request.app.state.chat_files.remove_files(db, files)
        for f in files:
            await db.delete(f)
    msg.text, msg.deleted_at = "", utcnow()
    actor = await _me(db, su)
    await write_audit(db, actor_user_id=actor.id, actor_name=actor.display_name, action="chat.message_delete", target_type="chat", target_id=str(chat.id),
                      ip=client_ip(request), details={"message_id": msg.id, "files": len(files)})
    await db.commit()
    out = (await cs.render(db, [msg]))[0]
    await _broadcast(request, db, chat.id, {"type": "chat_message_updated", "chat_id": str(chat.id), "message": out})
    return {"ok": True}


@router.post("/{chat_id}/messages/{message_id}/reaction")
async def react(chat_id: uuid.UUID, message_id: int, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Реакция на сообщение (`emoji`; тот же значок или null — снять). Изменение видят все участники сразу."""
    await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    await _limit(request, "react", su, (60, 60))
    emoji = body.get("emoji")
    try:
        msg = await cs.visible_message(db, chat, me, message_id)
        changed = await cs.set_reaction(db, msg, su.user_id, str(emoji) if emoji else None)
    except cs.ChatError as exc:
        _fail(exc)
    out = (await cs.render(db, [msg]))[0]
    if changed:
        await _broadcast(request, db, chat.id, {"type": "chat_message_updated", "chat_id": str(chat.id), "message": out})
    return out


@router.post("/{chat_id}/read")
async def read(chat_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Прочитано до сообщения: синхронизируется на всех устройствах; собеседникам — отметка «прочитано»."""
    chat, me = await _member(db, chat_id, su)
    try:
        last = int(body.get("last_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="last_id: номер сообщения") from None
    if await cs.mark_read(db, chat, me, last):
        await _broadcast(request, db, chat.id, {"type": "chat_read", "chat_id": str(chat.id), "user_id": str(su.user_id), "last_read_id": me.last_read_id})
    return {"last_read_id": me.last_read_id}


@router.get("/{chat_id}/messages/{message_id}/receipts")
async def message_receipts(chat_id: uuid.UUID, message_id: int, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    chat, me = await _member(db, chat_id, su)
    try:
        msg = await cs.visible_message(db, chat, me, message_id)
    except cs.ChatError as exc:
        _fail(exc)
    return {"delivered": True, "items": await cs.receipts(db, chat, msg)}


@router.post("/{chat_id}/typing", status_code=204)
async def typing(chat_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    chat, _m = await _member(db, chat_id, su)
    if not await within_limit(request.app.state.redis, f"rl:chat:typing:{su.user_id}", *TYPING_RATE):
        return
    others = [u for u in await cs.member_ids(db, chat.id) if u != su.user_id]
    await cs.notify(request.app.state.redis, others, {"type": "chat_typing", "chat_id": str(chat.id), "user_id": str(su.user_id), "name": su.display_name})


# ------------------------------------------------------------------------------------------------ поиск
def _day(v: str | None, end: bool = False) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        raise HTTPException(status_code=422, detail="Дата в формате ГГГГ-ММ-ДД") from None
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    from datetime import timedelta  # noqa: PLC0415

    return d + timedelta(days=1) if end and len(v) <= 10 else d


@router.get("/{chat_id}/search")
async def search_chat(chat_id: uuid.UUID, request: Request, q: str = Query("", max_length=200), author: uuid.UUID | None = None, date_from: str | None = None,
                      date_to: str | None = None, before_id: int | None = Query(None, ge=1), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    await _cfg(request, db)
    chat, me = await _member(db, chat_id, su)
    await _limit(request, "search", su, SEARCH_RATE)
    return await cs.search_messages(db, chat, me, q, author_id=author, date_from=_day(date_from), date_to=_day(date_to, True), before_id=before_id)


@router.get("/search/all")
async def search_all(request: Request, q: str = Query("", max_length=200), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    await _cfg(request, db)
    await _limit(request, "search", su, SEARCH_RATE)
    return await cs.search_all(db, su.user_id, q)


# ------------------------------------------------------------------------------------------------ папки
@router.get("/folders/mine")
async def list_folders(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return {"items": await cs.folders(db, su.user_id)}


@router.post("/folders/mine", status_code=201)
async def create_folder(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    try:
        f = await cs.create_folder(db, su.user_id, body.get("name"))
    except cs.ChatError as exc:
        _fail(exc)
    await cs.notify(request.app.state.redis, [su.user_id], {"type": "chat_folders"})
    return {"id": str(f.id), "name": f.name}


@router.patch("/folders/mine/{folder_id}")
async def rename_folder(folder_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    try:
        await cs.rename_folder(db, await cs.own_folder(db, su.user_id, folder_id), body.get("name"))
    except cs.ChatError as exc:
        _fail(exc)
    await cs.notify(request.app.state.redis, [su.user_id], {"type": "chat_folders"})
    return {"ok": True}


@router.delete("/folders/mine/{folder_id}")
async def delete_folder(folder_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Удалить папку; переписки из неё остаются (возвращаются в общий список)."""
    try:
        await cs.delete_folder(db, await cs.own_folder(db, su.user_id, folder_id))
    except cs.ChatError as exc:
        _fail(exc)
    await cs.notify(request.app.state.redis, [su.user_id], {"type": "chat_folders"})
    return {"ok": True}


# ------------------------------------------------------------------------------------------------ присутствие
@router.post("/presence/watch/{user_id}")
async def watch(user_id: uuid.UUID, request: Request, body: dict = Body(default={}), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """«Уведомить, когда появится в сети» (добровольно, одноразово). Если человек скрыл статус, уведомления не будет — и это не раскрывается."""
    cfg = await _cfg(request, db)
    if not cfg.presence_enabled:
        raise HTTPException(status_code=403, detail="Статус «в сети» отключён администратором")
    if user_id == su.user_id or await db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    if body.get("on", True):
        await cs.watch_presence(request.app.state.redis, su.user_id, user_id)
    else:
        await cs.unwatch_presence(request.app.state.redis, su.user_id, user_id)
    return {"watch": bool(body.get("on", True))}


# ------------------------------------------------------------------------------------------------ вложения
async def _read_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"Файл больше {limit // (1024 * 1024)} МБ")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(status_code=413, detail=f"Файл больше {limit // (1024 * 1024)} МБ")
    return bytes(buf)


@router.post("/{chat_id}/files", status_code=201)
async def upload(chat_id: uuid.UUID, request: Request, name: str = Query("", max_length=300), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Загрузка файла (тело — сами байты, имя — в `name`): проверка типа по содержимому и размера; вложение ждёт отправки сообщения."""
    await _cfg(request, db)
    chat, _m = await _member(db, chat_id, su)
    svc = request.app.state.chat_files
    cfg = await svc.settings(db)
    if not cfg.enabled:
        raise HTTPException(status_code=403, detail="Вложения отключены администратором")
    await _limit(request, "file", su, FILE_RATE)
    await db.commit()                                       # не держать транзакцию, пока принимается файл
    data = await _read_limited(request, cfg.max_size_mb * 1024 * 1024)
    try:
        f = await svc.store_for_chat(db, chat.id, uploader_id=su.user_id, raw_name=name, data=data)
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    return cs.file_out(f)


async def _file(db: AsyncSession, chat: Chat, me: ChatMember, file_id: uuid.UUID) -> ChatFile:
    f = await db.get(ChatFile, file_id)
    if f is None or f.chat_id != chat.id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    if f.message_id is None:
        if f.uploader_id != me.user_id:                     # ещё не отправленное вложение видит только загрузивший
            raise HTTPException(status_code=404, detail="Файл не найден")
    elif f.message_id <= me.cleared_before_id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return f


@router.get("/{chat_id}/files/{file_id}")
async def download(chat_id: uuid.UUID, file_id: uuid.UUID, request: Request, download: bool = Query(False), su: SessionUser = Depends(require_user),
                   db: AsyncSession = Depends(get_db)):
    """Файл — только участникам переписки, через backend (публичных ссылок нет). Картинки (проверенные по содержимому) — для просмотра, остальное — скачиванием."""
    chat, me = await _member(db, chat_id, su)
    f = await _file(db, chat, me, file_id)
    try:
        data = await request.app.state.chat_files.read(db, f)
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    inline = f.kind == "image" and f.mime in INLINE_IMAGES.values() and not download
    if not inline:
        await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="chat.file_download", target_type="chat", target_id=str(chat.id),
                        ip=client_ip(request), details={"file_id": str(f.id), "size": f.size})
    disposition = ("inline" if inline else "attachment") + "; filename*=UTF-8''" + quote(f.name, safe="")
    return Response(data, media_type=f.mime if inline else "application/octet-stream", headers={
        "Content-Disposition": disposition, "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.delete("/{chat_id}/files/{file_id}", status_code=204)
async def drop_pending(chat_id: uuid.UUID, file_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Убрать ещё не отправленное вложение из поля ввода."""
    chat, me = await _member(db, chat_id, su)
    f = await _file(db, chat, me, file_id)
    if f.message_id is not None:
        raise HTTPException(status_code=409, detail="Файл уже отправлен")
    await request.app.state.chat_files.remove_files(db, [f])
    await db.delete(f)
    await db.commit()
