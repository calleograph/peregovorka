"""Голосовая диктовка сообщений чата: `/api/v1/ws/dictation` (WebSocket, вход по cookie сессии, проверка Origin).

Браузер шлёт двоичные кадры PCM (int16, 16 кГц, моно) и текстовые команды `{"type":"stop"}` (закончить и дождаться окончательного текста) или
`{"type":"cancel"}`; в ответ — `ready`, фрагменты `final` (с номером — повтор клиент отбрасывает), `busy`, `done`, `error`. Распознаёт тот же ASR,
что и встречи (контракт: docs/ASR_CONTRACT.md, «Диктовка»), через Redis. Звук не сохраняется: потоки Redis удаляются по завершении.
Транзакций базы во время диктовки нет: настройки читаются один раз в начале.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth.deps import SessionUser
from ..auth.throttle import within_limit
from ..config import origin_allowed

router = APIRouter()
log = logging.getLogger("app.dictation")

CONTROL = "asr:dictation:control"
ACTIVE = "dictation:active"                 # ZSET: sid → срок, до которого диктовка считается идущей (для предела одновременных)
KEY_TTL = 600
MAX_FRAME = 64 * 1024
BYTES_PER_SECOND = 32000
DONE_WAIT_S = 35.0
PER_HOUR = 120                              # диктовок на пользователя в час


def in_key(sid: str) -> str:
    return f"asr:dict:{sid}:in"


def out_key(sid: str) -> str:
    return f"asr:dict:{sid}:out"


async def _send(ws: WebSocket, **msg) -> None:
    with contextlib.suppress(Exception):
        await ws.send_text(json.dumps(msg, ensure_ascii=False))


@router.websocket("/ws/dictation")
async def dictation(ws: WebSocket):
    app = ws.app
    origin = ws.headers.get("origin")
    if origin is not None and not origin_allowed(origin, app.state.settings):
        await ws.close(code=4403)
        return
    sid_cookie = ws.cookies.get(app.state.settings.cookie_name)
    data = await app.state.sessions.get(sid_cookie)
    if data is None or sid_cookie is None or await app.state.redis.exists(f"user:inactive:{data.user_id}"):
        await ws.close(code=4401)
        return
    su = SessionUser.from_session(sid_cookie, data)
    async with app.state.session_maker() as db:
        cfg = await app.state.settings_svc.get(db, "chats")
    await ws.accept()
    redis = app.state.redis
    if not (cfg.enabled and cfg.dictation_enabled):
        await _send(ws, type="error", code="disabled", message="Диктовка отключена администратором")
        await ws.close(code=4403)
        return
    hb = await app.state.bridge.heartbeat()
    if not hb or not hb.get("model_loaded", True):
        await _send(ws, type="error", code="asr_unavailable", message="Распознавание речи сейчас недоступно. Введите текст вручную.")
        await ws.close(code=1011)
        return
    if not await within_limit(redis, f"rl:dictation:{su.user_id}", PER_HOUR, 3600):
        await _send(ws, type="error", code="rate", message="Слишком много диктовок подряд. Попробуйте позже.")
        await ws.close(code=4429)
        return
    sid = uuid.uuid4().hex
    now = time.time()
    await redis.zremrangebyscore(ACTIVE, 0, now)
    if await redis.zcard(ACTIVE) >= cfg.dictation_max_concurrent:
        await _send(ws, type="error", code="busy", message="Сейчас диктуют слишком многие — распознавание занято. Попробуйте через минуту.")
        await ws.close(code=4429)
        return
    await redis.zadd(ACTIVE, {sid: now + cfg.dictation_max_seconds + DONE_WAIT_S})
    await redis.xadd(CONTROL, {"type": "start", "sid": sid, "max_seconds": str(cfg.dictation_max_seconds)}, maxlen=1000, approximate=True)
    await _send(ws, type="ready", max_seconds=cfg.dictation_max_seconds)
    state = {"stopping": False, "cancel": False, "bytes": 0, "stop_at": None}
    limit = cfg.dictation_max_seconds * BYTES_PER_SECOND

    async def eof() -> None:
        if not state["stopping"]:
            state["stopping"], state["stop_at"] = True, time.monotonic()
            await redis.xadd(in_key(sid), {"eof": "1"})

    async def pump_in() -> None:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                state["cancel"] = True
                await eof()
                return
            if msg.get("bytes") is not None:
                chunk = msg["bytes"]
                if state["stopping"] or not chunk or len(chunk) > MAX_FRAME or len(chunk) % 2:
                    continue
                state["bytes"] += len(chunk)
                await redis.xadd(in_key(sid), {"pcm": base64.b64encode(chunk).decode()}, maxlen=5000, approximate=True)
                await redis.expire(in_key(sid), KEY_TTL)
                if state["bytes"] >= limit:
                    await _send(ws, type="limit", max_seconds=cfg.dictation_max_seconds)
                    await eof()
            elif msg.get("text"):
                try:
                    kind = json.loads(msg["text"]).get("type")
                except (ValueError, AttributeError):
                    continue
                if kind == "cancel":
                    state["cancel"] = True
                    await eof()
                    return
                if kind == "stop":
                    await eof()

    async def pump_out() -> None:
        last = "0-0"
        while True:
            if state["stop_at"] is not None and time.monotonic() - state["stop_at"] > DONE_WAIT_S:
                await _send(ws, type="error", code="timeout", message="Распознавание не ответило вовремя. Уже распознанный текст сохранён в поле ввода.")
                return
            res = await redis.xread({out_key(sid): last}, block=1000, count=20)
            for _s, items in res or []:
                for mid, f in items:
                    last = mid
                    kind = f.get("type")
                    if kind == "final":
                        await _send(ws, type="final", seq=int(f.get("seq") or 0), text=f.get("text") or "")
                    elif kind == "busy":
                        await _send(ws, type="busy", seq=int(f.get("seq") or 0), message="Распознавание занято встречами — часть фразы не распознана")
                    elif kind in ("done", "error"):
                        await _send(ws, **({"type": "done", "timeout": f.get("timeout") == "1"} if kind == "done" else
                                           {"type": "error", "code": f.get("code") or "failed", "message": f.get("message") or "Сбой распознавания"}))
                        return

    reader = asyncio.create_task(pump_in())
    writer = asyncio.create_task(pump_out())
    try:
        done, _pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        if reader in done and not state["cancel"]:
            await writer                                       # клиент закончил отправку — дождаться окончательного текста
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("Сбой диктовки")
    finally:
        for t in (reader, writer):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if not state["stopping"]:
            with contextlib.suppress(Exception):
                await redis.xadd(in_key(sid), {"eof": "1"})
        with contextlib.suppress(Exception):
            await redis.zrem(ACTIVE, sid)
            await redis.expire(in_key(sid), 60)
            await redis.delete(out_key(sid))
        with contextlib.suppress(Exception):
            await ws.close()
