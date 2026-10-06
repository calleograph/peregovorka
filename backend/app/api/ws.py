"""WebSocket живых событий. Аутентификация по cookie сессии, проверка Origin,
подписка на встречу только при наличии права (участник встречи или админ)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..auth.deps import SessionUser
from ..models import Meeting, MeetingParticipant
from ..services import events
from ..services.access import can_access_meeting

router = APIRouter()
log = logging.getLogger("app.ws")

SESSION_RECHECK_SECONDS = 60
MAX_SUBSCRIPTIONS = 5


async def _can_subscribe(app, su: SessionUser, meeting_id: uuid.UUID) -> bool:
    async with app.state.session_maker() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            return False
        return await can_access_meeting(db, app.state.redis, meeting, su)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    app = ws.app
    settings = app.state.settings
    origin = ws.headers.get("origin")
    if origin is not None and origin.rstrip("/") != settings.public_origin:
        await ws.close(code=4403)
        return
    sid = ws.cookies.get(settings.cookie_name)
    data = await app.state.sessions.get(sid)
    if data is None or sid is None:
        await ws.close(code=4401)
        return
    if await app.state.redis.exists(f"user:inactive:{data.user_id}"):
        await ws.close(code=4401)
        return
    su = SessionUser.from_session(sid, data)
    await ws.accept()

    pubsub = app.state.redis.pubsub()
    subscribed: dict[str, uuid.UUID] = {}

    async def forward() -> None:
        while True:
            if not pubsub.subscribed:
                await asyncio.sleep(0.2)
                continue
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg.get("type") == "message":
                raw = msg["data"]
                await ws.send_text(raw.decode() if isinstance(raw, bytes) else raw)

    reader = asyncio.create_task(forward())
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=SESSION_RECHECK_SECONDS)
            except asyncio.TimeoutError:
                if await app.state.sessions.get(sid, touch=False) is None:
                    await ws.close(code=4401)
                    return
                continue
            try:
                msg = json.loads(raw)
                kind = msg.get("type")
            except (ValueError, AttributeError):
                await ws.send_text(json.dumps({"type": "error", "message": "bad_message"}))
                continue
            if kind == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            elif kind == "subscribe":
                try:
                    mid = uuid.UUID(str(msg.get("meeting_id")))
                except ValueError:
                    await ws.send_text(json.dumps({"type": "error", "message": "bad_meeting_id"}))
                    continue
                if len(subscribed) >= MAX_SUBSCRIPTIONS or not await _can_subscribe(app, su, mid):
                    await ws.send_text(json.dumps({"type": "error", "message": "forbidden", "meeting_id": str(mid)}))
                    continue
                ch = events.channel(mid)
                if ch not in subscribed:
                    await pubsub.subscribe(ch)
                    subscribed[ch] = mid
                await ws.send_text(json.dumps({"type": "subscribed", "meeting_id": str(mid)}))
            elif kind == "unsubscribe":
                try:
                    ch = events.channel(uuid.UUID(str(msg.get("meeting_id"))))
                except ValueError:
                    continue
                if ch in subscribed:
                    await pubsub.unsubscribe(ch)
                    subscribed.pop(ch, None)
            else:
                await ws.send_text(json.dumps({"type": "error", "message": "unknown_type"}))
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        with contextlib.suppress(Exception):
            await pubsub.aclose()
