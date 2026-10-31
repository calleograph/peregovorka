"""Трансляции: управление владельцем комнаты (настройки, ссылки, пароль зрителя, запуск и остановка эфира) и публичные запросы страницы зрителя.

Зритель получает от backend ТОЛЬКО короткую подписанную ссылку на плейлист (`/hls/<доступ>/<срок>/<подпись>/index.m3u8`); сам поток отдаёт nginx контейнера web с проверкой подписи
(`secure_link`), поэтому пароль защищает не страницу, а видео, а backend не участвует в раздаче сегментов. Все права проверяются здесь."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
from ..services.live_destinations import PLATFORMS, DestinationError
from .guest import _throttle
from .meetings import get_meeting_for_user

router = APIRouter(tags=["live"])
public_router = APIRouter(prefix="/live", tags=["live-public"])

PASS_TTL_S = 12 * 3600                      # «пропуск» зрителя после ввода пароля: продление ссылки без повторного пароля
PW_FAILS, PW_WINDOW = 8, 600                # неверных паролей зрителя с одного адреса за окно
MIN_PASSWORD = 6


def _err(exc: LiveError | DestinationError) -> HTTPException:
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


def _pass_make(secret: str, scope: str) -> str:
    body = f"{scope}.{int(time.time()) + PASS_TTL_S}"
    return f"{_b64(body.encode())}.{_b64(_pass_sig(secret, body))}"


def _pass_ok(secret: str, scope: str, token: str | None) -> bool:
    """Пропуск привязан к эфиру и к его текущему паролю: смена пароля обесценивает все выданные пропуска."""
    if not token or len(token) > 300 or token.count(".") != 1:
        return False
    try:
        body_b, sig_b = token.split(".")
        body = _unb64(body_b).decode()
        sc, _, exp = body.rpartition(".")
        return hmac.compare_digest(_unb64(sig_b), _pass_sig(secret, body)) and sc == scope and int(exp) > time.time()
    except (ValueError, UnicodeDecodeError):
        return False


# ------------------------------------------------------------------------------------------------ владелец: настройки
class LivePatch(BaseModel):
    live_enabled: bool | None = None
    live_start: str | None = Field(default=None, pattern="^(manual|auto)$")
    live_quality: str | None = Field(default=None, pattern="^(720p|1080p)$")
    live_record: bool | None = None
    live_own_hls: bool | None = None
    live_cdn: bool | None = None
    live_replay: bool | None = None
    live_title: str | None = Field(default=None, max_length=200)
    live_starts_at: datetime | None = None
    live_password_enabled: bool | None = None
    live_download: bool | None = None
    live_link_days: int | None = Field(default=None, ge=1, le=365)
    clear_starts_at: bool = False


def _broadcast_out(b: Broadcast, rec, now: datetime) -> dict:
    """Эфир для организатора: ссылка, параметры доступа, срок, запись."""
    st = "live" if b.status in ("starting", "live") else b.status
    access = "off" if not b.replay else "revoked" if b.revoked_at else "expired" if (b.access_until and now > b.access_until) else "open"
    return {"id": str(b.id), "status": st, "title": b.title, "started_at": _utc(b.started_at), "ended_at": _utc(b.ended_at), "view_path": f"/live/{b.view_token}" if b.view_token else None,
            "replay": b.replay, "download": b.download, "link_days": b.link_days, "access_until": _utc(b.access_until), "revoked_at": _utc(b.revoked_at), "access": access,
            "password": bool(b.password_hash), "meeting_id": str(b.meeting_id), "peak_viewers": b.peak_viewers, "error": b.error,
            "recording": None if rec is None else {"id": str(rec.id), "status": rec.status, "file_state": rec.file_state, "duration_s": rec.duration_s, "size_bytes": rec.size_bytes}}


async def _broadcasts(db: AsyncSession, room: Room, limit: int = 20) -> list[dict]:
    from ..models import Recording  # noqa: PLC0415

    rows = list((await db.execute(select(Broadcast).where(Broadcast.room_id == room.id).order_by(Broadcast.started_at.desc()).limit(limit))).scalars())
    ids = [b.recording_id for b in rows if b.recording_id]
    recs = {r.id: r for r in (await db.execute(select(Recording).where(Recording.id.in_(ids)))).scalars()} if ids else {}
    now = datetime.now(timezone.utc)
    return [_broadcast_out(b, recs.get(b.recording_id), now) for b in rows]


async def _out(request: Request, db: AsyncSession, room: Room, su: SessionUser) -> dict:
    live, svc, dests = request.app.state.live, request.app.state.settings_svc, request.app.state.destinations
    cfg = await svc.get(db, "live")
    secret = hls_secret(request.app.state.settings)
    stream = None
    cdn = bool(cfg.cdn_enabled and cfg.cdn_base_url and room.live_cdn)
    if room.live_stream_access and room.live_enabled:
        stream, _ = hls_path(secret, room.live_stream_access, cfg.stream_link_days * 86400, bucket_s=86400)
        if cdn:
            stream = cfg.cdn_base_url + stream            # внешним плеерам — адрес на CDN; страница зрителя тоже берёт поток с CDN
    return {"enabled": room.live_enabled, "start": room.live_start, "quality": room.live_quality, "record": room.live_record, "own_hls": room.live_own_hls,
            "title": room.live_title or "", "starts_at": _utc(room.live_starts_at), "password_enabled": room.live_password_enabled, "has_password": bool(room.live_password_hash),
            "speaker_password_set": bool(room.password_hash), "view_path": f"/live/{room.live_token}" if room.live_token else None, "stream_path": stream,
            "state": await live.state(db, room), "global": {"enabled": bool(cfg.enabled), "direct_viewers_warn": int(cfg.direct_viewers_warn), "max_concurrent": int(cfg.max_concurrent)},
            "is_broadcast_room": room.room_type == "broadcast", "cdn": room.live_cdn, "replay": room.live_replay, "cdn_available": bool(cfg.cdn_enabled and cfg.cdn_base_url),
            "destinations": [dests.public(r) for r in await dests.rows(db, room)],
            "platforms": [{"id": k, "name": v} for k, v in PLATFORMS.items() if getattr(cfg, f"allow_{k}", True)], "max_destinations": int(cfg.max_destinations),
            "allow_private_destinations": bool(cfg.allow_private_destinations),
            "download": room.live_download, "link_days": room.live_link_days, "link_days_max": int(cfg.replay_days_max), "broadcasts": await _broadcasts(db, room)}


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
    if fields.get("live_cdn") and not (await request.app.state.settings_svc.get(db, "live")).cdn_enabled:
        raise HTTPException(status_code=409, detail={"code": "cdn_off", "message": "CDN не подключён администратором сервера (Администрирование → Трансляции)."})
    if fields.get("live_link_days") and fields["live_link_days"] > (await request.app.state.settings_svc.get(db, "live")).replay_days_max:
        raise HTTPException(status_code=422, detail={"code": "link_days_max", "message": "Срок больше предела, заданного администратором сервера (Администрирование → Трансляции)."})
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
    cur = await request.app.state.live.current(db, room.id)
    if cur is not None and cur.status in ("starting", "live"):
        cur.password_hash = room.live_password_hash          # идущий эфир — сразу с новым паролем (прошлые пропуска перестают действовать); прошедшие эфиры — по своим параметрам
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.live_password", target_type="room", target_id=str(room.id),
                      details={"set": bool(pw), "room": room.slug})            # сам пароль нигде не записывается
    await db.commit()
    await request.app.state.live.rotate_view_access(db, room)
    return await _out(request, db, room, su)


class BroadcastPatch(BaseModel):
    replay: bool | None = None
    download: bool | None = None
    link_days: int | None = Field(default=None, ge=1, le=365)        # новый срок — от фактического окончания эфира
    revoke: bool | None = None                                        # true — закрыть доступ к записи досрочно; false — открыть снова (в пределах срока)


@router.patch("/rooms/{room_id}/live/broadcasts/{bid}")
async def patch_broadcast(room_id: uuid.UUID, bid: uuid.UUID, body: BroadcastPatch, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Организатор (владелец комнаты) или администратор: показ записи этого эфира, скачивание, срок ссылки, досрочное закрытие доступа."""
    room = await _room_for_manager(request, db, room_id, su)
    b = await db.get(Broadcast, bid)
    if b is None or b.room_id != room.id:
        raise HTTPException(status_code=404, detail="Эфир не найден")
    fields = body.model_dump(exclude_unset=True)
    cfg = await request.app.state.settings_svc.get(db, "live")
    if fields.get("link_days") is not None:
        if fields["link_days"] > cfg.replay_days_max:
            raise HTTPException(status_code=422, detail={"code": "link_days_max", "message": f"Не больше {cfg.replay_days_max} дн. — предел администратора сервера."})
        b.link_days = fields["link_days"]
        if b.ended_at is not None:
            b.access_until = b.ended_at + timedelta(days=b.link_days)
    for k in ("replay", "download"):
        if fields.get(k) is not None:
            setattr(b, k, fields[k])
    if fields.get("revoke") is not None:
        b.revoked_at = datetime.now(timezone.utc) if fields["revoke"] else None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="room.live_broadcast_access", target_type="room", target_id=str(room.id),
                      details={"broadcast": str(b.id), "room": room.slug, **{k: v for k, v in fields.items()}})
    await db.commit()
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
            opts = body.get("options") if isinstance(body.get("options"), dict) else None          # параметры этого эфира, изменённые ведущим при запуске
            await live.start(db, meeting, by=su.display_name, opts=opts)
            await request.app.state.redis.delete(f"live:manual_off:{meeting_id}")
        else:
            await live.stop(db, meeting, by=su.display_name, manual=True)
    except LiveError as exc:
        raise _err(exc) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.live", target_type="meeting", target_id=str(meeting_id),
                      details={"enabled": enabled, "room": meeting.room.slug})
    await db.commit()
    return {"enabled": enabled}


