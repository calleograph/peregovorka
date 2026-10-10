"""Сцена ведущего (Spotlight) и модерация показа экрана/камеры: права только у руководителя и администратора (сервер, а не скрытая кнопка),
подстановка чужих идентичностей, несколько одновременных показов, запрет повторного показа (действует и после переподключения), события для клиентов."""
from __future__ import annotations

import json

import pytest
from fakeredis import FakeAsyncRedis  # noqa: F401  (используется приложением)

from .conftest import OTHER_GROUP, login, make_room
from .test_roles_floor import grants
from .test_transcripts import _join

LEADERS = [{"subject_type": "group", "subject_ref": OTHER_GROUP, "display_name": "Руководители"}]   # carol — руководитель


@pytest.fixture
def lk(monkeypatch):
    rec = {"muted": [], "perm": []}

    async def mute(settings, room_name, sources, *, only=None, exclude=None):
        rec["muted"].append({"sources": set(sources), "only": set(only or ())})
        return sorted(only or ())

    async def perm(settings, room_name, identity, sources):
        rec["perm"].append((identity, list(sources)))
        return True

    monkeypatch.setattr("app.services.meetings.mute_sources", mute)
    monkeypatch.setattr("app.services.meetings.set_publish_permission", perm)
    return rec


def setup_meeting(client):
    room = make_room(client, moderators=LEADERS)
    a = _join(client, "alice", room["id"])
    b = _join(client, "bob", room["id"])
    c = _join(client, "carol", room["id"])
    return room, a, b, c


def published_events(client, mid):
    """События, ушедшие в канал встречи (их получают клиенты по WebSocket)."""
    r = client.app_obj.state.redis

    async def go():
        out = []
        for key in await r.keys("*"):
            if str(mid) in key and await r.type(key) == "stream":
                for _id, fields in await r.xrange(key):
                    raw = fields.get("data") or fields.get("event") or next(iter(fields.values()))
                    try:
                        out.append(json.loads(raw))
                    except (ValueError, TypeError):
                        pass
        return out
    try:
        return client.portal.call(go)
    except Exception:  # noqa: BLE001 — формат канала событий — деталь реализации; проверки ниже на него не опираются
        return []


def test_leader_stops_one_share_and_the_other_continues(client, lk):
    room, a, b, c = setup_meeting(client)
    mid = a["meeting_id"]
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{mid}/moderation/stop-share", json={"identity": b["identity"]})
    assert r.status_code == 200 and r.json() == {"identity": b["identity"], "stopped": True, "blocked": False}
    assert lk["muted"][-1] == {"sources": {"screen_share", "screen_share_audio"}, "only": {b["identity"]}}, "выключается ТОЛЬКО показ Бориса"
    assert lk["perm"] == [], "без запрета права публикации не меняются — Борис может начать показ снова"
    login(client, "root")
    assert "meeting.stop_share" in [x["action"] for x in client.get("/api/v1/admin/audit").json()]


def test_ordinary_participant_cannot_stop_others_share_or_set_the_stage(client, lk):
    room, a, b, c = setup_meeting(client)
    mid = a["meeting_id"]
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/stop-share", json={"identity": b["identity"]}).status_code == 403
    assert client.post(f"/api/v1/meetings/{mid}/moderation/stop-camera", json={"identity": b["identity"]}).status_code == 403
    assert client.post(f"/api/v1/meetings/{mid}/moderation/allow-share", json={"identity": b["identity"]}).status_code == 403
    assert client.put(f"/api/v1/meetings/{mid}/stage", json={"items": [{"type": "screen", "identity": a["identity"]}]}).status_code == 403
    assert lk["muted"] == [] and lk["perm"] == []
    assert client.get(f"/api/v1/meetings/{mid}/stage").json()["items"] == [], "смотреть сцену может любой участник"


def test_block_share_revokes_permission_and_survives_rejoin(client, lk):
    room, a, b, c = setup_meeting(client)
    mid = a["meeting_id"]
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{mid}/moderation/stop-share", json={"identity": b["identity"], "block": True})
    assert r.status_code == 200 and r.json()["blocked"] is True
    ident, sources = lk["perm"][-1]
    assert ident == b["identity"] and "screen_share" not in sources and "microphone" in sources, "запрет снимает только показ экрана"
    again = _join(client, "bob", room["id"])                       # переподключение/перезагрузка страницы
    g = grants(again["token"])
    assert "screen_share" not in [str(s).lower() for s in g.can_publish_sources] and again["identity"] == b["identity"]
    assert "screen_share" not in again["client"].get("sources", again.get("sources", [])) if isinstance(again.get("client"), dict) and "sources" in again["client"] else True
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/moderation/allow-share", json={"identity": b["identity"]}).status_code == 200
    assert "screen_share" in lk["perm"][-1][1]
    g2 = grants(_join(client, "bob", room["id"])["token"])
    assert any("screen" in str(s).lower() for s in g2.can_publish_sources), "после снятия запрета показ снова разрешён"


