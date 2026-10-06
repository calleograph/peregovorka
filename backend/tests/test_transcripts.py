from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from livekit import api as lkapi
from sqlalchemy import select

from app.models import Meeting, MeetingParticipant, TranscriptSegment
from app.services.asr_bridge import SEGMENTS_STREAM
from app.workers.segment_consumer import ensure_group, process_batch

from .conftest import login, make_room


def _join(client, user: str, room_id: str) -> dict:
    login(client, user)
    r = client.post(f"/api/v1/rooms/{room_id}/join", json={})
    assert r.status_code == 200, r.text
    return r.json()


def _segment(meeting_id: str, identity: str, text: str, *, offset: float = 0.0, uid: str | None = None, dur: float = 2.0) -> dict:
    start = datetime.now(timezone.utc) + timedelta(seconds=offset)
    return {
        "segment_uid": uid or str(uuid.uuid4()), "meeting_id": f"m-{uuid.UUID(meeting_id).hex}", "identity": identity,
        "started_at": start.isoformat(), "ended_at": (start + timedelta(seconds=dur)).isoformat(), "text": text,
        "language": "ru", "model": json.dumps({"provider": "gigaam", "name": "v3_e2e_rnnt", "device": "cpu"}),
        "duration_ms": str(int(dur * 1000)), "infer_ms": "120", "queue_ms": "5",
    }


def _feed_and_consume(client, messages: list[dict]) -> None:
    app = client.app_obj

    async def _run():
        await ensure_group(app.state.redis)
        for m in messages:
            await app.state.redis.xadd(SEGMENTS_STREAM, m)
        await process_batch(app.state.redis, app.state.session_maker, "test-consumer", block_ms=50)

    client.portal.call(_run)


def test_segment_is_stored_with_correct_user_from_track_identity(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    _join(client, "bob", room["id"])
    _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], "Добрый день, коллеги")])

    login(client, "alice")
    segs = client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").json()["segments"]
    assert len(segs) == 1
    assert segs[0]["text"] == "Добрый день, коллеги"
    assert segs[0]["display_name"] == "Alice A" and segs[0]["identity"] == a["identity"]


def test_parallel_speech_of_different_users_gives_independent_records(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    b = _join(client, "bob", room["id"])
    same_time = [_segment(a["meeting_id"], a["identity"], "Это говорит Алиса", offset=0, dur=3),
                 _segment(a["meeting_id"], b["identity"], "А это одновременно Боб", offset=0.5, dur=3)]
    _feed_and_consume(client, same_time)

    login(client, "bob")
    segs = client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").json()["segments"]
    by_name = {s["display_name"]: s for s in segs}
    assert set(by_name) == {"Alice A", "Bob B"}
    # интервалы перекрываются — это именно два независимых сегмента, а не склейка
    s1, s2 = by_name["Alice A"], by_name["Bob B"]
    assert s1["started_at"] < s2["ended_at"] and s2["started_at"] < s1["ended_at"]
    assert s1["text"] == "Это говорит Алиса" and s2["text"] == "А это одновременно Боб"


def test_duplicate_delivery_is_idempotent(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    uid = str(uuid.uuid4())
    msg = _segment(a["meeting_id"], a["identity"], "Один раз", uid=uid)
    _feed_and_consume(client, [msg, msg])
    login(client, "alice")
    assert len(client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").json()["segments"]) == 1


def test_identity_of_non_participant_is_not_attributed_to_user(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    login(client, "carol")  # carol существует, но во встрече не участвует
    me_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    _feed_and_consume(client, [_segment(a["meeting_id"], f"u-{uuid.UUID(me_id).hex}", "Подмена личности")])
    login(client, "alice")
    segs = client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").json()["segments"]
    assert segs[0]["user_id"] is None and segs[0]["display_name"] == "Неизвестный участник"


def test_service_identity_and_garbage_are_rejected(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    _feed_and_consume(client, [_segment(a["meeting_id"], "asr-worker", "служебное"),
                               {"segment_uid": "not-a-uuid", "meeting_id": "x"},
                               _segment(a["meeting_id"], a["identity"], "   ")])
    login(client, "alice")
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").json()["segments"] == []


def test_transcript_visible_only_to_participants_and_admin(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], "Секретно")])
    login(client, "carol")
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").status_code == 404
    assert client.get("/api/v1/meetings").json() == []
    login(client, "root")
    assert len(client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").json()["segments"]) == 1
    login(client, "alice")
    assert [m["id"] for m in client.get("/api/v1/meetings").json()] == [a["meeting_id"]]


def test_stream_message_is_acked_only_after_db_write(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    app = client.app_obj
    _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], "ack")])
    pending = client.portal.call(lambda: app.state.redis.xpending(SEGMENTS_STREAM, "backend"))
    assert pending["pending"] == 0


