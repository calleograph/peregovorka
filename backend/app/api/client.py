"""Диагностика со стороны браузера: события (причины остановки демонстрации экрана, ошибки устройств, потеря соединения)
и метрики качества (вход в комнату, RTT, потери, битрейт, FPS экрана). Нужны, чтобы понять, что именно тормозит:
сеть, браузер, LiveKit или ASR. Секреты и содержимое разговоров не принимаются; поля ограничены по размеру и значениям."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ..auth.deps import SessionUser, require_user
from ..services import timings

router = APIRouter(prefix="/client", tags=["client"])
log = logging.getLogger("app.client")

EVENTS = {
    "join_ok", "join_failed", "disconnected", "reconnecting", "reconnected",
    "screen_share_started", "screen_share_stopped", "screen_share_failed", "screen_share_ended_by_browser",
    "device_error", "mic_failed", "camera_failed", "publish_failed", "autoplay_blocked",
    "screen_track_published", "screen_track_unpublished", "screen_track_ended", "screen_share_restarted",
    "backend_ws_connected", "backend_ws_reconnecting", "rejoin_started", "rejoin_failed",
}
EVENTS_KEY, METRICS_KEY = "clientdiag:events", "clientdiag:metrics"


def _num(v: Any, lo: float = 0, hi: float = 1e9) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def _str(v: Any, n: int) -> str | None:
    return str(v)[:n] if isinstance(v, (str, int, float)) and str(v) else None


async def _push(request: Request, key: str, item: dict) -> None:
    r = request.app.state.redis
    await r.lpush(key, json.dumps(item, ensure_ascii=False))
    await r.ltrim(key, 0, 199)
    await r.expire(key, 86400)


@router.post("/events", status_code=204)
async def client_event(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user)):
    ev = body.get("event")
    if ev not in EVENTS:
        raise HTTPException(status_code=422, detail="Неизвестное событие")
    item = {"ts": time.time(), "event": ev, "user": su.sam_account_name, "meeting_id": _str(body.get("meeting_id"), 40),
            "reason": _str(body.get("reason"), 80), "detail": _str(body.get("detail"), 300)}
    log.info("client_event", extra={k: v for k, v in item.items() if k != "ts"})
    await _push(request, EVENTS_KEY, item)


@router.post("/metrics", status_code=204)
async def client_metrics(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user)):
    screen = body.get("screen") if isinstance(body.get("screen"), dict) else {}
    item = {
        "ts": time.time(), "user": su.sam_account_name, "meeting_id": _str(body.get("meeting_id"), 40),
        "join_ms": _num(body.get("join_ms"), 0, 600000), "signal_ms": _num(body.get("signal_ms"), 0, 600000),
        "rtt_ms": _num(body.get("rtt_ms"), 0, 60000), "packet_loss_pct": _num(body.get("packet_loss_pct"), 0, 100),
        "bitrate_out_kbps": _num(body.get("bitrate_out_kbps"), 0, 1e6), "bitrate_in_kbps": _num(body.get("bitrate_in_kbps"), 0, 1e6),
        "candidate": _str(body.get("candidate"), 20), "quality": _str(body.get("quality"), 20),
        "join_api_ms": _num(body.get("join_api_ms"), 0, 600000), "signaling_connect_ms": _num(body.get("signaling_connect_ms"), 0, 600000),
        "ice_connect_ms": _num(body.get("ice_connect_ms"), 0, 600000), "participant_active_ms": _num(body.get("participant_active_ms"), 0, 600000),
        "microphone_publish_ms": _num(body.get("microphone_publish_ms"), 0, 600000),
        "screen": {"fps": _num(screen.get("fps"), 0, 240), "bitrate_kbps": _num(screen.get("bitrate_kbps"), 0, 1e6),
                   "width": _num(screen.get("width"), 0, 20000), "height": _num(screen.get("height"), 0, 20000),
                   "limit_reason": _str(screen.get("limit_reason"), 30), "frames_dropped": _num(screen.get("frames_dropped"), 0, 1e9)},
    }
    await _push(request, METRICS_KEY, item)
    for name in ("join_api_ms", "signaling_connect_ms", "ice_connect_ms", "participant_active_ms", "microphone_publish_ms"):
        await timings.record(request.app.state.redis, name, item.get(name))