@router.post("/meetings/{meeting_id}/live/extend")
async def meeting_live_extend(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not roles.can_control_meeting(meeting.room, su):
        raise HTTPException(status_code=403, detail="Эфиром управляют руководители комнаты")
    try:
        ends_at = await request.app.state.live.extend(db, meeting.room)
    except LiveError as exc:
        raise _err(exc) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.live_extend", target_type="meeting", target_id=str(meeting_id),
                      details={"room": meeting.room.slug, "ends_at": ends_at.isoformat()})
    await db.commit()
    return {"ends_at": ends_at}


# ------------------------------------------------------------------------------------------------ публичные запросы зрителя
# Ссылки зрителя двух видов. Ссылка ЭФИРА (/live/<broadcasts.view_token>) создаётся при каждом запуске: пока эфир идёт — прямой эфир, после окончания — его запись
# (если показ записи разрешён, доступ не закрыт досрочно и не истёк срок). Ссылка КОМНАТЫ (/live/<rooms.live_token>) — постоянная страница: ожидание, текущий эфир,
# после окончания — последний эфир по его же правилам. Никакая ссылка не открывает другие встречи, чаты, протоколы и сведения об участниках.
@dataclass
class _View:
    room: Room
    b: Broadcast | None
    kind: str                       # broadcast | room