# ---- WebSocket: живые события
def test_websocket_pushes_new_segment_to_subscribed_participant_only(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    login(client, "alice")
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": a["meeting_id"]})
        assert ws.receive_json()["type"] == "subscribed"
        _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], "Живая реплика")])
        evt = ws.receive_json()
        assert evt["type"] == "segment" and evt["segment"]["text"] == "Живая реплика"
        assert evt["segment"]["display_name"] == "Alice A"

    login(client, "carol")
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": a["meeting_id"]})
        assert ws.receive_json()["type"] == "error"


def test_websocket_requires_session_and_origin(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws"):
            pass
    login(client, "alice")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws", headers={"origin": "https://evil.example"}):
            pass


# ---- webhook LiveKit
def _signed_webhook(settings, payload: dict):
    body = json.dumps(payload)
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    token = lkapi.AccessToken(settings.livekit_api_key, settings.livekit_api_secret).with_sha256(digest).to_jwt()
    return body, token


def test_webhook_rejects_unsigned_and_accepts_signed(client, settings):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    body, token = _signed_webhook(settings, {
        "event": "participant_joined", "room": {"name": a["livekit_room"]}, "participant": {"identity": a["identity"]}})
    assert client.post("/internal/v1/livekit/webhook", content=body).status_code == 401
    assert client.post("/internal/v1/livekit/webhook", content=body, headers={"Authorization": "bad"}).status_code == 401
    assert client.post("/internal/v1/livekit/webhook", content=body, headers={"Authorization": token}).status_code == 200

    app = client.app_obj

    async def _connected():
        async with app.state.session_maker() as db:
            return (await db.execute(select(MeetingParticipant.connected_at))).scalars().all()

    assert client.portal.call(_connected)[0] is not None


def test_last_participant_left_then_grace_ends_meeting_and_stops_asr(client, settings):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    body, token = _signed_webhook(settings, {
        "event": "participant_left", "room": {"name": a["livekit_room"]}, "participant": {"identity": a["identity"]}})
    assert client.post("/internal/v1/livekit/webhook", content=body, headers={"Authorization": token}).status_code == 200
    app = client.app_obj

    async def _reap():
        async with app.state.session_maker() as db:
            m = await db.get(Meeting, uuid.UUID(a["meeting_id"]))
            assert m.empty_since is not None and m.ended_at is None
            # grace=0 в тестовых настройках; LiveKit недоступен (сверка пропускается), но empty_since уже достаточно
            await app.state.meetings.reconcile(db, m)
            await db.refresh(m)
            return m.ended_at, m.end_reason

    ended_at, reason = client.portal.call(_reap)
    assert ended_at is not None and reason == "empty"
    assert a["meeting_id"] not in client.portal.call(lambda: app.state.redis.hgetall("asr:sessions"))


def test_internal_smoke_requires_token(client):
    assert client.post("/internal/v1/smoke").status_code == 401
    assert client.post("/internal/v1/smoke", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_internal_smoke_session_end_to_end(client_with_workers):
    """Внутренняя тестовая сессия без реального пользователя: встреча → токен → реплика → БД, затем очистка."""
    c = client_with_workers
    r = c.post("/internal/v1/smoke", headers={"Authorization": "Bearer internal-test-token"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    assert body["steps"] == {"db_session_created": True, "livekit_token_identity_bound": True, "segment_pipeline_to_db": True}
    app = c.app_obj

    async def _leftovers():
        from app.models import Room, User
        async with app.state.session_maker() as db:
            return [
                (await db.execute(select(TranscriptSegment))).scalars().all(),
                (await db.execute(select(Room))).scalars().all(),
                (await db.execute(select(User))).scalars().all(),
            ]

    assert c.portal.call(_leftovers) == [[], [], []], "smoke обязан убрать за собой"


def test_internal_diag_reports_components_without_secrets(client):
    assert client.get("/internal/v1/diag").status_code == 401
    r = client.get("/internal/v1/diag", headers={"Authorization": "Bearer internal-test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["postgres"]["ok"] and body["redis"]["ok"] and body["ldap"]["ok"]
    assert body["livekit"]["ok"] is False and body["asr"]["ok"] is False and body["ok"] is False
    assert {"version", "commit", "built_at"} <= set(body)
    assert "secret" not in r.text.lower() and "internal-test-token" not in r.text


def test_version_endpoint_exposes_build_metadata(tmp_path, directory):
    from .conftest import make_settings, running_app

    s = make_settings(tmp_path, app_version="0.1.0", app_git_commit="1d620bd", app_built_at="2026-10-06T10:00:00Z")
    with running_app(s, directory) as c:
        assert c.get("/api/v1/version").json() == {"version": "0.1.0", "commit": "1d620bd", "built_at": "2026-10-06T10:00:00Z"}
