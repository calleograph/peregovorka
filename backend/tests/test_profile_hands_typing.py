"""Личный кабинет (профиль из AD, аватарка), карточка участника, «поднять руку» и «печатает…»."""
from __future__ import annotations

import io
from dataclasses import replace

from .conftest import OTHER_GROUP, login, make_room, put_settings
from .test_collab_guests import ghead, guest_join, guest_room
from .test_transcripts import _join

API = "/api/v1"
LEADERS = [{"subject_type": "group", "subject_ref": OTHER_GROUP, "display_name": "Руководители"}]


def set_ad(directory, name: str, **attrs):
    pw, ident = directory.users[name]
    directory.users[name] = (pw, replace(ident, **attrs))


def png(w: int = 300, h: int = 200, color=(200, 40, 40)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "PNG")
    return out.getvalue()


# ------------------------------------------------------------------------------------------------ профиль
def test_profile_comes_from_ad_on_login_and_refresh_reads_new_values(client, directory):
    set_ad(directory, "alice", title="Ведущий инженер", department="Отдел эксплуатации", phone="+7000 111-22-33")
    login(client, "alice")
    p = client.get(f"{API}/profile").json()
    assert p["display_name"] == "Alice A" and p["login"] == "alice" and p["title"] == "Ведущий инженер" and p["department"] == "Отдел эксплуатации"
    assert p["phone"] == "+7000 111-22-33" and p["email"] == "alice@corp.test" and p["source"] == "ad" and p["synced_at"] and p["avatar_url"] is None
    set_ad(directory, "alice", title="Руководитель группы", department="Эксплуатация", phone=None)
    r = client.post(f"{API}/profile/refresh")
    assert r.status_code == 200 and r.json()["title"] == "Руководитель группы" and r.json()["phone"] is None
    assert "profile.refresh" in {a["action"] for a in (login(client, "root") and client.get(f"{API}/admin/audit").json())}
    login(client, "alice")
    directory.users.pop("alice")
    assert client.post(f"{API}/profile/refresh").status_code == 404
    client.cookies.clear()
    assert client.get(f"{API}/profile").status_code == 401


def test_only_whitelisted_ad_attributes_are_requested():
    from app.auth.directory import PROFILE_ATTRS

    assert set(PROFILE_ATTRS) == {"title", "department", "telephoneNumber"}


def test_avatar_is_validated_squared_stored_locally_and_removable(client):
    login(client, "alice")
    big = client.put(f"{API}/profile/avatar", content=png(300, 200), headers={"content-type": "image/png"})
    assert big.status_code == 200, big.text
    url = big.json()["avatar_url"]
    assert url and url.startswith(f"{API}/users/")
    img = client.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp" and "immutable" in img.headers["cache-control"] and img.content[:4] == b"RIFF"
    from PIL import Image

    assert Image.open(io.BytesIO(img.content)).size == (256, 256), "картинка приведена к квадрату 256×256"
    bad = client.put(f"{API}/profile/avatar", content=b"GIF89a....not-allowed", headers={"content-type": "image/gif"})
    assert bad.status_code == 422 and "JPEG" in bad.json()["detail"]
    assert client.put(f"{API}/profile/avatar", content=b"\x89PNG\r\n\x1a\n" + b"junk" * 50).status_code == 422, "подделанный заголовок — не картинка"
    assert client.put(f"{API}/profile/avatar", content=png() + b"0" * 700_000).status_code in (413, 422)
    login(client, "bob")
    assert client.get(url).status_code == 200, "аватарку видят вошедшие сотрудники"
    client.cookies.clear()
    assert client.get(url).status_code == 401
    login(client, "alice")
    assert client.delete(f"{API}/profile/avatar").json()["avatar_url"] is None and client.get(url).status_code == 404


def test_local_admin_cannot_refresh_from_ad(client):
    from .test_local_admin_ldap import make_local_admin

    make_local_admin(client, must_change=False)
    client.cookies.clear()
    assert client.post(f"{API}/auth/login", json={"login": "admin", "password": "Qm4-Rt8w-Yn2k-Bd6x"}).status_code == 200
    client.headers["X-CSRF-Token"] = client.get(f"{API}/auth/me").json()["csrf_token"]
    r = client.post(f"{API}/profile/refresh")
    assert r.status_code == 409 and client.get(f"{API}/profile").json()["source"] == "local"


# ------------------------------------------------------------------------------------------------ карточка участника
def test_participant_card_shows_only_whitelisted_fields_and_only_for_participants(client, directory):
    set_ad(directory, "alice", title="Инженер", department="ИТ", phone="+7000 000-00-01")
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    b = _join(client, "bob", room["id"])
    mid = a["meeting_id"]
    login(client, "alice")
    client.put(f"{API}/profile/avatar", content=png(), headers={"content-type": "image/png"})
    login(client, "bob")
    card = client.get(f"{API}/meetings/{mid}/participants/{a['identity']}/card").json()
    assert card["card"] == "full" and card["name"] == "Alice A" and card["title"] == "Инженер" and card["department"] == "ИТ"
    assert card["email"] == "alice@corp.test" and card["phone"] is None and card["avatar_url"] and card["login"] is None
    assert set(card) == {"identity", "name", "guest", "card", "title", "department", "avatar_url", "email", "phone", "login"}
    put_settings(client, "general", card_show_email=False, card_show_phone=True)
    login(client, "bob")
    card = client.get(f"{API}/meetings/{mid}/participants/{a['identity']}/card").json()
    assert card["email"] is None and card["phone"] == "+7000 000-00-01"
    carol = _join(client, "carol", room["id"]) if False else None
    assert client.get(f"{API}/meetings/{mid}/participants/u-{'0' * 32}/card").status_code == 404, "человека не из этой встречи не показываем"
    assert client.get(f"{API}/meetings/{mid}/participants/{b['identity']}/card").status_code == 200 and carol is None


