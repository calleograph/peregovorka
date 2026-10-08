"""Роли и права комнаты: презентационный режим, «слово», удаление участника, доска, запись/транскрибация, права руководителя."""
from __future__ import annotations

import json

import pytest
from livekit import api as lkapi

from .conftest import OTHER_GROUP, login, make_room
from .test_collab_guests import BOARD, guest_join
from .test_transcripts import _join

LEADERS = [{"subject_type": "group", "subject_ref": OTHER_GROUP, "display_name": "Руководители"}]  # в группе — carol; участники — staff (alice, bob)


@pytest.fixture
def lk_calls(monkeypatch):
    """Звонковый сервер подменён: фиксируем, кому и какие права выданы, кого отключили."""
    rec = {"perm": [], "removed": []}

    async def perm(settings, room_name, identity, sources):
        rec["perm"].append((identity, list(sources)))
        return True

    async def removed(settings, room_name, identity):
        rec["removed"].append(identity)

    monkeypatch.setattr("app.services.meetings.set_publish_permission", perm)
    monkeypatch.setattr("app.services.meetings.remove_participant", removed)
    return rec


def grants(token: str):
    return lkapi.TokenVerifier("devkey", "s" * 40).verify(token).video


def presentation(client, **over):
    return make_room(client, room_type="presentation", moderators=LEADERS, **over)


# ------------------------------------------------------------------------------------------- токены
def test_presentation_room_listener_cannot_publish_but_leader_and_admin_can(client, lk_calls):
    room = presentation(client)
    carol = _join(client, "carol", room["id"])
    alice = _join(client, "alice", room["id"])
    root = _join(client, "root", room["id"])
    g_alice = grants(alice["token"])
    assert g_alice.can_publish is False and list(g_alice.can_publish_sources) == [] and g_alice.can_subscribe is True
    assert alice["client"]["presentation"] is True and alice["client"]["sources"] == [] and alice["client"]["can_edit_board"] is False
    for who in (carol, root):
        g = grants(who["token"])
        assert g.can_publish is True and {"microphone", "camera", "screen_share"} <= set(g.can_publish_sources)
        assert who["client"]["can_manage"] is True and who["client"]["can_edit_board"] is True


def test_regular_room_is_unchanged_participants_publish_by_room_rights(client, lk_calls):
    room = make_room(client, screen_share_allowed=False)
    alice = _join(client, "alice", room["id"])
    g = grants(alice["token"])
    assert g.can_publish is True and set(g.can_publish_sources) == {"microphone", "camera"}
    assert alice["client"]["presentation"] is False and alice["client"]["can_edit_board"] is True


def test_leader_publishes_everything_even_if_room_forbids_camera_and_screen_for_participants(client, lk_calls):
    room = make_room(client, camera_allowed=False, screen_share_allowed=False, moderators=LEADERS)
    assert set(grants(_join(client, "alice", room["id"])["token"]).can_publish_sources) == {"microphone"}
    assert {"camera", "screen_share"} <= set(grants(_join(client, "carol", room["id"])["token"]).can_publish_sources)


# ----------------------------------------------------------------------------------------- слово
def test_leader_gives_and_takes_the_floor_and_rejoin_keeps_it(client, lk_calls):
    room = presentation(client)
    c = _join(client, "carol", room["id"])
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{mid}/moderation/floor", json={"identity": a["identity"], "granted": True})
    assert r.status_code == 200 and r.json() == {"identity": a["identity"], "granted": True}
    assert lk_calls["perm"][-1] == (a["identity"], ["microphone", "camera", "screen_share", "screen_share_audio"])
    assert client.get(f"/api/v1/meetings/{mid}/floor").json() == {"presentation": True, "floor": [a["identity"]], "leaders": [c["identity"]]}
    again = _join(client, "alice", room["id"])                       # переподключение (обрыв сети): слово сохраняется
    assert grants(again["token"]).can_publish is True and again["client"]["floor"] is True and again["client"]["can_edit_board"] is True
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/floor", json={"identity": a["identity"], "granted": False}).json()["granted"] is False
    assert lk_calls["perm"][-1] == (a["identity"], [])
    assert client.get(f"/api/v1/meetings/{mid}/floor").json()["floor"] == []
    assert grants(_join(client, "alice", room["id"])["token"]).can_publish is False