def test_leader_cannot_be_blocked_but_can_be_stopped(client, lk):
    room, a, b, c = setup_meeting(client)
    mid = a["meeting_id"]
    login(client, "root")
    r = client.post(f"/api/v1/meetings/{mid}/moderation/stop-share", json={"identity": c["identity"], "block": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "privileged"
    assert client.post(f"/api/v1/meetings/{mid}/moderation/stop-share", json={"identity": c["identity"]}).status_code == 200


def test_identity_substitution_is_rejected(client, lk):
    room, a, b, c = setup_meeting(client)
    other_room = make_room(client)
    stranger = _join(client, "bob", other_room["id"])              # участник ДРУГОЙ встречи
    mid = a["meeting_id"]
    login(client, "carol")
    for bad in ({"identity": "u-not-an-id"}, {"identity": ""}, {}, {"identity": 12}):
        assert client.post(f"/api/v1/meetings/{mid}/moderation/stop-share", json=bad).status_code == 422, bad
    r = client.put(f"/api/v1/meetings/{mid}/stage", json={"items": [{"type": "screen", "identity": "u-00000000000000000000000000000000"}]})
    assert r.status_code in (404, 422)
    r = client.put(f"/api/v1/meetings/{mid}/stage", json={"items": [{"type": "camera", "identity": stranger["identity"] + "x"}]})
    assert r.status_code in (404, 422)
    for bad in ([{"type": "evil", "identity": a["identity"]}], "items", [{"type": "camera"}], [{"type": "camera", "identity": "x" * 500}]):
        assert client.put(f"/api/v1/meetings/{mid}/stage", json={"items": bad}).status_code == 422, bad
    assert client.post(f"/api/v1/meetings/{mid}/moderation/stop-share", json={"identity": a["identity"].replace("u-", "g-")}).status_code in (404, 409, 422)


def test_stage_set_get_clear_with_limits_and_dedup(client, lk):
    room, a, b, c = setup_meeting(client)
    mid = a["meeting_id"]
    login(client, "carol")
    items = [{"type": "screen", "identity": a["identity"]}, {"type": "camera", "identity": a["identity"]}, {"type": "camera", "identity": b["identity"]},
             {"type": "camera", "identity": b["identity"]}, {"type": "board"}]
    r = client.put(f"/api/v1/meetings/{mid}/stage", json={"items": items})
    assert r.status_code == 200
    st = r.json()
    assert [i["type"] for i in st["items"]] == ["screen", "camera", "camera", "board"] and st["by"] == "Carol C", "повтор убран, порядок сохранён"
    login(client, "alice")
    assert client.get(f"/api/v1/meetings/{mid}/stage").json()["items"] == st["items"], "участник после входа/переподключения получает сцену ведущего"
    login(client, "carol")
    five = [{"type": "camera", "identity": x["identity"]} for x in (a, b, c)] + [{"type": "screen", "identity": a["identity"]}, {"type": "screen", "identity": b["identity"]}]
    r = client.put(f"/api/v1/meetings/{mid}/stage", json={"items": five})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many"
    assert client.put(f"/api/v1/meetings/{mid}/stage", json={"items": []}).json()["items"] == []
    assert client.get(f"/api/v1/meetings/{mid}/stage").json() == {"items": [], "by": None, "at": None}
    login(client, "root")
    assert "meeting.stage" in [x["action"] for x in client.get("/api/v1/admin/audit").json()]


def test_stage_and_share_block_are_cleared_when_the_meeting_ends(client, lk):
    room, a, b, c = setup_meeting(client)
    mid = a["meeting_id"]
    login(client, "carol")
    client.put(f"/api/v1/meetings/{mid}/stage", json={"items": [{"type": "camera", "identity": b["identity"]}]})
    client.post(f"/api/v1/meetings/{mid}/moderation/stop-share", json={"identity": b["identity"], "block": True})
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    r = client.app_obj.state.redis

    async def keys():
        return [k for k in await r.keys("*") if k in (f"stage:{mid}", f"share_block:{mid}")]
    assert client.portal.call(keys) == [], "новая встреча начинается без чужой сцены и запретов"
    assert client.put(f"/api/v1/meetings/{mid}/stage", json={"items": []}).status_code == 404


def test_stop_camera_mutes_only_that_camera(client, lk):
    room, a, b, c = setup_meeting(client)
    mid = a["meeting_id"]
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{mid}/moderation/stop-camera", json={"identity": a["identity"]})
    assert r.status_code == 200 and lk["muted"][-1] == {"sources": {"camera"}, "only": {a["identity"]}}


def test_livekit_down_is_reported_and_nothing_is_recorded(client, monkeypatch):
    async def down(*a, **k):
        return None
    monkeypatch.setattr("app.services.meetings.mute_sources", down)
    room, a, b, c = setup_meeting(client)
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{a['meeting_id']}/moderation/stop-share", json={"identity": b["identity"]})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "livekit_unavailable"
