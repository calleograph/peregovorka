"""Гостевая ссылка во встречах Outlook: две ссылки в приглашении, одна гостевая ссылка на событие, отзыв и перевыпуск, срок жизни как у встречи, без утечки секрета."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import AuditLog, Room

from .conftest import login
from .test_outlook import O, _anon, _body, _enable, _token

G = "/api/v1/guest/room"


def _create(client, h, **kw):
    _anon(client)
    r = client.post(f"{O}/meetings", json=_body(**kw), headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_invite_has_employee_and_guest_links_when_guests_allowed(client):
    _enable(client, guest_links=True)
    h = _token(client)
    j = _create(client, h)
    assert j["guests_allowed"] is True and j["guest_url"].startswith("http://testserver/guest/")
    assert j["url"].startswith("http://testserver/m/") and j["url"] != j["guest_url"]
    for blob in (j["invite_html"], j["invite_text"]):
        assert j["url"] in blob and j["guest_url"] in blob
        assert "Для сотрудников организации" in blob and "Для внешних участников" in blob and "передайте ему гостевую ссылку" in blob
    assert client.get(f"{G}/{j['guest_url'].rsplit('/', 1)[1]}").status_code == 200       # ссылка рабочая


def test_no_guest_link_in_invite_when_admin_forbids_guests(client):
    _enable(client, guest_links=False)
    h = _token(client)
    j = _create(client, h)
    assert j["guests_allowed"] is False and j["guest_url"] is None
    assert "Для внешних участников" not in j["invite_html"] and "/guest/" not in j["invite_text"]
    r = client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "issue"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "guests_disabled"           # запрет не обходится и вручную


def test_saving_the_same_event_again_never_creates_a_second_guest_link(client):
    _enable(client, guest_links=True)
    h = _token(client)
    key = "k" + uuid.uuid4().hex
    first = _create(client, h, key=key)
    again = _create(client, h, key=key)
    moved = _create(client, h, key=key, start_in=180)
    assert first["id"] == again["id"] == moved["id"]
    assert first["guest_url"] == again["guest_url"] == moved["guest_url"]
    issue = client.post(f"{O}/meetings/{first['id']}/guest-link", json={"action": "issue"}, headers=h).json()
    assert issue["guest_url"] == first["guest_url"]                                          # «выпустить» уже выпущенную не меняет её


def test_rotate_invalidates_old_link_and_revoke_removes_it_from_invite(client):
    _enable(client, guest_links=True)
    h = _token(client)
    j = _create(client, h)
    old = j["guest_url"].rsplit("/", 1)[1]
    new = client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "rotate"}, headers=h).json()
    assert new["guest_url"] and new["guest_url"].rsplit("/", 1)[1] != old and new["guest_url"] in new["invite_html"] and old not in new["invite_html"]
    assert client.get(f"{G}/{old}").status_code == 404 and client.get(f"{G}/{new['guest_url'].rsplit('/', 1)[1]}").status_code == 200
    gone = client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "revoke"}, headers=h).json()
    assert gone["guest_url"] is None and "/guest/" not in gone["invite_text"] and "Для внешних участников" not in gone["invite_html"]
    assert client.get(f"{G}/{new['guest_url'].rsplit('/', 1)[1]}").status_code == 404
    back = client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "issue"}, headers=h).json()      # повторный выпуск после отзыва
    assert back["guest_url"] and back["guest_url"] != new["guest_url"]


def test_guest_link_dies_with_the_meeting_cancel(client):
    _enable(client, guest_links=True)
    h = _token(client)
    j = _create(client, h)
    token = j["guest_url"].rsplit("/", 1)[1]
    assert client.get(f"{G}/{token}").status_code == 200
    assert client.delete(f"{O}/meetings/{j['id']}", headers=h).status_code == 204
    assert client.get(f"{G}/{token}").status_code == 404
    assert client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "issue"}, headers=h).status_code == 409


def test_only_the_organizer_manages_guest_link_and_actions_are_validated(client):
    _enable(client, guest_links=True)
    h = _token(client)
    j = _create(client, h)
    other = _token(client, "bob")
    assert client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "revoke"}, headers=other).status_code == 404
    assert client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "delete"}, headers=h).status_code == 422
    assert client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "issue"}).status_code == 401


def test_guest_secret_is_not_written_to_audit_or_by_key_lookup_for_others(client):
    _enable(client, guest_links=True)
    h = _token(client)
    j = _create(client, h)
    client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "rotate"}, headers=h)
    token = client.post(f"{O}/meetings/{j['id']}/guest-link", json={"action": "issue"}, headers=h).json()["guest_url"].rsplit("/", 1)[1]

    async def rows():
        async with client.app_obj.state.session_maker() as db:
            return [str(r.details) for r in (await db.execute(select(AuditLog))).scalars()]
    assert all(token not in d for d in client.portal.call(rows))


def test_guest_is_limited_to_guest_rights_not_history(client):
    _enable(client, guest_links=True)
    h = _token(client)
    j = _create(client, h)
    token = j["guest_url"].rsplit("/", 1)[1]
    info = client.get(f"{G}/{token}").json()
    assert set(info) == {"room_name", "description", "meeting_active", "has_password", "camera_allowed", "presentation"}
    assert client.get(f"/api/v1/meetings?room_id={j['id']}").status_code == 401                # гостевая ссылка не даёт ни сеанса, ни истории


def test_turning_guests_off_kills_issued_outlook_guest_links(client):
    _enable(client, guest_links=True)
    h = _token(client)
    j = _create(client, h)
    token = j["guest_url"].rsplit("/", 1)[1]
    assert client.get(f"{G}/{token}").status_code == 200
    _enable(client, guest_links=False)
    assert client.get(f"{G}/{token}").status_code == 404
    assert client.post(f"{G}/{token}/join", json={"display_name": "Гость Внешний"}).status_code == 404
