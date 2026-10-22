"""Один микрофон на человека: событие mic_owner для своих устройств; чужим устройством управлять нельзя."""
from __future__ import annotations

from app.services import events as ev

from .conftest import login, make_room
from .test_transcripts import _join

API = "/api/v1"


def test_mic_owner_event_only_for_own_device(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    b = _join(client, "bob", room["id"])
    mid = a["meeting_id"]
    seen: list[dict] = []
    orig = ev.publish

    async def spy(redis, meeting_id, event):
        seen.append(event)
        return await orig(redis, meeting_id, event)
    ev.publish = spy
    try:
        login(client, "alice")
        assert client.post(f"{API}/meetings/{mid}/mic-owner", json={"identity": a["identity"] + "~ab12cd34"}).status_code == 204
        assert client.post(f"{API}/meetings/{mid}/mic-owner", json={"identity": b["identity"]}).status_code == 403, "чужое устройство"
        assert client.post(f"{API}/meetings/{mid}/mic-owner", json={"identity": "asr-x"}).status_code == 403
    finally:
        ev.publish = orig
    owners = [e for e in seen if e.get("type") == "mic_owner"]
    assert len(owners) == 1 and owners[0]["identity"].startswith(a["identity"]) and owners[0]["user_id"]
