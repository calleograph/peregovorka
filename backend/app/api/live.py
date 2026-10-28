"""Трансляции: управление владельцем комнаты (настройки, ссылки, пароль зрителя, запуск и остановка эфира) и публичные запросы страницы зрителя.

Зритель получает от backend ТОЛЬКО короткую подписанную ссылку на плейлист (`/hls/<доступ>/<срок>/<подпись>/index.m3u8`); сам поток отдаёт nginx контейнера web с проверкой подписи
(`secure_link`), поэтому пароль защищает не страницу, а видео, а backend не участвует в раздаче сегментов. Все права проверяются здесь."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, _origin_ok, client_ip, get_db, require_user
from ..models import Broadcast, Room
from ..security.passwords import hash_room_password, verify_room_password
from ..services import roles
from ..services.audit import audit_now, write_audit
from ..services.live import LiveError, hls_path, hls_secret, new_token
from .guest import _throttle
from .meetings import get_meeting_for_user

router = APIRouter(tags=["live"])
public_router = APIRouter(prefix="/live", tags=["live-public"])

PASS_TTL_S = 12 * 3600                      # «пропуск» зрителя после ввода пароля: продление ссылки без повторного пароля
PW_FAILS, PW_WINDOW = 8, 600                # неверных паролей зрителя с одного адреса за окно
MIN_PASSWORD = 6


def _err(exc: LiveError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


async def _room_for_manager(request: Request, db: AsyncSession, room_id: uuid.UUID, su: SessionUser) -> Room:
    room = await db.get(Room, room_id)
    if room is None or not roles.can_manage_room(room, su):
        raise HTTPException(status_code=404, detail="Комната не найдена или нет прав")        # чужая комната не раскрывается
    return room


def _utc(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


# ------------------------------------------------------------------------------------------------ пропуск зрителя (после пароля)
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _pass_sig(secret: str, body: str) -> bytes:
    return hmac.new(secret.encode(), b"live-pass:" + body.encode(), hashlib.sha256).digest()[:18]


def _pass_make(secret: str, room: Room) -> str:
    body = f"{room.id.hex}.{room.live_view_access}.{int(time.time()) + PASS_TTL_S}"
    return f"{_b64(body.encode())}.{_b64(_pass_sig(secret, body))}"


def _pass_ok(secret: str, room: Room, token: str | None) -> bool:
    """Пропуск привязан к текущему идентификатору доступа: смена пароля или отзыв ссылки обесценивает все выданные пропуска."""
    if not token or len(token) > 200 or not room.live_view_access or token.count(".") != 1:
        return False
    try:
        body_b, sig_b = token.split(".")
        body = _unb64(body_b).decode()
        rid, acc, exp = body.split(".")
        sig = _unb64(sig_b)
        return hmac.compare_digest(sig, _pass_sig(secret, body)) and rid == room.id.hex and acc == room.live_view_access and int(exp) > time.time()
    except (ValueError, UnicodeDecodeError):
        return False


# ------------------------------------------------------------------------------------------------ владелец: настройки
class LivePatch(BaseModel):
    live_enabled: bool | None = None
    live_start: str | None = Field(default=None, pattern="^(manual|auto)$")
    live_quality: str | None = Field(default=None, pattern="^(720p|1080p)$")
    live_record: bool | None = None
    live_own_hls: bool | None = None
    live_title: str | None = Field(default=None, max_length=200)
    live_starts_at: datetime | None = None
    live_password_enabled: bool | None = None
    clear_starts_at: bool = False


async def _out(request: Request, db: AsyncSession, room: Room, su: SessionUser) -> dict:
    live, svc = request.app.state.live, request.app.state.settings_svc
    cfg = await svc.get(db, "live")
    secret = hls_secret(request.app.state.settings)
    stream = None
    if room.live_stream_access and room.live_enabled:
        stream, _ = hls_path(secret, room.live_stream_access, cfg.stream_link_days * 86400, bucket_s=86400)
    return {"enabled": room.live_enabled, "start": room.live_start, "quality": room.live_quality, "record": room.live_record, "own_hls": room.live_own_hls,
            "title": room.live_title or "", "starts_at": _utc(room.live_starts_at), "password_enabled": room.live_password_enabled, "has_password": bool(room.live_password_hash),
            "speaker_password_set": bool(room.password_hash), "view_path": f"/live/{room.live_token}" if room.live_token else None, "stream_path": stream,
            "state": await live.state(db, room), "global": {"enabled": bool(cfg.enabled), "direct_viewers_warn": int(cfg.direct_viewers_warn), "max_concurrent": int(cfg.max_concurrent)},
            "is_broadcast_room": room.room_type == "broadcast"}


@router.get("/rooms/{room_id}/live")
async def get_live(room_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_manager(request, db, room_id, su)
    if room.live_enabled and await request.app.state.live.ensure_links(db, room):
        await db.refresh(room)
    return await _out(request, db, room, su)


@router.patch("/rooms/{room_id}/live")
async def patch_live(room_id: uuid.UUID, body: LivePatch, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_manager(request, db, room_id, su)
    live = request.app.state.live
    fields = body.model_dump(exclude_unset=True, exclude={"clear_starts_at"})
    if body.clear_starts_at:
        fields["live_starts_at"] = None
    state = await live.state(db, room)
    if state["live"] and ({"live_quality", "live_record", "live_own_hls"} & fields.keys()):
        raise HTTPException(status_code=409, detail={"code": "live_running", "message": "Качество, запись и собственный просмотр меняются до начала эфира: остановите эфир."})
    if fields.get("live_password_enabled") and not room.live_password_hash:
        raise HTTPException(status_code=422, detail={"code": "no_password", "message": "Сначала задайте пароль зрителя."})
    if fields.get("live_enabled") and room.e2ee_mode != "off":
        raise HTTPException(status_code=409, detail={"code": "e2ee", "message": "При сквозном шифровании трансляция недоступна."})
    if fields.get("live_enabled") and room.encrypt_storage:
        raise HTTPException(status_code=409, detail={"code": "encrypt_storage", "message": "В комнате включено шифрование хранилища: трансляция с ним пока не поддерживается."})
    changed = {}
    for k, v in fields.items():
        if getattr(room, k) != v:
            changed[k] = v
            setattr(room, k, v)
    if "live_title" in changed:
        room.live_title = (room.live_title or "").strip() or None
    if room.live_enabled:
        await live.ensure_links(db, room)
    if changed:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.live_settings", target_type="room", target_id=str(room.id),
                          details={"changed": sorted(changed), "room": room.slug})
    await db.commit()
    if "live_password_enabled" in changed:
        await live.rotate_view_access(db, room)          # включили или выключили пароль: прежние подписи и пропуска больше не действуют
    return await _out(request, db, room, su)


@router.put("/rooms/{room_id}/live/password")
async def set_viewer_password(room_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Пароль зрителя: задать/сменить (строка) или убрать (null). Не совпадает с паролем выступающего. Смена мгновенно обесценивает выданные зрителям подписи и пропуска."""
    room = await _room_for_manager(request, db, room_id, su)
    pw = body.get("password")
    if pw is not None and (not isinstance(pw, str) or len(pw) < MIN_PASSWORD or len(pw) > 128):
        raise HTTPException(status_code=422, detail={"code": "weak_password", "message": f"Пароль зрителя — от {MIN_PASSWORD} до 128 знаков."})
    if pw and room.password_hash and verify_room_password(room.password_hash, pw):
        raise HTTPException(status_code=422, detail={"code": "same_as_speaker", "message": "Пароль зрителя не должен совпадать с паролем выступающего."})
    room.live_password_hash = hash_room_password(pw) if pw else None
    room.live_password_enabled = bool(pw)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.live_password", target_type="room", target_id=str(room.id),
                      details={"set": bool(pw), "room": room.slug})            # сам пароль нигде не записывается
    await db.commit()
    await request.app.state.live.rotate_view_access(db, room)
    return await _out(request, db, room, su)