def test_guest_gets_minimal_cards_and_guest_card_is_minimal(client, directory):
    room = guest_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    g = guest_join(client, room["guest_token"], "Иван Иванов").json()
    h = ghead(g)
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    card = client.get(f"{API}/meetings/{mid}/participants/{a['identity']}/card", headers=h).json()
    assert card == {"identity": a["identity"], "name": "Alice A", "guest": False, "card": "minimal"}
    own = client.get(f"{API}/meetings/{mid}/participants/{g['identity']}/card", headers=h).json()
    assert own["guest"] is True and own["card"] == "minimal" and own["name"] == "Иван Иванов"
    login(client, "alice")
    assert client.get(f"{API}/meetings/{mid}/participants/{g['identity']}/card").json()["guest"] is True


# ------------------------------------------------------------------------------------------------ «поднять руку»
def test_raise_hand_queue_order_repeat_lower_and_leader_lowering(client):
    room = make_room(client, moderators=LEADERS)
    a = _join(client, "alice", room["id"])
    b = _join(client, "bob", room["id"])
    c = _join(client, "carol", room["id"])
    mid = a["meeting_id"]
    login(client, "alice")
    r = client.post(f"{API}/meetings/{mid}/hand", json={"raised": True}).json()
    assert [h["identity"] for h in r["queue"]] == [a["identity"]]
    login(client, "bob")
    client.post(f"{API}/meetings/{mid}/hand", json={"raised": True})
    login(client, "alice")
    again = client.post(f"{API}/meetings/{mid}/hand", json={"raised": True}).json()
    assert [h["identity"] for h in again["queue"]] == [a["identity"], b["identity"]], "повторное нажатие очередь не сдвигает"
    login(client, "bob")
    assert client.post(f"{API}/meetings/{mid}/hand", json={"raised": False, "identity": a["identity"]}).status_code == 403, "чужую руку снимает только руководитель"
    login(client, "carol")
    assert client.get(f"{API}/meetings/{mid}/hands").json()["hands"][0]["name"] == "Alice A"
    low = client.post(f"{API}/meetings/{mid}/hand", json={"raised": False, "identity": a["identity"]}).json()
    assert [h["identity"] for h in low["queue"]] == [b["identity"]]
    login(client, "bob")
    assert client.post(f"{API}/meetings/{mid}/hand", json={"raised": False}).json()["queue"] == []
    assert client.post(f"{API}/meetings/{mid}/hand", json={"raised": "yes"}).status_code == 422
    assert c["identity"]
    login(client, "alice")
    client.post(f"{API}/meetings/{mid}/hand", json={"raised": True})
    login(client, "carol")                                   # завершить встречу для всех может руководитель комнаты (раньше — любой участник)
    assert client.post(f"{API}/meetings/{mid}/end").status_code == 204
    assert client.post(f"{API}/meetings/{mid}/hand", json={"raised": False}).status_code in (403, 409)


def test_hand_and_typing_events_reach_other_participants_but_are_not_stored(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    _join(client, "bob", room["id"])
    mid = a["meeting_id"]
    login(client, "bob")
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": mid})
        assert ws.receive_json()["type"] == "subscribed"
        login(client, "alice")
        assert client.post(f"{API}/meetings/{mid}/chat/typing", json={"typing": True}).status_code == 204
        ev = ws.receive_json()
        assert ev == {"type": "chat_typing", "id": ev["id"], "name": "Alice A", "typing": True}
        client.post(f"{API}/meetings/{mid}/hand", json={"raised": True})
        ev = ws.receive_json()
        assert ev["type"] == "hand_changed" and ev["identity"] == a["identity"] and ev["raised"] is True and ev["queue"][0]["name"] == "Alice A"
    assert client.get(f"{API}/meetings/{mid}/chat").json()["messages"] == [], "«печатает» в историю чата не попадает"


def test_typing_is_rate_limited_and_outsiders_cannot_send(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    codes = [client.post(f"{API}/meetings/{mid}/chat/typing", json={"typing": True}).status_code for _ in range(18)]
    assert codes[0] == 204 and 429 in codes
    login(client, "carol")
    assert client.post(f"{API}/meetings/{mid}/chat/typing", json={"typing": True}).status_code in (403, 404)


def test_guest_link_secret_goes_only_to_leaders_and_admins_in_the_room_list(client):
    with_guests = make_room(client, moderators=LEADERS, guest_access_enabled=True, name="С гостями")
    make_room(client, moderators=LEADERS, name="Без гостей")
    login(client, "alice")
    rows = {r["name"]: r for r in client.get(f"{API}/rooms").json()}
    assert rows["С гостями"]["guest_token"] is None and rows["С гостями"]["can_manage"] is False, "обычный участник секрет гостевой ссылки не получает"
    login(client, "carol")
    rows = {r["name"]: r for r in client.get(f"{API}/rooms").json()}
    assert rows["С гостями"]["can_manage"] is True and rows["С гостями"]["guest_token"] == with_guests["guest_token"]
    assert rows["Без гостей"]["can_manage"] is True and rows["Без гостей"]["guest_token"] is None, "гостевой вход выключен — копировать нечего"
    login(client, "root")
    assert {r["name"]: r["guest_token"] for r in client.get(f"{API}/rooms").json()}["С гостями"] == with_guests["guest_token"]
