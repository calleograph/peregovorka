from __future__ import annotations

import json
import uuid

from .conftest import login, make_room
from .test_collab_guests import _join, guest_join

KEY = "e2ee:{}"


def _set(client, room_id, mode):
    login(client, "root")
    r = client.patch(f"/api/v1/admin/rooms/{room_id}", json={"e2ee_mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def _redis(client, coro_fn):
    return client.portal.call(coro_fn)


def test_no_key_when_e2ee_off(client):
    room = make_room(client)
    j = client.post(f"/api/v1/rooms/{room['id']}/join", json={})
    assert j.status_code == 200 and j.json()["e2ee"] is None and j.json()["room"]["e2ee_mode"] == "off"


def test_trusted_mode_gives_same_key_to_all_and_hands_it_to_asr(client):
    room = make_room(client, guest_access_enabled=True)
    _set(client, room["id"], "trusted")
    a = _join(client, "alice", room["id"])
    assert a["e2ee"]["mode"] == "trusted" and len(a["e2ee"]["key"]) >= 40
    key = a["e2ee"]["key"]
    b = _join(client, "bob", room["id"])
    assert b["e2ee"]["key"] == key                                            # один ключ на встречу
    g = guest_join(client, room["guest_token"]).json()
    assert g["e2ee"]["key"] == key                                            # гость тоже получает ключ встречи

    async def asr_payload():
        raw = await client.app_obj.state.redis.hget("asr:sessions", a["meeting_id"])
        return json.loads(raw) if raw else None
    p = client.portal.call(asr_payload)
    assert p and p["e2ee_key"] == key and p["transcribe"] is True               # доверенный компонент получает ключ и работает


def test_full_mode_disables_server_transcription_and_recording_and_hides_key_from_asr(client):
    room = make_room(client, record_audio=True)
    _set(client, room["id"], "full")
    a = _join(client, "alice", room["id"])
    assert a["e2ee"]["mode"] == "full"
    mid = a["meeting_id"]
    assert a["transcription"] is False and a["recording"] is False

    async def asr_payload():
        raw = await client.app_obj.state.redis.hget("asr:sessions", mid)
        return json.loads(raw)
    p = client.portal.call(asr_payload)
    assert "e2ee_key" not in p and p["transcribe"] is False and p["record_audio"] is False
    login(client, "alice")
    r = client.post(f"/api/v1/meetings/{mid}/transcription", json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "e2ee_full"
    r = client.post(f"/api/v1/meetings/{mid}/recording", json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "e2ee_full"


def test_key_dropped_when_meeting_ends_and_new_meeting_gets_new_key(client):
    room = make_room(client)
    _set(client, room["id"], "trusted")
    a = _join(client, "alice", room["id"])
    mid, key = a["meeting_id"], a["e2ee"]["key"]
    login(client, "root")
    assert client.post(f"/api/v1/admin/meetings/{mid}/end").status_code in (200, 204)

    async def exists():
        return await client.app_obj.state.redis.exists(KEY.format(mid))
    assert client.portal.call(exists) == 0
    a2 = _join(client, "alice", room["id"])
    assert a2["meeting_id"] != mid and a2["e2ee"]["key"] != key


def test_mode_change_applies_to_next_meeting_only(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])                                      # встреча идёт без E2EE
    _set(client, room["id"], "trusted")
    b = _join(client, "bob", room["id"])
    assert b["meeting_id"] == a["meeting_id"] and b["e2ee"] is None             # идущую встречу посреди звонка не шифруем: клиенты уже подключены без ключа


def test_validation_and_sip_conflict(client):
    room = make_room(client)
    login(client, "root")
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"e2ee_mode": "maybe"}).status_code == 422

    async def sip_on():
        from app.models import Room
        async with client.app_obj.state.session_maker() as db:
            r = await db.get(Room, uuid.UUID(room["id"]))
            r.sip_mode = "outbound"
            await db.commit()
    client.portal.call(sip_on)
    r = client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"e2ee_mode": "full"})
    assert r.status_code == 409 and "SIP" in r.json()["detail"]
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"e2ee_mode": "off", "name": "ok"}).status_code == 200


def test_leader_can_choose_mode(client):
    room = make_room(client)
    login(client, "root")
    r = client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"e2ee_mode": "trusted"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/v1/rooms/{room['id']}/manage").json()["e2ee_mode"] == "trusted"
