from __future__ import annotations

import uuid

from livekit import api as lkapi

from .conftest import OTHER_GROUP, STAFF_GROUP, login, make_room


def _claims(settings, token: str):
    return lkapi.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret).verify(token)


def test_user_sees_only_rooms_allowed_by_acl(client):
    open_room = make_room(client, name="Для штата")
    make_room(client, name="Для других", acl=[{"subject_type": "group", "subject_ref": OTHER_GROUP}])
    make_room(client, name="Закрытая", acl=[])
    login(client, "alice")
    names = {r["name"] for r in client.get("/api/v1/rooms").json()}
    assert names == {"Для штата"}
    login(client, "carol")
    assert {r["name"] for r in client.get("/api/v1/rooms").json()} == {"Для других"}
    login(client, "root")  # админ видит все включённые
    assert len(client.get("/api/v1/rooms").json()) == 3
    assert open_room["id"]


def test_cannot_join_foreign_room(client):
    foreign = make_room(client, name="Чужая", acl=[{"subject_type": "group", "subject_ref": OTHER_GROUP}])
    login(client, "alice")
    r = client.post(f"/api/v1/rooms/{foreign['id']}/join", json={})
    assert r.status_code == 404  # неотличимо от несуществующей
    assert client.post(f"/api/v1/rooms/{uuid.uuid4()}/join", json={}).status_code == 404


def test_disabled_room_cannot_be_joined(client):
    room = make_room(client, is_enabled=False)
    login(client, "alice")
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={}).status_code == 404
    assert room["id"] not in [r["id"] for r in client.get("/api/v1/rooms").json()]


def test_user_specific_acl_by_ad_guid(client, directory):
    ident = directory.users["carol"][1]
    room = make_room(client, acl=[{"subject_type": "user", "subject_ref": ident.ad_guid.upper()}])
    login(client, "carol")
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={}).status_code == 200


def test_token_identity_is_bound_to_internal_user_id_not_input(client, settings):
    room = make_room(client, camera_allowed=True, screen_share_allowed=False)
    login(client, "alice")
    me = client.get("/api/v1/auth/me").json()["user"]
    # попытка подсунуть чужую identity/имя через тело запроса — игнорируется
    r = client.post(f"/api/v1/rooms/{room['id']}/join", json={"identity": "u-evil", "name": "Boss", "room": "other"})
    assert r.status_code == 200
    body = r.json()
    claims = _claims(settings, body["token"])
    assert claims.identity == f"u-{uuid.UUID(me['id']).hex}" == body["identity"]
    assert claims.name == "Alice A"
    assert claims.video.room == body["livekit_room"] == f"m-{uuid.UUID(body['meeting_id']).hex}"
    assert claims.video.room_join is True
    assert not claims.video.room_admin and not claims.video.room_create and not claims.video.room_record
    assert claims.video.can_publish_data is False and not claims.video.hidden
    assert set(claims.video.can_publish_sources) == {"microphone", "camera"}  # экран запрещён комнатой
    import jwt, time
    raw = jwt.decode(body['token'], settings.livekit_api_secret, algorithms=['HS256'])
    assert 0 < raw['exp'] - time.time() <= settings.livekit_token_ttl_seconds + 5, 'токен короткоживущий'
    assert body["livekit_url"] == "wss://meet.test/livekit"


def test_two_users_get_different_identities_same_meeting(client, settings):
    room = make_room(client)
    login(client, "alice")
    a = client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()
    login(client, "bob")
    b = client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()
    assert a["meeting_id"] == b["meeting_id"], "в комнате одна активная встреча"
    assert a["identity"] != b["identity"]
    assert _claims(settings, a["token"]).identity != _claims(settings, b["token"]).identity


def test_room_password_required_and_throttled(client):
    room = make_room(client, password="s3cret")
    assert room["has_password"] is True
    login(client, "alice")
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()["detail"]["code"] == "room_password_required"
    for _ in range(4):
        assert client.post(f"/api/v1/rooms/{room['id']}/join", json={"password": "wrong"}).status_code == 403
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={"password": "wrong"}).status_code == 403
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={"password": "s3cret"}).status_code == 429  # перебор остановлен


def test_room_password_ok(client):
    room = make_room(client, password="s3cret")
    login(client, "alice")
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={"password": "s3cret"}).status_code == 200


def test_room_capacity(client):
    room = make_room(client, max_participants=1)
    login(client, "alice")
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={}).status_code == 200
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={}).status_code == 200  # тот же человек, переподключение
    login(client, "bob")
    r = client.post(f"/api/v1/rooms/{room['id']}/join", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "room_full"


def test_join_starts_meeting_and_commands_asr(client):
    room = make_room(client)
    login(client, "alice")
    body = client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()
    app = client.app_obj

    async def _check():
        sessions = await app.state.redis.hgetall("asr:sessions")
        stream = await app.state.redis.xrange("asr:control")
        return sessions, stream

    sessions, stream = client.portal.call(_check)
    assert body["meeting_id"] in sessions
    assert stream and stream[0][1]["type"] == "start" and stream[0][1]["meeting_id"] == body["meeting_id"]
    listed = client.get("/api/v1/rooms").json()
    assert listed[0]["active_meeting"]["id"] == body["meeting_id"]


def test_join_works_when_asr_is_down(client):
    """ASR недоступен — комнаты работают, readiness = degraded (не падает)."""
    room = make_room(client)
    login(client, "alice")
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={}).status_code == 200
    # LiveKit в тесте недоступен, поэтому ready=503; ASR heartbeat отсутствует — это не причина отказа
    r = client.get("/api/v1/health/ready")
    assert r.json()["checks"]["asr"]["ok"] is False
    assert r.json()["checks"]["postgres"]["ok"] and r.json()["checks"]["redis"]["ok"]


def test_manual_end_stops_asr_and_blocks_foreign_users(client):
    room = make_room(client)
    login(client, "alice")
    mid = client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()["meeting_id"]
    login(client, "carol")  # не участник
    assert client.get(f"/api/v1/meetings/{mid}").status_code == 404
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 404
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    assert client.get(f"/api/v1/meetings/{mid}").json()["ended_at"] is not None
    app = client.app_obj
    sessions = client.portal.call(lambda: app.state.redis.hgetall("asr:sessions"))
    assert mid not in sessions
    # после завершения следующий вход создаёт НОВУЮ встречу
    again = client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()["meeting_id"]
    assert again != mid
    assert STAFF_GROUP