async def _view(request: Request, db: AsyncSession, token: str) -> _View:
    """Любая причина отказа (нет ссылки, отозвана, комната отключена) — один и тот же 404."""
    nf = HTTPException(status_code=404, detail="Трансляция не найдена")
    if not token or len(token) > 64:
        raise nf
    room = (await db.execute(select(Room).where(Room.live_token == token))).scalars().first()
    if room is not None:
        if not room.live_enabled or not room.is_enabled or room.cancelled_at is not None:
            raise nf
        _st, b = await _status(request, db, room)
        return _View(room, b, "room")
    b = (await db.execute(select(Broadcast).where(Broadcast.view_token == token))).scalars().first()
    room = await db.get(Room, b.room_id) if b is not None else None
    if b is None or room is None or not room.is_enabled or room.cancelled_at is not None:
        raise nf
    return _View(room, b, "broadcast")


async def _status(request: Request, db: AsyncSession, room: Room) -> tuple[str, Broadcast | None]:
    live = request.app.state.live
    b = await live.current(db, room.id)
    if b is not None and b.status in ("starting", "live"):
        return ("live" if live.hls_ready(b) or not b.hls_dir else "starting"), b
    last = (await db.execute(select(Broadcast).where(Broadcast.room_id == room.id).order_by(Broadcast.started_at.desc()).limit(1))).scalars().first()
    if last is not None and last.status in ("ended", "failed", "ending") and last.ended_at is not None and (datetime.now(timezone.utc) - last.ended_at).total_seconds() < 6 * 3600:
        return "ended", last
    return "waiting", last