def test_floor_rules_regular_room_leader_and_strangers_and_ordinary_users(client, lk_calls):
    reg = make_room(client, moderators=LEADERS)
    a = _join(client, "alice", reg["id"])
    _join(client, "carol", reg["id"])
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{a['meeting_id']}/moderation/floor", json={"identity": a["identity"], "granted": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_presentation"
    room = presentation(client)
    c = _join(client, "carol", room["id"])
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/floor", json={"identity": c["identity"], "granted": False}).json()["detail"]["code"] == "already_privileged"
    assert client.post(f"/api/v1/meetings/{mid}/moderation/floor", json={"identity": "u-" + "0" * 32, "granted": True}).status_code == 404
    assert client.post(f"/api/v1/meetings/{mid}/moderation/floor", json={"identity": "asr-bot", "granted": True}).status_code == 422
    assert client.post(f"/api/v1/meetings/{mid}/moderation/floor", json={"identity": a["identity"], "granted": "yes"}).status_code == 422
    login(client, "alice")      # обычный участник слово давать не может
    assert client.post(f"/api/v1/meetings/{mid}/moderation/floor", json={"identity": a["identity"], "granted": True}).status_code == 403


def test_floor_is_not_changed_when_livekit_is_unavailable(client, monkeypatch):
    async def down(*a, **k):
        return False

    monkeypatch.setattr("app.services.meetings.set_publish_permission", down)
    room = presentation(client)
    _join(client, "carol", room["id"])
    a = _join(client, "alice", room["id"])
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{a['meeting_id']}/moderation/floor", json={"identity": a["identity"], "granted": True})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "livekit_unavailable"
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/floor").json()["floor"] == []


def test_floor_works_for_guests_and_is_reset_after_the_meeting(client, lk_calls):
    room = presentation(client, guest_access_enabled=True)
    c = _join(client, "carol", room["id"])
    g = guest_join(client, room["guest_token"]).json()
    assert grants(g["token"]).can_publish is False and g["client"]["sources"] == []
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{c['meeting_id']}/moderation/floor", json={"identity": g["identity"], "granted": True}).status_code == 200
    assert lk_calls["perm"][-1] == (g["identity"], ["microphone", "camera"])      # гость: без показа экрана
    assert client.post(f"/api/v1/meetings/{c['meeting_id']}/end").status_code == 204
    r = client.app_obj.state.redis
    assert client.portal.call(lambda: r.smembers(f"floor:{c['meeting_id']}")) == set(), "слово — состояние встречи: после неё сбрасывается"


