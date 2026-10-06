"""Админка: настройки (хранилище, обезличивание, LLM, протокол, экран, общие), пользователи,
встречи, каталог AD, состояние системы. Все эндпоинты — только для администратора; изменения — в аудит."""
from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..auth.directory import DirectoryError
from ..integrations.anonymizer import AnonymizerClient
from ..integrations.llm import LlmClient
from ..models import Meeting, Protocol, Recording, Room, TranscriptSegment, User
from ..services.audit import write_audit
from ..services.settings import GROUPS, SettingsError
from ..services.storage import StorageError, build_storage
from .meetings import meeting_out
from .schemas import MeetingOut

router = APIRouter(prefix="/admin", tags=["admin"])


# ----------------------------------------------------------------------------- настройки
@router.get("/settings")
async def all_settings(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.settings_svc
    return {g: await svc.public(db, g) for g in GROUPS}


@router.get("/settings/{group}")
async def get_settings(group: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if group not in GROUPS:
        raise HTTPException(status_code=404, detail="Неизвестная группа настроек")
    return await request.app.state.settings_svc.public(db, group)


@router.put("/settings/{group}")
async def put_settings(group: str, request: Request, body: dict[str, Any] = Body(...),
                       su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Частичное обновление группы. Секреты: поле отсутствует/null — не менять; "" — очистить; строка — задать."""
    if group not in GROUPS:
        raise HTTPException(status_code=404, detail="Неизвестная группа настроек")
    svc = request.app.state.settings_svc
    try:
        if group == "storage":  # ошибки каталога видны сразу, а не при первой выгрузке
            try:
                build_storage(await svc.preview(db, group, body), request.app.state.settings.data_dir)  # type: ignore[arg-type]
            except StorageError as exc:
                raise SettingsError(str(exc)) from None
        changed = await svc.update(db, group, body, actor=su.display_name)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    secrets = set(GROUPS[group].SECRETS)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=f"settings.{group}.update",
                      target_type="settings", target_id=group, ip=client_ip(request),
                      details={"changed": changed, "protected_changed": [c for c in changed if c in secrets]})
    await db.commit()
    return await svc.public(db, group)


@router.post("/settings/{group}/test")
async def test_settings(group: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Проверка СОХРАНЁННЫХ настроек: хранилище (пробная запись), обезличивание, LLM."""
    svc = request.app.state.settings_svc
    app_s = request.app.state.settings
    tr = getattr(request.app.state, "test_transports", {}) or {}
    try:
        cfg = await svc.get(db, group)
    except SettingsError as exc:
        return {"ok": False, "message": str(exc), "ms": 0}
    if group == "storage":
        try:
            backend = build_storage(cfg, app_s.data_dir)  # type: ignore[arg-type]
        except StorageError as exc:
            return {"ok": False, "message": str(exc), "ms": 0}
        if backend is None:
            return {"ok": False, "message": "Выгрузка выключена (включите и сохраните).", "ms": 0}
        started = asyncio.get_running_loop().time()
        try:
            msg = await asyncio.to_thread(backend.test)
            ok = True
        except StorageError as exc:
            msg, ok = str(exc), False
        return {"ok": ok, "message": msg, "ms": int((asyncio.get_running_loop().time() - started) * 1000)}
    if group == "anonymizer":
        ok, msg, ms = await AnonymizerClient(cfg, ca_file=app_s.ldap_ca_file or None, transport=tr.get("anonymizer")).test()  # type: ignore[arg-type]
        return {"ok": ok, "message": msg, "ms": ms}
    if group == "llm":
        an = await svc.get(db, "anonymizer")
        if not an.enabled:  # type: ignore[attr-defined]
            return {"ok": False, "message": "Сначала настройте и включите обезличивание: к LLM данные уходят только через него.", "ms": 0}
        ok, msg, ms = await LlmClient(cfg, ca_file=app_s.ldap_ca_file or None, transport=tr.get("llm")).test()  # type: ignore[arg-type]
        return {"ok": ok, "message": msg, "ms": ms}
    raise HTTPException(status_code=404, detail="Для этой группы проверки нет")


# --------------------------------------------------------------------------- пользователи
@router.get("/users")
async def list_users(q: str = Query("", max_length=100), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                     su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    stmt = select(User).order_by(User.display_name).limit(limit).offset(offset)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(User.display_name).like(like) | func.lower(User.sam_account_name).like(like))
    rows = (await db.execute(stmt)).scalars().all()
    return [{"id": str(u.id), "sam_account_name": u.sam_account_name, "display_name": u.display_name, "email": u.email,
             "is_active": u.is_active, "is_admin": u.last_is_admin, "last_login_at": u.last_login_at, "ad_guid": u.ad_guid}
            for u in rows]


@router.patch("/users/{user_id}")
async def patch_user(user_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...),
                     su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if "is_active" not in body or not isinstance(body["is_active"], bool):
        raise HTTPException(status_code=422, detail="Ожидается is_active: true|false")
    if user.id == su.user_id and not body["is_active"]:
        raise HTTPException(status_code=409, detail="Нельзя отключить самого себя")
    user.is_active = body["is_active"]
    redis = request.app.state.redis
    key = f"user:inactive:{user.id}"
    if user.is_active:
        await redis.delete(key)
    else:  # действующие сессии прекращаются при ближайшем запросе
        await redis.set(key, "1", ex=request.app.state.settings.session_absolute_timeout_seconds)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="user.activate" if user.is_active else "user.deactivate",
                      target_type="user", target_id=str(user.id), ip=client_ip(request), details={"sam": user.sam_account_name})
    await db.commit()
    return {"id": str(user.id), "is_active": user.is_active}


# --------------------------------------------------------------------------- каталог AD
@router.get("/directory/search")
async def directory_search(request: Request, kind: str = Query(pattern="^(group|user)$"), q: str = Query(min_length=2, max_length=100),
                           su: SessionUser = Depends(require_admin)):
    try:
        return await asyncio.to_thread(request.app.state.directory.search, kind, q, 20)
    except DirectoryError as exc:
        raise HTTPException(status_code=503, detail=f"Каталог недоступен ({exc.code})") from None


# ------------------------------------------------------------------------------- встречи
@router.get("/meetings", response_model=list[MeetingOut])
async def admin_meetings(active: bool | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                         su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    stmt = select(Meeting).order_by(Meeting.started_at.desc()).limit(limit).offset(offset)
    if active is True:
        stmt = stmt.where(Meeting.ended_at.is_(None))
    elif active is False:
        stmt = stmt.where(Meeting.ended_at.is_not(None))
    return [meeting_out(m) for m in (await db.execute(stmt)).scalars().unique()]


@router.post("/meetings/{meeting_id}/end", status_code=204)
async def admin_end_meeting(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin),
                            db: AsyncSession = Depends(get_db)):
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if await request.app.state.meetings.end(db, meeting, "admin", kick=True):
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.force_end",
                          target_type="meeting", target_id=str(meeting_id), ip=client_ip(request), details={"room": meeting.room.slug})
        await db.commit()


@router.get("/recordings")
async def list_recordings(room_id: uuid.UUID | None = None, limit: int = Query(100, ge=1, le=500),
                          su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    stmt = select(Recording, Room.name).join(Room, Room.id == Recording.room_id).order_by(Recording.created_at.desc()).limit(limit)
    if room_id:
        stmt = stmt.where(Recording.room_id == room_id)
    return [{"id": str(r.id), "meeting_id": str(r.meeting_id), "room": name, "identity": r.participant_identity, "path": r.path,
             "size_bytes": r.size_bytes, "duration_s": r.duration_s, "created_at": r.created_at}
            for r, name in (await db.execute(stmt)).all()]


# -------------------------------------------------------------------------------- система
@router.get("/system")
async def system_status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    app = request.app
    s = app.state.settings
    out: dict[str, Any] = {"version": s.app_version, "commit": s.app_git_commit, "public_url": s.app_public_url, "checks": {}}

    out["checks"]["postgres"] = {"ok": True}
    try:
        await app.state.redis.ping()
        out["checks"]["redis"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        out["checks"]["redis"] = {"ok": False, "error": type(exc).__name__}
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            out["checks"]["livekit"] = {"ok": (await c.get(s.livekit_http_url + "/")).status_code == 200}
    except Exception as exc:  # noqa: BLE001
        out["checks"]["livekit"] = {"ok": False, "error": type(exc).__name__}
    hb = await app.state.bridge.heartbeat()
    out["checks"]["asr"] = {"ok": bool(hb and hb.get("model_loaded")), **({k: hb.get(k) for k in ("queue_depth", "dropped", "errors", "active_meetings", "processed")} if hb else {})}
    try:
        await asyncio.to_thread(app.state.directory.check_service_account)
        out["checks"]["ldap"] = {"ok": True}
    except DirectoryError as exc:
        out["checks"]["ldap"] = {"ok": False, "error": exc.code}
    except Exception as exc:  # noqa: BLE001
        out["checks"]["ldap"] = {"ok": False, "error": type(exc).__name__}

    out["counts"] = {
        "users": (await db.execute(select(func.count()).select_from(User))).scalar_one(),
        "rooms": (await db.execute(select(func.count()).select_from(Room))).scalar_one(),
        "meetings": (await db.execute(select(func.count()).select_from(Meeting))).scalar_one(),
        "active_meetings": (await db.execute(select(func.count()).select_from(Meeting).where(Meeting.ended_at.is_(None)))).scalar_one(),
        "segments": (await db.execute(select(func.count()).select_from(TranscriptSegment))).scalar_one(),
        "recordings": (await db.execute(select(func.count()).select_from(Recording))).scalar_one(),
        "recordings_bytes": (await db.execute(select(func.coalesce(func.sum(Recording.size_bytes), 0)))).scalar_one(),
        "protocols": (await db.execute(select(func.count()).select_from(Protocol))).scalar_one(),
    }
    try:
        free = shutil.disk_usage(s.data_dir).free
        out["disk_free_bytes"] = free
    except OSError:
        out["disk_free_bytes"] = None
    out["master_key_ok"] = app.state.settings_svc._box is not None  # noqa: SLF001 — только флаг для UI
    return out