def _bstatus(request: Request, b: Broadcast) -> str:
    if b.status in ("starting", "live"):
        return "live" if request.app.state.live.hls_ready(b) or not b.hls_dir else "starting"
    return "ended"


def _pw_hash(v: _View) -> str | None:
    if v.b is not None:
        return v.b.password_hash
    return v.room.live_password_hash if v.room.live_password_enabled else None


def _scope(v: _View) -> str:
    h = _pw_hash(v) or ""
    return f"{'b' + v.b.id.hex if v.b is not None else 'r' + v.room.id.hex}.{hashlib.sha256(h.encode()).hexdigest()[:10]}"


PREPARE_SLOW_S = 1800               # подготовка записи дольше получаса — зрителю сообщаем, что она задерживается


async def _replay_state(db: AsyncSession, b: Broadcast | None) -> tuple[str, "Recording | None"]:
    """Состояние записи эфира для зрителя: off · live · preparing · slow · ready · expired · revoked · deleted · failed."""
    from ..models import Recording  # noqa: PLC0415

    if b is None or not b.replay:
        return "off", None
    if b.status in ("starting", "live"):
        return "live", None
    if b.revoked_at is not None:
        return "revoked", None
    now = datetime.now(timezone.utc)
    if b.access_until is not None and now > b.access_until:
        return "expired", None
    rec = await db.get(Recording, b.recording_id) if b.recording_id else (
        (await db.execute(select(Recording).where(Recording.egress_id == b.egress_id, Recording.kind == "mix_video"))).scalars().first() if b.egress_id else None)
    if rec is None or rec.file_state == "missing":
        return "deleted", None
    if rec.status == "failed":
        return "failed", None
    if rec.status != "ready" or b.status == "ending":
        slow = b.ended_at is not None and (now - b.ended_at).total_seconds() > PREPARE_SLOW_S
        return ("slow" if slow else "preparing"), None
    return "ready", rec


