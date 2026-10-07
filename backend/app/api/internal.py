"""Внутренние эндпоинты. Наружу не публикуются (web/host nginx отдают на /internal/ 404)."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from livekit import api as lkapi
from sqlalchemy import delete, select

from ..auth.deps import require_internal
from ..models import Meeting, MeetingParticipant, Room, TranscriptSegment, User
from ..services import diagnostics
from ..services.asr_bridge import SEGMENTS_STREAM
from ..services.livekit import (
    issue_user_token,
    meeting_room_name,
    parse_guest_identity,
    parse_meeting_room_name,
    parse_user_identity,
    user_identity,
    webhook_receiver,
)

router = APIRouter(prefix="/internal/v1", tags=["internal"], include_in_schema=False)
log = logging.getLogger("app.internal")


@router.post("/livekit/webhook")
async def livekit_webhook(request: Request):
    """Webhook LiveKit. Подлинность — подпись JWT (sha256 тела); без неё — 401."""
    body = (await request.body()).decode("utf-8")
    auth = request.headers.get("authorization", "")
    try:
        event = webhook_receiver(request.app.state.settings).receive(body, auth)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Недействительная подпись webhook") from None

    meeting_id = parse_meeting_room_name(event.room.name) if event.room and event.room.name else None
    user_id = parse_user_identity(event.participant.identity) if event.participant and event.participant.identity else None
    guest_id = parse_guest_identity(event.participant.identity) if event.participant and event.participant.identity else None
    if event.event in ("participant_joined", "participant_left") and meeting_id and (user_id or guest_id):
        svc = request.app.state.meetings
        async with request.app.state.session_maker() as db:
            if guest_id:
                if event.event == "participant_joined":
                    await svc.on_guest_joined(db, meeting_id, guest_id)
                else:
                    await svc.on_guest_left(db, meeting_id, guest_id)
            elif event.event == "participant_joined":
                await svc.on_participant_joined(db, meeting_id, user_id)
            else:
                await svc.on_participant_left(db, meeting_id, user_id)
    elif event.event == "room_finished" and meeting_id:
        # Комната LiveKit закрылась (все ушли дольше departure_timeout либо её удалили). Встречу НЕ завершаем мгновенно:
        # обновление страницы или обрыв сети — это не конец встречи. Сверяем присутствие; завершит reaper по истечении
        # льготного периода MEETING_END_GRACE_SECONDS, а вернувшийся участник пересоздаст комнату тем же именем.
        async with request.app.state.session_maker() as db:
            meeting = await db.get(Meeting, meeting_id)
            if meeting is not None and meeting.ended_at is None:
                await request.app.state.meetings.reconcile(db, meeting)
    return {"ok": True}


@router.post("/smoke", dependencies=[Depends(require_internal)])
async def smoke(request: Request):
    """Тестовая внутренняя сессия без реального пользователя: встреча → токен LiveKit →
    синтетическая реплика через Redis-стрим → запись в БД. Всё созданное удаляется."""
    app = request.app
    settings = app.state.settings
    steps: dict[str, bool] = {}
    suffix = secrets.token_hex(4)
    user_id, room_id, meeting_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    error: str | None = None
    try:
        async with app.state.session_maker() as db:
            db.add(User(id=user_id, ad_guid=f"smoke-{uuid.uuid4()}", sam_account_name=f"smoke-{suffix}", display_name="Smoke Test"))
            db.add(Room(id=room_id, slug=f"smoke-{suffix}", name="Smoke Test Room", transcription_enabled=True))
            await db.flush()
            db.add(Meeting(id=meeting_id, room_id=room_id, livekit_room=meeting_room_name(meeting_id), started_by_user_id=user_id))
            await db.flush()
            db.add(MeetingParticipant(meeting_id=meeting_id, user_id=user_id))
            await db.commit()
        steps["db_session_created"] = True

        token = issue_user_token(settings, user_id=user_id, display_name="Smoke Test", livekit_room=meeting_room_name(meeting_id),
                                 camera_allowed=True, screen_share_allowed=True)
        claims = lkapi.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret).verify(token)
        steps["livekit_token_identity_bound"] = claims.identity == user_identity(user_id)

        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        await app.state.redis.xadd(SEGMENTS_STREAM, {
            "segment_uid": str(uid), "meeting_id": meeting_room_name(meeting_id), "identity": user_identity(user_id),
            "started_at": (now - timedelta(seconds=1)).isoformat(), "ended_at": now.isoformat(),
            "text": "проверка связи", "language": "ru",
            "model": '{"provider":"smoke","name":"synthetic"}',
        })
        deadline = time.monotonic() + 15
        stored = False
        while time.monotonic() < deadline and not stored:
            async with app.state.session_maker() as db:
                row = (await db.execute(select(TranscriptSegment).where(TranscriptSegment.segment_uid == uid))).scalars().first()
                stored = bool(row and row.user_id == user_id)
            if not stored:
                await asyncio.sleep(0.3)
        steps["segment_pipeline_to_db"] = stored
    except Exception as exc:  # noqa: BLE001
        error = type(exc).__name__
        log.exception("Smoke-сессия завершилась ошибкой")
    finally:
        async with app.state.session_maker() as db:
            await db.execute(delete(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id))
            await db.execute(delete(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id))
            await db.execute(delete(Meeting).where(Meeting.id == meeting_id))
            await db.execute(delete(Room).where(Room.id == room_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
    ok = error is None and steps and all(steps.values())
    return {"ok": bool(ok), "steps": steps, "error": error}


@router.get("/diag/token", dependencies=[Depends(require_internal)])
async def diag_token(request: Request):
    """Одноразовый короткоживущий токен скрытого тестового участника в служебной комнате diag-*: для проверки реального
    WebSocket Upgrade со стороны хоста (scripts/smoke-test.sh). Пользовательских комнат и встреч не касается."""
    import secrets as _secrets

    room = f"diag-{_secrets.token_hex(4)}"
    return {"room": room, "token": diagnostics._diag_token(request.app.state.settings, room)}  # noqa: SLF001


@router.get("/diag", dependencies=[Depends(require_internal)])
async def diag(request: Request):
    """Диагностика для smoke-test: БД, Redis, LDAP (bind сервисной учётки), LiveKit, ASR, версия сборки. Секреты не возвращаются."""
    import httpx
    from sqlalchemy import text

    app = request.app
    s = app.state.settings
    out: dict = {"version": s.app_version, "commit": s.app_git_commit, "built_at": s.app_built_at}
    try:
        async with app.state.session_maker() as db:
            await db.execute(text("SELECT 1"))
        out["postgres"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        out["postgres"] = {"ok": False, "error": type(exc).__name__}
    try:
        await app.state.redis.ping()
        out["redis"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        out["redis"] = {"ok": False, "error": type(exc).__name__}
    try:
        await asyncio.to_thread(app.state.directory.check_service_account)
        out["ldap"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        out["ldap"] = {"ok": False, "error": getattr(exc, "code", type(exc).__name__)}
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            out["livekit"] = {"ok": (await c.get(s.livekit_http_url + "/")).status_code == 200}
    except Exception as exc:  # noqa: BLE001
        out["livekit"] = {"ok": False, "error": type(exc).__name__}
    hb = await app.state.bridge.heartbeat()
    out["asr"] = {"ok": bool(hb and hb.get("model_loaded")), "provider": (hb or {}).get("provider"), "commit": (hb or {}).get("commit")}
    out["versions"] = {"livekit_server": s.livekit_server_version or "unknown", "livekit_python_sdk_asr": (hb or {}).get("livekit_sdk")}
    if request.query_params.get("deep") == "1":  # реальный WebSocket Upgrade на /rtc/v1 (создаёт и сразу закрывает тестовое соединение)
        out["livekit_ws"] = await diagnostics.livekit_checks(s)
        out["kernel"] = diagnostics.kernel_report()
    out["ok"] = all(out[k]["ok"] for k in ("postgres", "redis", "ldap", "livekit", "asr"))
    return out