@router.post("/rooms/{room_id}/live/links/{which}/revoke")
async def revoke_links(room_id: uuid.UUID, which: str, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Отозвать ссылку зрителя (и выпустить новую), ссылку на поток или обе. Прежние адреса перестают открываться сразу: удаляется ссылка доступа к каталогу эфира."""
    if which not in ("view", "stream", "all"):
        raise HTTPException(status_code=404, detail="Неизвестная ссылка")
    room = await _room_for_manager(request, db, room_id, su)
    await request.app.state.live.revoke(db, room, which)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.live_link_revoked", target_type="room", target_id=str(room.id), details={"which": which, "room": room.slug})
    await db.commit()
    return await _out(request, db, room, su)


# ------------------------------------------------------------------------------------------------ участники встречи: состояние и запуск
@router.get("/meetings/{meeting_id}/live")
async def meeting_live_state(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Состояние эфира — видно всем участникам (индикатор «Прямой эфир»). Ссылки и площадки получают только те, кто может ими управлять."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    st = await request.app.state.live.state(db, meeting.room, meeting)
    can = bool(st["enabled"] and roles.can_control_meeting(meeting.room, su))
    st["can_control"] = can
    if can or roles.can_manage_room(meeting.room, su):
        st["links"] = await _out(request, db, meeting.room, su)
    else:
        st.pop("destinations", None)
    return st


@router.post("/meetings/{meeting_id}/live")
async def meeting_live_toggle(meeting_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not roles.can_control_meeting(meeting.room, su):
        raise HTTPException(status_code=403, detail="Эфиром управляют руководители комнаты")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="Ожидается enabled: true|false")
    live = request.app.state.live
    try:
        if enabled:
            await live.start(db, meeting, by=su.display_name)
            await request.app.state.redis.delete(f"live:manual_off:{meeting_id}")
        else:
            await live.stop(db, meeting, by=su.display_name, manual=True)
    except LiveError as exc:
        raise _err(exc) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.live", target_type="meeting", target_id=str(meeting_id),
                      details={"enabled": enabled, "room": meeting.room.slug})
    await db.commit()
    return {"enabled": enabled}


# ------------------------------------------------------------------------------------------------ публичные запросы зрителя
async def _room_by_token(db: AsyncSession, token: str) -> Room:
    """Любая причина отказа (нет ссылки, отозвана, трансляция выключена, комната отключена) — один и тот же 404."""
    if not token or len(token) > 64:
        raise HTTPException(status_code=404, detail="Трансляция не найдена")
    room = (await db.execute(select(Room).where(Room.live_token == token))).scalars().first()
    if room is None or not room.live_enabled or not room.is_enabled or room.cancelled_at is not None:
        raise HTTPException(status_code=404, detail="Трансляция не найдена")
    return room


async def _status(request: Request, db: AsyncSession, room: Room) -> tuple[str, Broadcast | None]:
    live = request.app.state.live
    b = await live.current(db, room.id)
    if b is not None and b.status in ("starting", "live"):
        return ("live" if live.hls_ready(b) or not b.hls_dir else "starting"), b
    last = (await db.execute(select(Broadcast).where(Broadcast.room_id == room.id).order_by(Broadcast.started_at.desc()).limit(1))).scalars().first()
    if last is not None and last.status in ("ended", "failed", "ending") and last.ended_at is not None and (datetime.now(timezone.utc) - last.ended_at).total_seconds() < 6 * 3600:
        return "ended", last
    return "waiting", last


@public_router.get("/{token}")
async def viewer_info(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _throttle(request, "live:info", 240, 600)
    room = await _room_by_token(db, token)
    status, b = await _status(request, db, room)
    cfg = await request.app.state.settings_svc.get(db, "live")
    return {"title": room.live_title or room.name, "starts_at": _utc(room.live_starts_at), "status": status, "password_required": bool(room.live_password_enabled and room.live_password_hash),
            "own_hls": room.live_own_hls, "ended_at": _utc(b.ended_at) if b is not None and status == "ended" else None, "quality": room.live_quality,
            "available": bool(cfg.enabled), "now": datetime.now(timezone.utc).isoformat()}


class AccessIn(BaseModel):
    password: str | None = Field(default=None, max_length=256, repr=False)
    pass_: str | None = Field(default=None, alias="pass", max_length=200, repr=False)
    long: bool = False                       # нативный HLS (Safari, iPhone) не умеет подменять подпись на лету: ему — долгая подпись (4 часа)
    model_config = {"populate_by_name": True}


@public_router.post("/{token}/access")
async def viewer_access(token: str, body: AccessIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Подписанная ссылка на плейлист. Пароль включён — нужен пароль (первый раз) или пропуск, выданный после пароля (продление без повторного ввода)."""
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    await _throttle(request, "live:access", 120, 600)
    room = await _room_by_token(db, token)
    settings, ip = request.app.state.settings, client_ip(request)
    secret = hls_secret(settings)
    new_pass = None
    if room.live_password_enabled and room.live_password_hash:
        if not _pass_ok(secret, room, body.pass_):
            await _throttle(request, f"live:pwfail:{room.id}", PW_FAILS, PW_WINDOW, hit=False)
            if not body.password:
                raise HTTPException(status_code=403, detail={"code": "password_required", "message": "Эта трансляция защищена паролем."})
            if not verify_room_password(room.live_password_hash, body.password):
                await _throttle(request, f"live:pwfail:{room.id}", PW_FAILS, PW_WINDOW)
                await audit_now(request.app, actor_user_id=None, actor_name="зритель", action="live.view_denied", target_type="room", target_id=str(room.id), ip=ip,
                                result="denied", actor_type="guest", details={"code": "password_invalid"})
                request.app.state.journal.emit("room", "live_view_denied", level="warn", user="viewer", room=room.name, ip=ip, message="Неверный пароль зрителя трансляции", data={"code": "password_invalid"})
                raise HTTPException(status_code=403, detail={"code": "password_invalid", "message": "Неверный пароль."})
            new_pass = _pass_make(secret, room)
    if not room.live_own_hls or not room.live_view_access:
        raise HTTPException(status_code=409, detail={"code": "no_hls", "message": "Эфир идёт только на внешних площадках."})
    cfg = await request.app.state.settings_svc.get(db, "live")
    ttl = max(int(cfg.view_link_ttl_s), 4 * 3600) if body.long else int(cfg.view_link_ttl_s)
    path, exp = hls_path(secret, room.live_view_access, ttl, bucket_s=1800 if body.long else 300)
    out = {"path": path, "expires_at": exp, "refresh_in": max(60, ttl // 2), "vid": uuid.uuid4().hex[:16]}
    if new_pass:
        out["pass"] = new_pass
    return out


@public_router.post("/{token}/ping")
async def viewer_ping(token: str, request: Request, body: dict = Body(default={}), db: AsyncSession = Depends(get_db)):
    await _throttle(request, "live:ping", 60, 60)
    room = await _room_by_token(db, token)
    vid = str(body.get("vid") or "")[:40]
    if not vid or not vid.isalnum():
        raise HTTPException(status_code=422, detail="vid")
    n = await request.app.state.live.viewer_ping(room.id, vid)
    return {"viewers": n}
