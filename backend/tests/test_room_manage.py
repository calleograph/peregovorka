"""Самостоятельное управление комнатой руководителем: «Настройки комнаты» без доступа в системную админку."""
from __future__ import annotations

from .conftest import OTHER_GROUP, login, make_room

LEADERS = [{"subject_type": "group", "subject_ref": OTHER_GROUP, "display_name": "Руководители"}]  # carol — руководитель; alice, bob — участники


def url(room, tail=""):
    return f"/api/v1/rooms/{room['id']}/manage{tail}"


def test_leader_reads_and_changes_own_room_settings(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    got = client.get(url(room))
    assert got.status_code == 200 and got.json()["room_type"] == "regular" and got.json()["can_edit_system_fields"] is False
    r = client.patch(url(room), json={"name": "Кадры", "description": "Отдел кадров", "room_type": "presentation", "auto_record": True,
                                      "board_allowed": False, "camera_allowed": False})
    assert r.status_code == 200
    body = r.json()
    assert (body["name"], body["room_type"], body["auto_record"], body["board_allowed"], body["camera_allowed"]) == ("Кадры", "presentation", True, False, False)
    assert body["record_audio"] is True                       # автозапись требует разрешённой записи аудио
    assert body["needs_rejoin"] is False                      # встречи ещё нет
    # изменения постоянны
    again = client.get(url(room)).json()
    assert again["room_type"] == "presentation" and again["description"] == "Отдел кадров"


def test_leader_manages_leaders_access_password_and_guest_link(client, directory):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    bob = {"subject_type": "user", "subject_ref": directory.users["bob"][1].ad_guid, "display_name": "Bob"}
    r = client.patch(url(room), json={"moderators": LEADERS + [bob], "acl": [bob], "password": "s3cret-pass"})
    assert r.status_code == 200
    body = r.json()
    assert {m["subject_ref"] for m in body["moderators"]} == {OTHER_GROUP, bob["subject_ref"]} and [a["subject_ref"] for a in body["acl"]] == [bob["subject_ref"]]
    assert body["has_password"] is True and "password" not in body
    # новый руководитель действительно получает управление
    login(client, "bob")
    assert client.get(url(room)).status_code == 200
    # гостевая ссылка
    login(client, "carol")
    on = client.patch(url(room), json={"guest_access_enabled": True}).json()
    token = on["guest_token"]
    assert on["guest_access_enabled"] and token
    rotated = client.post(url(room, "/guest-link/rotate")).json()
    assert rotated["guest_token"] and rotated["guest_token"] != token
    revoked = client.post(url(room, "/guest-link/revoke")).json()
    assert revoked["guest_token"] is None and revoked["guest_access_enabled"] is False
    assert client.post(url(room, "/guest-link/boom")).status_code == 404


def test_leader_cannot_remove_all_leaders_but_admin_can(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    assert client.patch(url(room), json={"moderators": []}).status_code == 422
    login(client, "root")
    assert client.patch(url(room), json={"moderators": []}).status_code == 200


def test_participant_and_foreign_leader_have_no_access(client, directory):
    room = make_room(client, moderators=LEADERS)
    other = make_room(client, name="Чужая", moderators=[{"subject_type": "user", "subject_ref": directory.users["bob"][1].ad_guid, "display_name": "Bob"}])
    login(client, "alice")                                              # участник: видит комнату, но не управляет
    assert client.get(url(room)).status_code in (403, 404)
    assert client.patch(url(room), json={"name": "X"}).status_code in (403, 404)
    login(client, "carol")                                              # руководитель одной комнаты не управляет другой
    assert client.get(url(other)).status_code in (403, 404)
    assert client.patch(url(other), json={"name": "X"}).status_code in (403, 404)
    assert client.post(url(other, "/guest-link/rotate")).status_code in (403, 404)


def test_leader_cannot_touch_system_fields(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    client.patch(url(room), json={"is_enabled": False, "slug": "hacked", "llm_enabled": False, "retention_days": 1})
    got = client.get(url(room)).json()
    assert got["is_enabled"] is True and got["slug"] == room["slug"]
    assert client.patch(url(room), json={"room_type": "weird"}).status_code == 422


def test_leader_change_is_audited(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    client.patch(url(room), json={"room_type": "presentation"})
    login(client, "root")
    rows = client.get("/api/v1/admin/audit").json()
    items = rows["items"] if isinstance(rows, dict) else rows
    assert any(i["action"] == "room.manage.update" for i in items)


def test_directory_search_is_for_leaders_only(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "alice")
    assert client.get(url(room, "/directory"), params={"kind": "user", "q": "bo"}).status_code in (403, 404)
    login(client, "carol")
    r = client.get(url(room, "/directory"), params={"kind": "user", "q": "bo"})
    assert r.status_code == 200 and isinstance(r.json(), list)
    assert client.get(url(room, "/directory"), params={"kind": "oops", "q": "bo"}).status_code == 422