@public_router.get("/{token}")
async def viewer_info(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _throttle(request, "live:info", 240, 600)
    v = await _view(request, db, token)
    room, b = v.room, v.b
    if v.kind == "room":
        status, _b = await _status(request, db, room)
    else:
        status = _bstatus(request, b)
    cfg = await request.app.state.settings_svc.get(db, "live")
    rstate, _rec = await _replay_state(db, b) if status == "ended" else ("off" if b is None or not b.replay else "live", None)
    return {"kind": v.kind, "title": (b.title if b is not None and b.title else None) or room.live_title or room.name, "starts_at": _utc(room.live_starts_at) if v.kind == "room" else None,
            "status": status, "password_required": bool(_pw_hash(v)), "own_hls": room.live_own_hls, "ended_at": _utc(b.ended_at) if b is not None and status == "ended" else None,
            "quality": b.quality if b is not None else room.live_quality, "available": bool(cfg.enabled), "now": datetime.now(timezone.utc).isoformat(),
            "replay": rstate == "ready", "replay_state": rstate, "download": bool(b is not None and b.download and rstate == "ready"),
            "access_until": _utc(b.access_until) if b is not None and b.replay else None}


class AccessIn(BaseModel):
    password: str | None = Field(default=None, max_length=256, repr=False)
    pass_: str | None = Field(default=None, alias="pass", max_length=300, repr=False)
    long: bool = False                       # нативный HLS (Safari, iPhone) не умеет подменять подпись на лету: ему — долгая подпись (4 часа)
    model_config = {"populate_by_name": True}


async def _gate(request: Request, db: AsyncSession, v: _View, body: "AccessIn") -> str | None:
    """Пароль зрителя (если задан у эфира): пароль или «пропуск», выданный после пароля. Возвращает новый пропуск (только после ввода пароля) или None."""
    settings, ip = request.app.state.settings, client_ip(request)
    secret = hls_secret(settings)
    h = _pw_hash(v)
    if not h or _pass_ok(secret, _scope(v), body.pass_):
        return None
    room = v.room
    await _throttle(request, f"live:pwfail:{room.id}", PW_FAILS, PW_WINDOW, hit=False)
    if not body.password:
        raise HTTPException(status_code=403, detail={"code": "password_required", "message": "Эта трансляция защищена паролем."})
    if not verify_room_password(h, body.password):
        await _throttle(request, f"live:pwfail:{room.id}", PW_FAILS, PW_WINDOW)
        await audit_now(request.app, actor_user_id=None, actor_name="зритель", action="live.view_denied", target_type="room", target_id=str(room.id), ip=ip,
                        result="denied", actor_type="guest", details={"code": "password_invalid"})
        request.app.state.journal.emit("room", "live_view_denied", level="warn", user="viewer", room=room.name, ip=ip, message="Неверный пароль зрителя трансляции", data={"code": "password_invalid"})
        raise HTTPException(status_code=403, detail={"code": "password_invalid", "message": "Неверный пароль."})
    return _pass_make(secret, _scope(v))


@public_router.post("/{token}/access")
async def viewer_access(token: str, body: AccessIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Подписанная ссылка на плейлист идущего эфира. Пароль задан — нужен пароль (первый раз) или пропуск (продление без повторного ввода)."""
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    await _throttle(request, "live:access", 120, 600)
    v = await _view(request, db, token)
    room = v.room
    if v.kind == "broadcast" and v.b.status not in ("starting", "live"):
        raise HTTPException(status_code=409, detail={"code": "not_live", "message": "Эфир сейчас не идёт."})      # ссылка прошлого эфира не открывает новый эфир комнаты
    secret = hls_secret(request.app.state.settings)
    new_pass = await _gate(request, db, v, body)
    if not room.live_own_hls or not room.live_view_access:
        raise HTTPException(status_code=409, detail={"code": "no_hls", "message": "Эфир идёт только на внешних площадках."})
    cfg = await request.app.state.settings_svc.get(db, "live")
    ttl = max(int(cfg.view_link_ttl_s), 4 * 3600) if body.long else int(cfg.view_link_ttl_s)
    path, exp = hls_path(secret, room.live_view_access, ttl, bucket_s=1800 if body.long else 300)
    out = {"path": path, "expires_at": exp, "refresh_in": max(60, ttl // 2), "vid": uuid.uuid4().hex[:16],
           "base": cfg.cdn_base_url if (cfg.cdn_enabled and cfg.cdn_base_url and room.live_cdn) else ""}       # CDN: плеер берёт поток прямо с него, а не с этого сервера
    if new_pass:
        out["pass"] = new_pass
    return out


# ------------------------------------------------------------------------------------------------ запись эфира (если организатор разрешил)
def _replay_sig(secret: str, b: Broadcast, rec_id: uuid.UUID, exp: int, dl: int) -> str:
    """Подпись ссылки на файл: эфир, запись, срок подписи, режим (просмотр/скачивание) и пароль эфира — смена пароля обесценивает выданные ссылки."""
    pw = hashlib.sha256((b.password_hash or "").encode()).hexdigest()[:10]
    return _b64(hmac.new(secret.encode(), f"live-replay:{b.id.hex}:{rec_id.hex}:{exp}:{dl}:{pw}".encode(), hashlib.sha256).digest()[:18])


@public_router.post("/{token}/replay")
async def viewer_replay(token: str, body: AccessIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Ссылка на запись эфира: только если организатор разрешил показ, доступ не закрыт и не истёк; пароль эфира действует так же, как на просмотр."""
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    await _throttle(request, "live:replay", 60, 600)
    v = await _view(request, db, token)
    new_pass = await _gate(request, db, v, body)                 # сначала пароль: без него даже наличие записи не раскрывается
    state, rec = await _replay_state(db, v.b)
    if state != "ready":
        raise HTTPException(status_code=404, detail={"code": f"replay_{state}", "message": REPLAY_TEXT.get(state, "Запись недоступна.")})
    exp = int(time.time()) + 3600
    secret = hls_secret(request.app.state.settings)
    base = f"/api/v1/live/{token}/replay.mp4?r={rec.id.hex}&e={exp}"
    out = {"url": f"{base}&d=0&s={_replay_sig(secret, v.b, rec.id, exp, 0)}", "duration_s": rec.duration_s, "expires_at": exp,
           "download_url": f"{base}&d=1&s={_replay_sig(secret, v.b, rec.id, exp, 1)}" if v.b.download else None}
    if new_pass:
        out["pass"] = new_pass
    return out


REPLAY_TEXT = {
    "off": "Организатор не открыл запись этого эфира для зрителей.",
    "live": "Эфир ещё идёт.",
    "preparing": "Запись готовится. Обычно это занимает несколько минут после окончания эфира.",
    "slow": "Запись готовится дольше обычного. Попробуйте открыть ссылку позже.",
    "expired": "Срок просмотра записи по этой ссылке завершён.",
    "revoked": "Организатор закрыл доступ к записи.",
    "deleted": "Запись удалена и больше недоступна.",
    "failed": "Запись этого эфира не удалось сохранить.",
}


@public_router.get("/{token}/replay.mp4")
async def viewer_replay_file(token: str, request: Request, r: str, e: int, s: str, d: int = 0, db: AsyncSession = Depends(get_db)):
    """Файл записи с поддержкой Range. Каждый запрос (в том числе перемотка) заново проверяет подпись, срок ссылки, закрытие доступа и разрешение на скачивание:
    постоянного адреса файла в хранилище зритель не получает."""
    from fastapi.responses import StreamingResponse  # noqa: PLC0415
    from starlette.concurrency import iterate_in_threadpool  # noqa: PLC0415
    from urllib.parse import quote  # noqa: PLC0415

    from .media import parse_range  # noqa: PLC0415
    from ..services.storage import StorageError, StorageNotFound  # noqa: PLC0415

    v = await _view(request, db, token)
    try:
        rid = uuid.UUID(r)
    except ValueError:
        raise HTTPException(status_code=404, detail="Не найдено") from None
    dl = 1 if d else 0
    if v.b is None or e < time.time() or not hmac.compare_digest(s, _replay_sig(hls_secret(request.app.state.settings), v.b, rid, e, dl)):
        raise HTTPException(status_code=403, detail={"code": "bad_link", "message": "Ссылка на запись недействительна или устарела."})
    state, rec = await _replay_state(db, v.b)
    if state != "ready" or rec is None or rec.id != rid:
        raise HTTPException(status_code=410 if state in ("expired", "revoked", "deleted") else 404, detail={"code": f"replay_{state}", "message": REPLAY_TEXT.get(state, "Запись недоступна.")})
    if dl and not v.b.download:
        raise HTTPException(status_code=403, detail={"code": "no_download", "message": "Скачивание записи не разрешено."})
    try:
        size, reader = await request.app.state.protocols.media_source(db, rec)
    except StorageNotFound:
        raise HTTPException(status_code=410, detail={"code": "replay_deleted", "message": REPLAY_TEXT["deleted"]}) from None
    except StorageError:
        raise HTTPException(status_code=503, detail={"code": "storage_unavailable", "message": "Хранилище записей сейчас недоступно. Повторите позже."}) from None
    rng = parse_range(request.headers.get("range"), size)
    if rng == "bad":
        return StreamingResponse(iter(()), status_code=416, headers={"Content-Range": f"bytes */{size}"})
    start, end = rng if rng else (0, size - 1)
    name = ((v.b.title or v.room.live_title or v.room.name or "Запись эфира").strip()[:120]) + ".mp4"
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
               "Content-Disposition": ("attachment" if dl else "inline") + "; filename*=UTF-8''" + quote(name, safe="")}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(iterate_in_threadpool(reader(start, end)), status_code=206 if rng else 200, media_type=rec.mime or "video/mp4", headers=headers)


@public_router.post("/{token}/ping")
async def viewer_ping(token: str, request: Request, body: dict = Body(default={}), db: AsyncSession = Depends(get_db)):
    await _throttle(request, "live:ping", 60, 60)
    room = (await _view(request, db, token)).room
    vid = str(body.get("vid") or "")[:40]
    if not vid or not vid.isalnum():
        raise HTTPException(status_code=422, detail="vid")
    n = await request.app.state.live.viewer_ping(room.id, vid)
    return {"viewers": n}


# ------------------------------------------------------------------------------------------------ внешние площадки
async def _dest(request: Request, db: AsyncSession, room: Room, did: uuid.UUID):
    from ..models import LiveDestination  # noqa: PLC0415

    row = await db.get(LiveDestination, did)
    if row is None or row.room_id != room.id:
        raise HTTPException(status_code=404, detail="Площадка не найдена")           # площадка другой комнаты не раскрывается
    return row


def _audit_dest(db, su, action: str, room: Room, row, **extra):
    return write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action=action, target_type="room", target_id=str(room.id),
                       details={"room": room.slug, "platform": row.platform, "destination": row.name, **extra})       # ключ в аудит не попадает


