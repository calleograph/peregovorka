"""Дефекты безопасности из аудита: каждый тест падал на коде до исправления."""
from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from app.api import ws as ws_mod
from app.models import MeetingParticipant
from app.services import events
from app.services.events import EventHub
from .conftest import login, make_room
from .test_collab_guests import guest_join
from .test_live import access, get_room, lv, room_live, start, viewer  # noqa: F401
from .test_roles_floor import LEADERS, lk_calls  # noqa: F401
from .test_sessions import API, PC, PHONE, as_device, join, sign_in
from .test_transcripts import _join


def ended_broadcast(c, room):
    j = _join(c, "carol", room["id"])
    assert start(c, j["meeting_id"]).status_code == 200
    login(c, "carol")
    return j["meeting_id"]


def test_room_link_gives_no_signature_while_nothing_is_live(lv):  # noqa: F811
    """Прошлый эфир без пароля, в комнате включён пароль: подпись на «ожидании» открывала следующий защищённый эфир."""
    room = room_live(lv)
    mid = ended_broadcast(lv, room)
    rm = get_room(lv, room["id"])
    r = access(lv, rm.live_token)
    assert r.status_code == 200, "эфир идёт, пароля нет"
    assert start(lv, mid, enabled=False).status_code == 200
    login(lv, "carol")
    assert lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": "zritel-2026"}).status_code == 200
    lv.portal.call(lambda: _finish(lv, mid))
    rm = get_room(lv, room["id"])
    r = access(lv, rm.live_token, long=True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_live"
    assert viewer(lv, rm.live_token).json()["password_required"] is True, "страница заранее предупреждает о пароле самой комнаты"


async def _finish(c, mid):
    from sqlalchemy import update
    from app.models import Broadcast
    async with c.app_obj.state.session_maker() as db:
        await db.execute(update(Broadcast).values(status="ended"))
        await db.commit()


def test_each_broadcast_gets_its_own_view_access(lv):  # noqa: F811
    """Подпись живёт до 4 часов и привязана к идентификатору доступа: у нового эфира он другой."""
    room = room_live(lv)
    j = _join(lv, "carol", room["id"])
    before = get_room(lv, room["id"]).live_view_access
    assert start(lv, j["meeting_id"]).status_code == 200
    first = get_room(lv, room["id"]).live_view_access
    assert first != before
    assert not (lv.dir / "acc" / before).exists() if before else True
    lv.portal.call(lambda: _finish(lv, j["meeting_id"]))
    assert start(lv, j["meeting_id"]).status_code == 200
    assert get_room(lv, room["id"]).live_view_access != first


def test_segments_are_named_by_time_not_by_number():
    """Номера начинаются с нуля в каждом эфире: сегмент прошлого эфира совпадал по имени в кеше CDN и у постоянной ссылки."""
    import app.services.live as live_mod
    src = open(live_mod.__file__, encoding="utf-8").read()
    assert "filename_suffix=eg.SegmentedFileSuffix.TIMESTAMP" in src


# ------------------------------------------------------------------------------------------- WebSocket
def test_socket_with_regular_pings_is_closed_after_logout(client, monkeypatch):
    """Клиент пингует чаще срока проверки: при проверке «по тишине» она не наступала никогда."""
    monkeypatch.setattr(ws_mod, "SESSION_RECHECK_SECONDS", 0.3)
    login(client, "alice")
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        client.post(f"{API}/auth/logout")
        closed = False
        end = time.time() + 5
        while time.time() < end:
            time.sleep(0.1)
            try:
                ws.send_json({"type": "ping"})
                m = ws.receive_json()
                assert m["type"] == "pong"
            except Exception:  # noqa: BLE001 — сервер закрыл сокет
                closed = True
                break
        assert closed, "сокет вышедшего пользователя должен закрыться"


def test_socket_loses_the_meeting_when_access_is_revoked(client, monkeypatch, lk_calls):  # noqa: F811
    monkeypatch.setattr(ws_mod, "SESSION_RECHECK_SECONDS", 0.3)
    room = make_room(client, moderators=LEADERS)
    _join(client, "carol", room["id"])
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": mid})
        assert ws.receive_json()["type"] == "subscribed"

        async def mark():
            from sqlalchemy import update
            async with client.app_obj.state.session_maker() as db:
                await db.execute(update(MeetingParticipant).where(MeetingParticipant.user_id != None).values(removed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))  # noqa: E711
                await db.commit()
        client.portal.call(mark)
        end = time.time() + 5
        got = None
        while time.time() < end and got is None:
            ws.send_json({"type": "ping"})
            m = ws.receive_json()
            if m.get("type") == "error":
                got = m
        assert got and got["message"] == "forbidden" and got["meeting_id"] == mid


# ------------------------------------------------------------------------------------------- события
def hub_with(*subs):
    h = EventHub(None)
    ch = events.channel("m")
    h._subs[ch] = set(subs)
    return h, ch


def drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(json.loads(sub.queue.get_nowait()))
    return out


@pytest.mark.parametrize("kind", ["segment"])
def test_transcript_events_never_reach_guests(kind):
    member, guest = events.Subscriber(owner="u1"), events.Subscriber(owner="g1", guest=True)
    h, ch = hub_with(member, guest)
    h._deliver(ch, json.dumps({"type": kind, "members_only": True, "segment": {"text": "закрытый текст"}}))
    h._deliver(ch, json.dumps({"type": "chat_message"}))
    assert [e["type"] for e in drain(member)] == [kind, "chat_message"]
    assert [e["type"] for e in drain(guest)] == ["chat_message"]


def test_published_segment_is_marked_for_members_only():
    import inspect
    from app.services import segments
    assert '"members_only": True' in inspect.getsource(segments)


def test_removed_person_stops_receiving_events_on_every_socket():
    a1, a2, b = events.Subscriber(owner="u1"), events.Subscriber(owner="u1"), events.Subscriber(owner="u2")
    h, ch = hub_with(a1, a2, b)
    h._deliver(ch, json.dumps({"type": "participant_left", "user_id": "u1", "removed": True}))
    h._deliver(ch, json.dumps({"type": "chat_message"}))
    assert [e["type"] for e in drain(a1)] == ["participant_left"]
    assert [e["type"] for e in drain(a2)] == ["participant_left"]
    assert [e["type"] for e in drain(b)] == ["participant_left", "chat_message"]


# ------------------------------------------------------------------------------------------- удаление участника
def test_removal_closes_every_device_and_holds_until_the_meeting_ends(client, lk_calls):  # noqa: F811
    room = make_room(client, moderators=LEADERS)
    c = _join(client, "carol", room["id"])
    _, pc = sign_in(client, ua=PC)
    _, phone = sign_in(client, ua=PHONE)
    a = join(client, pc, room["id"], "tab-computer-0001", PC).json()
    b = join(client, phone, room["id"], "tab-phone-000002", PHONE).json()
    mid = a["meeting_id"]
    assert a["identity"] != b["identity"] and mid == c["meeting_id"]
    login(client, "carol")
    assert client.post(f"{API}/meetings/{mid}/moderation/kick", json={"identity": a["identity"]}).status_code == 200
    assert set(lk_calls["removed"]) == {a["identity"], b["identity"]}, "второе устройство оставалось в звонке"

    # блокировка в Redis живёт 10 минут: убираем её, право должно держаться на отметке в базе
    r = client.app_obj.state.redis
    from app.services.meetings import MeetingService
    uid = client.portal.call(lambda: _uid(client, "alice"))
    client.portal.call(lambda: r.delete(MeetingService._kick_key(room["id"], uid)))

    as_device(client, pc)
    csrf = client.get(f"{API}/auth/me").json()["csrf_token"]
    resp = client.post(f"{API}/rooms/{room['id']}/join", json={}, headers={"X-CSRF-Token": csrf, "X-Client-Id": "tab-computer-0001"})
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "removed_from_meeting"
    assert client.get(f"{API}/meetings/{mid}/transcript").status_code in (403, 404)
    assert client.get(f"{API}/meetings/{mid}/chat").status_code in (403, 404)
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": mid})
        assert ws.receive_json()["message"] == "forbidden"


async def _uid(c, sam):
    from app.models import User
    async with c.app_obj.state.session_maker() as db:
        return (await db.execute(select(User.id).where(User.sam_account_name == sam))).scalar_one()


def test_guest_is_not_subscribed_after_removal(client, lk_calls):  # noqa: F811
    room = make_room(client, guest_access_enabled=True, moderators=LEADERS)
    c = _join(client, "carol", room["id"])
    g = guest_join(client, room["guest_token"]).json()
    login(client, "carol")
    assert client.post(f"{API}/meetings/{c['meeting_id']}/moderation/kick", json={"identity": g["identity"]}).status_code == 200
    client.cookies.clear()
    assert client.get(f"{API}/meetings/{c['meeting_id']}/chat", headers={"X-Guest-Token": g["guest_token"]}).status_code == 401
