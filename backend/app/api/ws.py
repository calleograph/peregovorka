"""WebSocket живых событий. Аутентификация по cookie сессии (сотрудник) либо по гостевому токену (`/ws?guest=1`, первое сообщение
`{"type":"auth","guest_token":...}` — заголовки в браузерном WebSocket не задать), проверка Origin, подписка на встречу только при
наличии права (участник встречи или админ; гость — только на свою идущую встречу).

Чаты: `{"type":"subscribe_user"}` — личный канал сотрудника (только свой; события всех его переписок на все устройства);
`{"type":"presence","active":true}` — клиент подтверждает активность (вкладка видна и было действие) не чаще раза в 30 с: только это делает
человека «в сети». Действующая, но неактивная сессия статуса не даёт; скрывший статус в настройках не отмечается."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..auth.deps import SessionUser
from ..config import origin_allowed
from ..models import MEETING_LITE, Meeting, MeetingParticipant
from ..models import User
from ..services import chats, events, roles
from ..services.access import can_access_meeting

router = APIRouter()
log = logging.getLogger("app.ws")

SESSION_RECHECK_SECONDS = 60
MAX_SUBSCRIPTIONS = 5


GUEST_AUTH_TIMEOUT = 10


async def _can_subscribe(app, su: SessionUser | None, guest, meeting_id: uuid.UUID) -> tuple[bool, bool]:
    """(можно ли подписаться, руководитель ли подписчик). Руководителям отдаются события, помеченные `leaders_only` (закрытая доска)."""
    async with app.state.session_maker() as db:
        meeting = await db.get(Meeting, meeting_id, options=MEETING_LITE)   # во встрече бывают сотни участников, а подписывается каждый
        if meeting is None:
            return False, False
        if guest is not None:
            return meeting.ended_at is None and str(meeting.id) == guest.meeting_id, False
        ok = await can_access_meeting(db, app.state.redis, meeting, su)
        return ok, bool(ok and roles.is_leader_of(meeting.room, su))


async def _presence_allowed(app, user_id: uuid.UUID) -> bool:
    async with app.state.session_maker() as db:
        cfg = await app.state.settings_svc.get(db, "chats")
        u = await db.get(User, user_id)
        return bool(cfg.enabled and cfg.presence_enabled and u is not None and chats.prefs(u)["presence"])


async def _user(app, user_id: uuid.UUID) -> User:
    async with app.state.session_maker() as db:
        return await db.get(User, user_id)


async def _guest_alive(app, token: str):
    g = await app.state.guest_sessions.get(token)
    if g is None or await app.state.redis.exists(f"guest:revoked:{g.guest_id}"):
        return None
    return g


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    app = ws.app
    settings = app.state.settings
    origin = ws.headers.get("origin")
    if origin is not None and not origin_allowed(origin, settings):
        log.warning("Origin отклонён", extra={"origin": origin[:200], "path": "/ws"})
        await ws.close(code=4403)
        return
    su: SessionUser | None = None
    guest = None
    guest_token = ""
    sid = None
    if ws.query_params.get("guest") == "1":
        await ws.accept()
        try:  # гость подтверждает себя первым сообщением
            first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=GUEST_AUTH_TIMEOUT))
            guest_token = str(first.get("guest_token") or "") if first.get("type") == "auth" else ""
        except (asyncio.TimeoutError, ValueError, AttributeError, WebSocketDisconnect):
            guest_token = ""
        guest = await _guest_alive(app, guest_token) if guest_token else None
        if guest is None:
            await ws.close(code=4401)
            return
        await ws.send_text(json.dumps({"type": "authed"}))
    else:
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

    hub = app.state.event_hub
    sub = hub.new_subscriber()
    subscribed: dict[str, uuid.UUID] = {}
    presence_ok: tuple[bool, float] | None = None          # (можно ли показывать «в сети», когда проверено)

    async def forward() -> None:
        while True:
            text = await sub.queue.get()
            if text is None:        # получатель не успевал за потоком событий — отключаем, клиент переподключится и догрузит состояние
                await ws.close(code=events.SLOW_CLOSE_CODE)
                return
            await ws.send_text(text)

    reader = asyncio.create_task(forward())
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=SESSION_RECHECK_SECONDS)
            except asyncio.TimeoutError:
                alive = (await _guest_alive(app, guest_token)) is not None if guest is not None else \
                    await app.state.sessions.get(sid, touch=False) is not None
                if not alive:
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
                allowed, is_leader = (False, False) if len(subscribed) >= MAX_SUBSCRIPTIONS else await _can_subscribe(app, su, guest, mid)
                if not allowed:
                    await ws.send_text(json.dumps({"type": "error", "message": "forbidden", "meeting_id": str(mid)}))
                    continue
                ch = events.channel(mid)
                await hub.subscribe(sub, ch, leader=is_leader)
                subscribed[ch] = mid
                await ws.send_text(json.dumps({"type": "subscribed", "meeting_id": str(mid)}))
            elif kind == "subscribe_user":
                if su is None:
                    await ws.send_text(json.dumps({"type": "error", "message": "forbidden"}))
                    continue
                await hub.subscribe(sub, events.user_channel(su.user_id))
                await ws.send_text(json.dumps({"type": "subscribed_user"}))
            elif kind == "presence":
                if su is None or not msg.get("active"):
                    continue
                now = asyncio.get_running_loop().time()
                if presence_ok is None or now - presence_ok[1] > SESSION_RECHECK_SECONDS:
                    presence_ok = (await _presence_allowed(app, su.user_id), now)
                if await chats.touch_presence(app.state.redis, su.user_id, presence_ok[0]) and presence_ok[0]:
                    await chats.came_online(app.state.redis, await _user(app, su.user_id))
            elif kind == "unsubscribe":
                try:
                    ch = events.channel(uuid.UUID(str(msg.get("meeting_id"))))
                except ValueError:
                    continue
                if ch in subscribed:
                    await hub.unsubscribe(sub, ch)
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
            await hub.release(sub)
