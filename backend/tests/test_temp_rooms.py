"""Человекочитаемые адреса комнат и временные переговорки."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.models import Meeting, Room, utcnow

from .conftest import login, make_room, put_settings
from .test_transcripts import _feed_and_consume, _join, _segment

API = "/api/v1"


def create_temp(client, user: str = "alice", name: str = "Планёрка"):
    login(client, user)
    return client.post(f"{API}/rooms/temporary", json={"name": name})


def room_row(client, room_id: str) -> dict:
    async def go():
        async with client.app_obj.state.session_maker() as db:
            r = await db.get(Room, uuid.UUID(room_id))
            return {"lifecycle": r.lifecycle, "closed_at": r.closed_at, "auto_close_at": r.auto_close_at, "lifetime": r.lifetime, "created_by": r.created_by_user_id}
    return client.portal.call(go)


def reconcile(client, meeting_id: str, *, empty_minutes: float | None = None):
    async def go():
        async with client.app_obj.state.session_maker() as db:
            m = await db.get(Meeting, uuid.UUID(meeting_id))
            if empty_minutes is not None:
                m.empty_since = utcnow() - timedelta(minutes=empty_minutes)
                await db.commit()
            await client.app_obj.state.meetings.reconcile(db, m)
            await db.refresh(m)
            return m.ended_at, m.end_reason
    return client.portal.call(go)


# ------------------------------------------------------------------------------------------------ адреса
def test_room_url_uses_the_technical_id_and_old_uuid_links_resolve(client):
    room = make_room(client, slug="it-1", name="ИТ")
    login(client, "alice")
    by_slug = client.get(f"{API}/rooms/resolve/it-1").json()
    assert by_slug["id"] == room["id"] and by_slug["slug"] == "it-1" and by_slug["canonical"] is True
    assert client.get(f"{API}/rooms/resolve/IT-1").json()["id"] == room["id"], "регистр не важен"
    old = client.get(f"{API}/rooms/resolve/{room['id']}").json()
    assert old["slug"] == "it-1" and old["canonical"] is False, "старая ссылка с UUID переадресуется на адрес"
    assert client.get(f"{API}/rooms/resolve/net-takoy").status_code == 404
    login(client, "carol")      # комната недоступна: неотличима от несуществующей
    assert client.get(f"{API}/rooms/resolve/it-1").status_code == 404


def test_slug_rules_normalization_and_uniqueness(client):
    login(client, "root")
    r = client.post(f"{API}/admin/rooms", json={"slug": "  Dev_Team-2 ", "name": "A"})
    assert r.status_code == 201 and r.json()["slug"] == "dev_team-2"
    assert client.post(f"{API}/admin/rooms", json={"slug": "DEV_team-2", "name": "B"}).status_code == 409
    for bad in ("a", "-x", "кириллица", "with space", "dot.dot"):
        assert client.post(f"{API}/admin/rooms", json={"slug": bad, "name": "x"}).status_code == 422, bad


def test_changing_the_slug_keeps_the_old_address_as_redirect_and_blocks_its_reuse(client):
    room = make_room(client, slug="it-1", name="ИТ")
    login(client, "root")
    guest_token = client.patch(f"{API}/admin/rooms/{room['id']}", json={"guest_access_enabled": True}).json()["guest_token"]
    r = client.patch(f"{API}/admin/rooms/{room['id']}", json={"slug": "It-Support"})
    assert r.status_code == 200 and r.json()["slug"] == "it-support" and r.json()["slug_history"] == ["it-1"]
    assert r.json()["guest_token"] == guest_token, "гостевая ссылка не зависит от адреса"
    login(client, "alice")
    res = client.get(f"{API}/rooms/resolve/it-1").json()
    assert res["slug"] == "it-support" and res["canonical"] is False
    login(client, "root")
    assert client.post(f"{API}/admin/rooms", json={"slug": "it-1", "name": "Другая"}).status_code == 409, "прежний адрес занят переадресацией"
    other = client.post(f"{API}/admin/rooms", json={"slug": "ops", "name": "Ops"}).json()
    assert client.patch(f"{API}/admin/rooms/{other['id']}", json={"slug": "it-support"}).status_code == 409
    assert client.patch(f"{API}/admin/rooms/{other['id']}", json={"slug": "it-1"}).status_code == 409
    audit = client.get(f"{API}/admin/audit", params={"action": "room.update"}).json()
    assert any(a["details"].get("slug") == {"from": "it-1", "to": "it-support"} for a in audit)


# ------------------------------------------------------------------------------------- создание и доступ
def test_authorized_user_creates_a_temporary_room_and_becomes_its_leader(client):
    r = create_temp(client)
    assert r.status_code == 201, r.text
    room = r.json()
    assert room["lifetime"] == "temporary" and room["lifecycle"] == "active" and room["slug"].startswith("tmp-") and len(room["slug"]) == 10 and room["name"] == "Планёрка"
    mg = client.get(f"{API}/rooms/{room['id']}/manage")
    assert mg.status_code == 200, "создатель — руководитель"
    assert [x["id"] for x in client.get(f"{API}/rooms").json()] == [room["id"]]
    assert client.get(f"{API}/rooms/resolve/{room['slug']}").json()["id"] == room["id"]
    login(client, "bob")
    assert client.get(f"{API}/rooms").json() == [] and client.get(f"{API}/rooms/resolve/{room['slug']}").status_code == 404, "чужие пользователи комнату не видят"
    assert room_row(client, room["id"])["lifetime"] == "temporary"


def test_codes_are_random_and_not_sequential(client):
    put_settings(client, "general", temp_room_max_per_user=50, temp_room_max_total=100)
    codes = [create_temp(client, "alice", f"T{i}").json()["slug"] for i in range(6)]
    assert len(set(codes)) == 6 and all(len(c) == 10 and c[4:].isalnum() for c in codes)
    nums = [c[4:] for c in codes]
    assert not all(n.isdigit() for n in nums)


def test_guest_and_anonymous_cannot_create_temporary_rooms_or_see_the_list(client):
    room = make_room(client, guest_access_enabled=True)
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert client.post(f"{API}/rooms/temporary", json={"name": "x"}).status_code == 401
    assert client.get(f"{API}/rooms").status_code == 401
    # гость, вошедший по ссылке в идущую встречу, получает только гостевой токен — он не заменяет сессию пользователя
    login(client, "alice")
    _join(client, "alice", room["id"])
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    g = client.post(f"{API}/guest/room/{room['guest_token']}/join", json={"display_name": "Иван Иванов"})
    assert g.status_code == 200, g.text
    h = {"X-Guest-Token": g.json()["guest_token"]}
    assert client.post(f"{API}/rooms/temporary", json={"name": "x"}, headers=h).status_code == 401
    assert client.get(f"{API}/rooms", headers=h).status_code == 401


def test_limits_per_user_total_and_disabled(client):
    assert create_temp(client, "alice", "A1").status_code == 201
    assert create_temp(client, "alice", "A2").status_code == 201
    r = create_temp(client, "alice", "A3")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "limit_user"
    pol = client.get(f"{API}/rooms/temporary/policy").json()
    assert pol["can_create"] is False and pol["active_mine"] == 2 and pol["max_per_user"] == 2
    put_settings(client, "general", temp_room_max_total=3)
    assert create_temp(client, "bob", "B1").status_code == 201
    r = create_temp(client, "bob", "B2")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "limit_total"
    put_settings(client, "general", temp_rooms_enabled=False)
    r = create_temp(client, "bob", "B3")
    assert r.status_code == 403 and client.get(f"{API}/rooms/temporary/policy").json()["can_create"] is False
    put_settings(client, "general", temp_rooms_enabled=True)
    assert create_temp(client, "alice", "").status_code == 422


def test_guest_link_for_temporary_room_only_when_policy_allows(client):
    room = create_temp(client).json()
    r = client.patch(f"{API}/rooms/{room['id']}/manage", json={"guest_access_enabled": True})
    assert r.status_code == 403
    assert client.post(f"{API}/rooms/{room['id']}/manage/guest-link/rotate").status_code == 403
    put_settings(client, "general", temp_room_allow_guest_link=True)
    login(client, "alice")
    r = client.patch(f"{API}/rooms/{room['id']}/manage", json={"guest_access_enabled": True})
    assert r.status_code == 200 and r.json()["guest_token"], r.text


# ------------------------------------------------------------------------------------- жизненный цикл
def test_everyone_left_waits_for_grace_period_and_closes_after_it(client):
    room = create_temp(client).json()
    a = _join(client, "alice", room["id"])
    assert room_row(client, room["id"])["lifecycle"] == "active"
    assert client.post(f"{API}/meetings/{a['meeting_id']}/leave").status_code == 204
    st = room_row(client, room["id"])
    assert st["lifecycle"] == "grace_period" and st["auto_close_at"] is not None
    assert reconcile(client, a["meeting_id"], empty_minutes=2)[0] is None, "за 2 минуты из 5 комната ещё жива"
    assert room_row(client, room["id"])["lifecycle"] == "grace_period"
    ended, reason = reconcile(client, a["meeting_id"], empty_minutes=6)
    assert ended is not None and reason == "empty"
    st = room_row(client, room["id"])
    assert st["lifecycle"] == "closed" and st["closed_at"] is not None and st["auto_close_at"] is None


def test_return_within_grace_cancels_the_closing(client):
    room = create_temp(client).json()
    a = _join(client, "alice", room["id"])
    client.post(f"{API}/meetings/{a['meeting_id']}/leave")
    assert room_row(client, room["id"])["lifecycle"] == "grace_period"
    b = _join(client, "alice", room["id"])
    assert b["meeting_id"] == a["meeting_id"], "возврат — в ту же встречу"
    st = room_row(client, room["id"])
    assert st["lifecycle"] == "active"
    assert reconcile(client, a["meeting_id"])[0] is None


def test_owner_ends_the_meeting_for_everyone_and_the_room_disappears_from_the_list(client):
    room = create_temp(client).json()
    a = _join(client, "alice", room["id"])
    _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], "Решили выпускать в четверг")])
    assert client.post(f"{API}/meetings/{a['meeting_id']}/end").status_code == 204
    st = room_row(client, room["id"])
    assert st["lifecycle"] == "closed" and st["closed_at"] is not None
    assert client.get(f"{API}/rooms").json() == [], "из списка доступных исчезла"
    r = client.post(f"{API}/rooms/{room['id']}/join", json={})
    assert r.status_code == 410 and r.json()["detail"]["code"] == "room_closed", "по старому адресу новую встречу не начать"
    assert client.get(f"{API}/rooms/resolve/{room['slug']}").json()["lifecycle"] == "closed"


def test_materials_of_a_closed_temporary_room_stay_in_history_with_normal_access_rules(client):
    room = create_temp(client, name="Совещание по релизу").json()
    a = _join(client, "alice", room["id"])
    _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], "Принято решение: релиз в четверг")])
    login(client, "alice")
    assert client.post(f"{API}/meetings/{a['meeting_id']}/end").status_code == 204
    client.post(f"{API}/meetings/{a['meeting_id']}/release")      # ушла со страницы встречи: дальше — только по правилам истории
    row = room_row(client, room["id"])
    assert row["lifecycle"] == "closed" and row["created_by"] is not None, "строка комнаты не удалена, создатель известен"
    hist = client.get(f"{API}/meetings").json()
    mine = [m for m in hist if m["id"] == a["meeting_id"]]
    assert mine and mine[0]["room_name"] == "Совещание по релизу", "встреча в Истории"
    tr = client.get(f"{API}/meetings/{a['meeting_id']}/transcript")
    assert tr.status_code == 200 and "релиз в четверг" in tr.text, "участник читает стенограмму"
    login(client, "bob")
    assert client.get(f"{API}/meetings/{a['meeting_id']}/transcript").status_code in (403, 404), "посторонний — нет"
    login(client, "root")
    assert client.get(f"{API}/meetings/{a['meeting_id']}/transcript").status_code == 200
    ids = [r["id"] for r in client.get(f"{API}/admin/rooms").json()]
    assert room["id"] not in ids and room["id"] in [r["id"] for r in client.get(f"{API}/admin/rooms", params={"include_closed": True}).json()]
    closed = [r for r in client.get(f"{API}/admin/rooms", params={"include_closed": True}).json() if r["id"] == room["id"]][0]
    assert closed["lifetime"] == "temporary" and closed["lifecycle"] == "closed" and closed["closed_at"] and closed["created_by_name"]


def test_hung_rooms_are_cleaned_up_by_ttl_and_idle_rules(client):
    never = create_temp(client, "alice", "Никто не вошёл").json()
    hung = create_temp(client, "bob", "Зависла").json()
    b = _join(client, "bob", hung["id"])

    async def age():
        async with client.app_obj.state.session_maker() as db:
            r = await db.get(Room, uuid.UUID(never["id"]))
            r.created_at = utcnow() - timedelta(minutes=45)
            m = await db.get(Meeting, uuid.UUID(b["meeting_id"]))
            m.started_at = utcnow() - timedelta(hours=13)
            await db.commit()
            return await client.app_obj.state.meetings.cleanup_temporary(db)
    assert client.portal.call(age) == 2
    assert room_row(client, never["id"])["lifecycle"] == "closed" and room_row(client, hung["id"])["lifecycle"] == "closed"
    login(client, "root")
    assert client.get(f"{API}/admin/rooms").json() == []


def test_permanent_rooms_are_never_closed_by_the_temporary_lifecycle(client):
    room = make_room(client, slug="perm-1")
    a = _join(client, "alice", room["id"])
    client.post(f"{API}/meetings/{a['meeting_id']}/leave")
    reconcile(client, a["meeting_id"], empty_minutes=60)
    assert room_row(client, room["id"])["lifecycle"] == "active"
    assert client.get(f"{API}/rooms").json()[0]["lifetime"] == "permanent"