@router.post("/rooms/{room_id}/live/destinations", status_code=201)
async def create_destination(room_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_manager(request, db, room_id, su)
    try:
        row = await request.app.state.destinations.create(db, room, body)
    except DestinationError as exc:
        raise _err(exc) from None
    await _audit_dest(db, su, "room.live_destination_added", room, row)
    await db.commit()
    return await _out(request, db, room, su)


@router.patch("/rooms/{room_id}/live/destinations/{did}")
async def update_destination(room_id: uuid.UUID, did: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_manager(request, db, room_id, su)
    row = await _dest(request, db, room, did)
    try:
        await request.app.state.destinations.update(db, row, body)
    except DestinationError as exc:
        raise _err(exc) from None
    await _audit_dest(db, su, "room.live_destination_changed", room, row, key_replaced=bool(body.get("key")), fields=sorted(k for k in body if k != "key"))
    await db.commit()
    return await _out(request, db, room, su)


@router.delete("/rooms/{room_id}/live/destinations/{did}")
async def delete_destination(room_id: uuid.UUID, did: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_manager(request, db, room_id, su)
    row = await _dest(request, db, room, did)
    live = request.app.state.live
    cur = await live.current(db, room.id)
    if cur is not None and row.status in ("live", "connecting") and row.enabled:
        try:
            await request.app.state.destinations.set_live(db, cur, row, False)          # идущий поток на эту площадку закрывается до удаления
        except DestinationError:
            pass
    await _audit_dest(db, su, "room.live_destination_removed", room, row)
    await db.delete(row)
    await db.commit()
    return await _out(request, db, room, su)


@router.post("/rooms/{room_id}/live/destinations/probe")
async def probe_new_destination(room_id: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Проверка адреса до сохранения: формат, политика (внутренние адреса), имя разрешается, сервер отвечает по сети. Ключ потока проверяет сама площадка."""
    await _room_for_manager(request, db, room_id, su)
    await _throttle(request, "live:probe", 30, 600)
    try:
        return await request.app.state.destinations.probe(db, server_url=str(body.get("server_url") or ""))
    except DestinationError as exc:
        return {"ok": False, "message": exc.message}


@router.post("/rooms/{room_id}/live/destinations/{did}/probe")
async def probe_destination(room_id: uuid.UUID, did: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await _room_for_manager(request, db, room_id, su)
    row = await _dest(request, db, room, did)
    await _throttle(request, "live:probe", 30, 600)
    try:
        return await request.app.state.destinations.probe(db, row)
    except DestinationError as exc:
        return {"ok": False, "message": exc.message}


@router.post("/meetings/{meeting_id}/live/destinations/{did}")
async def toggle_destination(meeting_id: uuid.UUID, did: uuid.UUID, request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Включить или выключить площадку в этой встрече. Идёт эфир — адрес добавляется в выход Egress или убирается из него без остановки эфира; не идёт — меняется настройка комнаты."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    if not roles.can_control_meeting(meeting.room, su):
        raise HTTPException(status_code=403, detail="Площадками управляют руководители комнаты")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="Ожидается enabled: true|false")
    row = await _dest(request, db, meeting.room, did)
    dests, live = request.app.state.destinations, request.app.state.live
    cur = await live.current(db, meeting.room_id)
    try:
        if cur is not None and cur.status in ("starting", "live"):
            await dests.set_live(db, cur, row, enabled)
        else:
            await dests.update(db, row, {"enabled": enabled})
    except DestinationError as exc:
        raise _err(exc) from None
    await _audit_dest(db, su, "meeting.live_destination", meeting.room, row, enabled=enabled)
    await db.commit()
    return await live.state(db, meeting.room, meeting)
