"""Диагностика со стороны браузера: события (причины остановки демонстрации экрана, ошибки устройств, потеря соединения)
и метрики качества (вход в комнату, RTT, потери, битрейт, FPS экрана). Нужны, чтобы понять, что именно тормозит:
сеть, браузер, LiveKit или ASR. Секреты и содержимое разговоров не принимаются; поля ограничены по размеру и значениям."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..auth.deps import SessionUser, client_ip, require_user
from ..services.journal import parse_client
from ..services import timings

router = APIRouter(prefix="/client", tags=["client"])
log = logging.getLogger("app.client")

EVENTS = {
    "join_ok", "join_failed", "disconnected", "reconnecting", "reconnected",
    "screen_share_started", "screen_share_stopped", "screen_share_failed", "screen_share_ended_by_browser",
    "device_error", "mic_failed", "camera_failed", "publish_failed", "autoplay_blocked",
    "screen_track_published", "screen_track_unpublished", "screen_track_ended", "screen_share_restarted",
    "backend_ws_connected", "backend_ws_reconnecting", "rejoin_started", "rejoin_failed",
    "room_lifecycle", "screen_lifecycle", "screen_frozen",
    # диагностика оборудования, сети и входа (попадает в журнал событий)
    "join_attempt", "ice_failed", "ice_slow", "connect_retry", "connect_failed", "network_info", "device_inventory",
    "mic_busy", "mic_permission_denied", "mic_released", "join_without_mic", "camera_busy", "noise_suppression_changed",
    "audio_output_error", "page_hidden_long", "ice_stats", "muted_by_moderator", "livekit_connection", "board_ready", "board_history_ready", "camera_preview",
}
# категория и уровень записи в журнале; всё, чего нет в таблице, — client/info
META: dict[str, tuple[str, str]] = {
    "join_ok": ("room", "info"), "join_attempt": ("room", "info"), "join_failed": ("room", "error"),
    "disconnected": ("network", "warn"), "reconnecting": ("network", "warn"), "reconnected": ("network", "info"),
    "rejoin_started": ("network", "warn"), "rejoin_failed": ("network", "error"), "connect_retry": ("network", "warn"),
    "connect_failed": ("network", "error"), "ice_failed": ("network", "error"), "ice_slow": ("network", "warn"),
    "network_info": ("network", "info"), "livekit_connection": ("network", "info"), "ice_stats": ("network", "info"), "backend_ws_connected": ("network", "info"),
    "backend_ws_reconnecting": ("network", "warn"), "device_error": ("device", "warn"), "mic_failed": ("device", "warn"),
    "camera_failed": ("device", "warn"), "publish_failed": ("device", "error"), "mic_busy": ("device", "warn"),
    "mic_permission_denied": ("device", "warn"), "camera_busy": ("device", "warn"), "device_inventory": ("device", "info"),
    "mic_released": ("device", "info"), "join_without_mic": ("device", "info"), "noise_suppression_changed": ("device", "info"),
    "audio_output_error": ("device", "warn"), "autoplay_blocked": ("device", "warn"), "muted_by_moderator": ("room", "info"),
    "screen_share_started": ("client", "info"), "screen_share_stopped": ("client", "info"), "screen_share_failed": ("client", "warn"),
    "screen_share_ended_by_browser": ("client", "warn"), "screen_frozen": ("client", "warn"),
    "room_lifecycle": ("client", "debug"), "screen_lifecycle": ("client", "debug"), "board_ready": ("client", "info"), "camera_preview": ("device", "info"), "board_history_ready": ("client", "info"),
}
LIFECYCLE = {"room_lifecycle", "screen_lifecycle"}
COUNTERS_KEY, LIFECYCLE_KEY = "counters:realtime", "clientdiag:lifecycle"
COUNTED = {"reconnecting", "reconnected", "disconnected", "rejoin_started", "rejoin_failed", "screen_frozen", "screen_share_failed", "screen_share_ended_by_browser", "join_failed"}
EVENTS_KEY, METRICS_KEY = "clientdiag:events", "clientdiag:metrics"
EVENTS_PER_MINUTE = 240


_SECRET_RE = re.compile(r"(access_token|token|join_request|authorization|auth)=[^&\s\"'<>]*", re.I)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")


def scrub(text: str) -> str:
    """Вторая линия защиты: токены доступа и join_request из URL, JWT и Bearer в текстах диагностики не сохраняются, даже если клиент их прислал."""
    t = _SECRET_RE.sub(lambda m: m.group(0).split("=", 1)[0] + "=<скрыто>", text)
    t = _JWT_RE.sub("<jwt скрыт>", t)
    return re.sub(r"Bearer\s+\S+", "Bearer <скрыто>", t, flags=re.I)


def _num(v: Any, lo: float = 0, hi: float = 1e9) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def _data(v: Any) -> dict | None:
    """Небольшой словарь с примитивными значениями (параметры оборудования/сети): без вложенности, не более 24 ключей."""
    if not isinstance(v, dict):
        return None
    out: dict[str, Any] = {}
    for k, val in list(v.items())[:24]:
        if isinstance(val, (str, int, float, bool)) or val is None:
            out[str(k)[:40]] = scrub(val)[:200] if isinstance(val, str) else val
        elif isinstance(val, list):
            out[str(k)[:40]] = [scrub(str(x))[:80] for x in val[:12]]
    return out or None


def _str(v: Any, n: int) -> str | None:
    return scrub(str(v))[:n] if isinstance(v, (str, int, float)) and str(v) else None


async def _push(request: Request, key: str, item: dict, keep: int = 200) -> None:
    r = request.app.state.redis
    await r.lpush(key, json.dumps(item, ensure_ascii=False))
    await r.ltrim(key, 0, keep - 1)
    await r.expire(key, 86400)


@router.post("/events", status_code=204)
async def client_event(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user)):
    ev = body.get("event")
    if ev not in EVENTS:
        raise HTTPException(status_code=422, detail="Неизвестное событие")
    # Защита журнала и Redis от заливки: не более EVENTS_PER_MINUTE событий в минуту от одного пользователя (лишние отбрасываются).
    r0 = request.app.state.redis
    bucket = f"client:evrate:{su.user_id}"
    n = await r0.incr(bucket)
    if n == 1:
        await r0.expire(bucket, 60)  # окно в минуту отсчитывается от первого события (не от границы часа — так счёт предсказуем)
    if n > EVENTS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Слишком много событий диагностики", headers={"Retry-After": "30"})
    item = {"ts": time.time(), "event": ev, "user": su.sam_account_name, "meeting_id": _str(body.get("meeting_id"), 40),
            "reason": _str(body.get("reason"), 80), "detail": _str(body.get("detail"), 300)}
    log.info("client_event", extra={k: v for k, v in item.items() if k != "ts"})
    category, level = META.get(ev, ("client", "info"))
    journal = getattr(request.app.state, "journal", None)
    if journal is not None:
        journal.emit(category, ev, level=level, user=su.sam_account_name, room=_str(body.get("room"), 200), meeting_id=item["meeting_id"],
                     ip=client_ip(request), client=parse_client(request.headers.get("user-agent")),
                     message=" · ".join(x for x in (item["reason"], item["detail"]) if x) or None, data=_data(body.get("data")))
    r = request.app.state.redis
    field = None
    if ev in COUNTED:
        field = ev
    elif ev == "room_lifecycle" and item["reason"] == "ROOM_CREATE":  # сколько объектов Room создано (больше входов — значит, Room пересоздаётся)
        field = "room_create_rejoin" if "reason=rejoin" in (item["detail"] or "") else "room_create"
    if field:
        await r.hincrby(COUNTERS_KEY, field, 1)
        await r.expire(COUNTERS_KEY, 86400)
    # фазы жизненного цикла пишутся отдельно: их много, и они не должны вытеснять ошибки
    await _push(request, LIFECYCLE_KEY if ev in LIFECYCLE else EVENTS_KEY, item, keep=400 if ev in LIFECYCLE else 200)


@router.post("/metrics", status_code=204)
async def client_metrics(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user)):
    screen = body.get("screen") if isinstance(body.get("screen"), dict) else {}
    item = {
        "ts": time.time(), "user": su.sam_account_name, "meeting_id": _str(body.get("meeting_id"), 40),
        "join_ms": _num(body.get("join_ms"), 0, 600000), "signal_ms": _num(body.get("signal_ms"), 0, 600000),
        "rtt_ms": _num(body.get("rtt_ms"), 0, 60000), "packet_loss_pct": _num(body.get("packet_loss_pct"), 0, 100), "jitter_ms": _num(body.get("jitter_ms"), 0, 60000),
        "bitrate_out_kbps": _num(body.get("bitrate_out_kbps"), 0, 1e6), "bitrate_in_kbps": _num(body.get("bitrate_in_kbps"), 0, 1e6),
        "candidate": _str(body.get("candidate"), 20), "quality": _str(body.get("quality"), 20),
        "join_api_ms": _num(body.get("join_api_ms"), 0, 600000), "signaling_connect_ms": _num(body.get("signaling_connect_ms"), 0, 600000),
        "ice_connect_ms": _num(body.get("ice_connect_ms"), 0, 600000), "participant_active_ms": _num(body.get("participant_active_ms"), 0, 600000),
        "microphone_publish_ms": _num(body.get("microphone_publish_ms"), 0, 600000), "room_create_ms": _num(body.get("room_create_ms"), 0, 600000),
        "livekit_connect_ms": _num(body.get("livekit_connect_ms"), 0, 600000), "get_user_media_ms": _num(body.get("get_user_media_ms"), 0, 600000),
        "backend_ws_connect_ms": _num(body.get("backend_ws_connect_ms"), 0, 600000), "total_join_ms": _num(body.get("total_join_ms"), 0, 600000),
        "screen": {"fps": _num(screen.get("fps"), 0, 240), "bitrate_kbps": _num(screen.get("bitrate_kbps"), 0, 1e6),
                   "width": _num(screen.get("width"), 0, 20000), "height": _num(screen.get("height"), 0, 20000),
                   "limit_reason": _str(screen.get("limit_reason"), 30), "frames_dropped": _num(screen.get("frames_dropped"), 0, 1e9)},
    }
    await _push(request, METRICS_KEY, item)
    for name in ("join_api_ms", "signaling_connect_ms", "ice_connect_ms", "participant_active_ms", "microphone_publish_ms", "room_create_ms",
                 "livekit_connect_ms", "get_user_media_ms", "backend_ws_connect_ms", "total_join_ms"):
        await timings.record(request.app.state.redis, name, item.get(name))
