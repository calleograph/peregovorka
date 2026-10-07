"""Руководители комнат: выключить микрофоны всех или одного; микрофон по умолчанию выключен; приветствие; вход руководителя."""
from __future__ import annotations

import types

import pytest

from app.services import livekit as lk

from .conftest import OTHER_GROUP, login, make_room
from .test_transcripts import _join


@pytest.fixture
def calls(monkeypatch):
    rec: list[dict] = []

    async def fake(settings, room_name, *, only=None, exclude=None):
        rec.append({"room": room_name, "only": only, "exclude": exclude})
        return sorted(only) if only else ["u-fake1", "u-fake2"]

    monkeypatch.setattr("app.api.moderation.mute_microphones", fake)
    return rec


def room_with_moderator(client, **over):
    # руководитель — группа OTHER (в ней carol); обычные участники — staff (alice, bob)
    return make_room(client, moderators=[{"subject_type": "group", "subject_ref": OTHER_GROUP, "display_name": "Руководители"}], **over)


def test_moderator_group_can_enter_and_gets_the_flag_while_participants_do_not(client, calls):
    room = room_with_moderator(client)
    assert room["moderators"][0]["subject_ref"] == OTHER_GROUP.lower()
    carol = _join(client, "carol", room["id"])      # carol не в списке доступа (staff), но она руководитель
    assert carol["client"]["can_moderate"] is True
    assert _join(client, "alice", room["id"])["client"]["can_moderate"] is False
    assert _join(client, "root", room["id"])["client"]["can_moderate"] is True, "администратор сервера — руководитель любой комнаты"


def test_mute_all_and_mute_one_by_moderator_and_admin(client, calls):
    room = room_with_moderator(client)
    a = _join(client, "alice", room["id"])
    c = _join(client, "carol", room["id"])
    mid = a["meeting_id"]
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{mid}/moderation/mute-all")
    assert r.status_code == 200 and r.json() == {"muted": 2}
    assert calls[-1]["exclude"] == {c["identity"]}, "сам руководитель не заглушается"
    r = client.post(f"/api/v1/meetings/{mid}/moderation/mute", json={"identity": a["identity"]})
    assert r.status_code == 200 and calls[-1]["only"] == {a["identity"]}
    login(client, "root")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/mute-all").status_code == 200
    actions = [x["action"] for x in client.get("/api/v1/admin/audit").json()]
    assert {"meeting.mute_all", "meeting.mute"} <= set(actions)


def test_ordinary_participants_cannot_mute_and_input_is_validated(client, calls):
    room = room_with_moderator(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/mute-all").status_code == 403
    assert client.post(f"/api/v1/meetings/{mid}/moderation/mute", json={"identity": a["identity"]}).status_code == 403
    assert calls == []
    _join(client, "carol", room["id"])
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/mute", json={"identity": "asr-bot"}).status_code == 422
    assert client.post(f"/api/v1/meetings/{mid}/moderation/mute", json={}).status_code == 422
    assert client.post("/api/v1/meetings/00000000-0000-0000-0000-000000000000/moderation/mute-all").status_code == 404


def test_cannot_moderate_a_finished_meeting_or_when_livekit_is_down(client, monkeypatch, calls):
    room = room_with_moderator(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]

    async def down(*a, **k):
        return None

    monkeypatch.setattr("app.api.moderation.mute_microphones", down)
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/mute-all").status_code == 503
    login(client, "root")
    assert client.post(f"/api/v1/admin/meetings/{mid}/end").status_code == 204
    assert client.post(f"/api/v1/meetings/{mid}/moderation/mute-all").status_code == 404


def test_moderation_is_journaled(client, calls):
    room = room_with_moderator(client)
    a = _join(client, "alice", room["id"])
    _join(client, "carol", room["id"])
    login(client, "carol")
    client.post(f"/api/v1/meetings/{a['meeting_id']}/moderation/mute-all")
    client.portal.call(client.app_obj.state.journal.flush)
    login(client, "root")
    items = client.get("/api/v1/admin/journal", params={"filters": '[{"field":"event","op":"eq","value":"moderator_mute_all"}]'}).json()["items"]
    assert len(items) == 1 and items[0]["user"] == "carol" and items[0]["room"] == "Переговорка"


def test_room_options_mute_on_join_welcome_and_moderators_roundtrip(client):
    room = make_room(client, mute_on_join=True, welcome_message="Добро пожаловать! Микрофоны выключены.")
    assert room["mute_on_join"] is True and room["welcome_message"].startswith("Добро")
    j = _join(client, "alice", room["id"])["client"]
    assert j["mute_on_join"] is True and j["welcome_message"] == "Добро пожаловать! Микрофоны выключены."
    login(client, "root")
    r = client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"mute_on_join": False, "welcome_message": None,
                                                                 "moderators": [{"subject_type": "user", "subject_ref": "ABC-GUID"}, {"subject_type": "user", "subject_ref": "abc-guid"}]})
    assert r.status_code == 200 and r.json()["mute_on_join"] is False and r.json()["welcome_message"] is None
    assert [m["subject_ref"] for m in r.json()["moderators"]] == ["abc-guid"], "дубликаты убираются, ссылки приводятся к нижнему регистру"
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"moderators": []}).json()["moderators"] == []


# ---- сама функция работы с LiveKit (подставной клиент)
class _Track:
    def __init__(self, sid, source, muted=False):
        self.sid, self.source, self.muted = sid, source, muted


def _p(identity, *tracks):
    return types.SimpleNamespace(identity=identity, tracks=list(tracks))


@pytest.mark.parametrize("only,exclude,expect", [
    (None, None, {"u-a", "u-b"}),
    ({"u-a"}, None, {"u-a"}),
    (None, {"u-a"}, {"u-b"}),
])
async def test_mute_microphones_filters_participants_and_tracks(monkeypatch, only, exclude, expect):
    from livekit.protocol import models

    MIC, CAM = models.TrackSource.MICROPHONE, models.TrackSource.CAMERA
    muted_calls = []

    class Room:
        async def list_participants(self, req):
            return types.SimpleNamespace(participants=[
                _p("u-a", _Track("T1", MIC), _Track("T2", CAM)), _p("u-b", _Track("T3", MIC)), _p("u-c", _Track("T4", MIC, muted=True)),
                _p("asr-service", _Track("T5", MIC)), _p("u-d")])

        async def mute_published_track(self, req):
            muted_calls.append((req.identity, req.track_sid, req.muted))

    class FakeApi:
        def __init__(self, *a, **k):
            self.room = Room()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(lk.lkapi, "LiveKitAPI", FakeApi)
    settings = types.SimpleNamespace(livekit_http_url="http://x", livekit_api_key="k", livekit_api_secret="s")
    done = await lk.mute_microphones(settings, "room", only=only, exclude=exclude)
    assert set(done) == expect
    assert all(m for _, _, m in muted_calls) and {i for i, _, _ in muted_calls} == expect
    assert all(sid in {"T1", "T3"} for _, sid, _ in muted_calls), "камера, уже выключенный микрофон и служебные участники не затрагиваются"


async def test_mute_microphones_returns_none_when_livekit_is_unavailable():
    settings = types.SimpleNamespace(livekit_http_url="http://127.0.0.1:1", livekit_api_key="k", livekit_api_secret="s" * 40)
    assert await lk.mute_microphones(settings, "room") is None