# --------------------------------------------------------------------------------------- удаление
def test_leader_removes_participant_who_cannot_return_at_once_and_leader_cannot_be_removed(client, lk_calls):
    room = presentation(client)
    c = _join(client, "carol", room["id"])
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/kick", json={"identity": c["identity"]}).status_code == 403
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/kick", json={"identity": c["identity"]}).json()["detail"]["code"] == "privileged"
    assert client.post(f"/api/v1/meetings/{mid}/moderation/kick", json={"identity": a["identity"]}).json() == {"identity": a["identity"], "removed": True}
    assert lk_calls["removed"] == [a["identity"]]
    login(client, "alice")
    r = client.post(f"/api/v1/rooms/{room['id']}/join", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "removed_from_meeting"
    login(client, "carol")
    m = client.get(f"/api/v1/meetings/{mid}").json()
    assert [p["online"] for p in m["participants"] if p["display_name"] == "Alice A"] == [False]


def test_removed_guest_loses_the_session(client, lk_calls):
    room = make_room(client, guest_access_enabled=True, moderators=LEADERS)
    c = _join(client, "carol", room["id"])
    g = guest_join(client, room["guest_token"]).json()
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{c['meeting_id']}/moderation/kick", json={"identity": g["identity"]}).status_code == 200
    client.cookies.clear()
    assert client.get(f"/api/v1/meetings/{c['meeting_id']}/chat", headers={"X-Guest-Token": g["guest_token"]}).status_code == 401


# ------------------------------------------------------------------------------------------ доска
def test_board_rights_follow_room_type_floor_and_room_policy(client, lk_calls):
    room = presentation(client)
    c = _join(client, "carol", room["id"])
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    login(client, "alice")
    assert client.get(f"/api/v1/meetings/{mid}/whiteboard").json()["can_edit"] is False
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 1}}).status_code == 403
    assert client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 0}).status_code == 403
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 1}}).status_code == 200
    client.post(f"/api/v1/meetings/{mid}/moderation/floor", json={"identity": a["identity"], "granted": True})
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 2}}).status_code == 200
    assert client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 2}).status_code == 200
    assert client.get(f"/api/v1/meetings/{mid}/whiteboard").json()["can_edit"] is True
    # обычная комната с запретом доски: участники читают, правят только руководители
    reg = make_room(client, moderators=LEADERS, board_allowed=False)
    c2 = _join(client, "carol", reg["id"])
    _join(client, "alice", reg["id"])
    login(client, "alice")
    assert client.get(f"/api/v1/meetings/{c2['meeting_id']}/whiteboard").status_code == 200
    assert client.post(f"/api/v1/meetings/{c2['meeting_id']}/whiteboard/patch", json={"patch": {"x": 1}}).status_code == 403
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{c2['meeting_id']}/whiteboard/patch", json={"patch": {"x": 1}}).status_code == 200


# ----------------------------------------------------------------------- запись и транскрибация
def test_only_leaders_control_recording_when_room_has_leaders_but_anyone_when_it_has_none(client, lk_calls):
    led = make_room(client, moderators=LEADERS, record_audio=True)
    a = _join(client, "alice", led["id"])
    _join(client, "carol", led["id"])
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{a['meeting_id']}/recording", json={"enabled": True}).status_code == 403
    assert client.post(f"/api/v1/meetings/{a['meeting_id']}/transcription", json={"enabled": False}).status_code == 403
    assert a["client"]["can_control"] is False
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{a['meeting_id']}/recording", json={"enabled": True}).status_code == 200
    assert client.post(f"/api/v1/meetings/{a['meeting_id']}/transcription", json={"enabled": False}).status_code == 200
    free = make_room(client, record_audio=True)                      # без руководителей — как раньше, управляет любой участник
    b = _join(client, "bob", free["id"])
    assert b["client"]["can_control"] is True
    assert client.post(f"/api/v1/meetings/{b['meeting_id']}/transcription", json={"enabled": False}).status_code == 200


def test_auto_record_starts_audio_recording_with_the_meeting_and_manual_otherwise(client):
    auto = make_room(client, record_audio=True, auto_record=True)
    a = _join(client, "alice", auto["id"])
    assert a["recording"] is True and a["transcription"] is True
    app = client.app_obj
    sess = json.loads(client.portal.call(lambda: app.state.redis.hget("asr:sessions", a["meeting_id"])))
    assert sess["record_audio"] is True and sess["transcribe"] is True
    manual = make_room(client, record_audio=True)
    b = _join(client, "bob", manual["id"])
    assert b["recording"] is False and b["transcription"] is True and b["client"]["recording_allowed"] is True
    none = make_room(client)
    assert _join(client, "alice", none["id"])["client"]["recording_allowed"] is False


def test_auto_record_implies_audio_recording_is_allowed_in_admin_api(client):
    room = make_room(client, auto_record=True)
    assert room["auto_record"] is True and room["record_audio"] is True
    login(client, "root")
    patched = client.patch(f"/api/v1/admin/rooms/{make_room(client)['id']}", json={"auto_record": True}).json()
    assert patched["auto_record"] is True and patched["record_audio"] is True
    assert patched["room_type"] == "regular" and patched["board_allowed"] is True
