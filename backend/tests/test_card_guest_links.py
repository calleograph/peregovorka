"""Кнопки копирования на карточках главной страницы: обычная ссылка и гостевая. Сервер сам решает, кому отдать гостевую ссылку и кто вправе её выпустить:
интерфейс только показывает то, что пришло. Ссылка выпускается один раз (повторное «выпустить» возвращает прежнюю), политика организации не обходится."""
from __future__ import annotations

from .conftest import login, make_room, put_settings
from .test_outlook import O, _anon, _body, _enable, _token
from .test_outlook_sync import LINK
from .test_upcoming import API, _item, _schedule

MANAGE = "/api/v1/rooms/{}/manage/guest-link/{}"


def _issue(client, user, rid, action="issue"):
    login(client, user)
    return client.post(MANAGE.format(rid, action))


# ------------------------------------------------------------------------------------------------ встреча из Outlook или календаря
def test_scheduled_meeting_gives_the_organizer_its_own_guest_link_when_guests_are_allowed(client):
    _enable(client, guest_links=True)
    h = _token(client, "alice")
    _anon(client)
    j = client.post(f"{O}/meetings", json={**_body(), **LINK}, headers=h).json()
    it = _item(client, "alice", j["id"])
    assert it["guest_token"] and j["guest_url"].endswith("/guest/" + it["guest_token"]), "гостевая ссылка именно этой встречи, а не комнаты или другого события"
    assert it["guest_issuable"] is False and it["url"] == "/m/" + j["url"].rsplit("/", 1)[1]
    other = client.post(f"{O}/meetings", json={**_body(key="other-" + "x" * 12), **LINK, "event_id": "OTHER", "ews_id": "OTHER"}, headers=h).json()
    assert _item(client, "alice", other["id"])["guest_token"] != it["guest_token"]


def test_no_guest_link_when_the_organization_forbids_guests_or_for_non_organizers(client):
    _enable(client, guest_links=False)
    j, *_ = _schedule(client)
    it = _item(client, "alice", j["id"])
    assert it["guest_token"] is None and it["guest_issuable"] is False, "политика запрещает гостей: красной кнопки нет даже у организатора"
    assert _issue(client, "alice", j["id"]).status_code == 403, "и сервер выпуск не разрешает"
    _enable(client, guest_links=True)
    j2, *_ = _schedule(client, attendees=[{"email": "bob@corp.test", "name": "Bob B"}], key="second-" + "k" * 10)
    login(client, "bob")
    assert _item(client, "bob", j2["id"])["guest_token"] is None, "приглашённый сотрудник гостевую ссылку не получает"
    assert _issue(client, "bob", j2["id"]).status_code in (403, 404), "и выпустить её не может"


def test_issue_creates_the_link_once_and_never_again(client):
    _enable(client, guest_links=True)
    j, *_ = _schedule(client)
    login(client, "alice")
    # организатор отозвал ссылку: кнопка остаётся (выпустить можно), токена нет
    assert client.post(MANAGE.format(j["id"], "revoke")).status_code == 200
    it = _item(client, "alice", j["id"])
    assert it["guest_token"] is None and it["guest_issuable"] is True
    first = _issue(client, "alice", j["id"])
    assert first.status_code == 200 and first.json()["guest_token"]
    again = _issue(client, "alice", j["id"])
    assert again.json()["guest_token"] == first.json()["guest_token"], "повторное нажатие не создаёт новую ссылку"
    it = _item(client, "alice", j["id"])
    assert it["guest_token"] == first.json()["guest_token"] and it["guest_issuable"] is False


def test_link_stays_the_same_after_the_calendar_meeting_is_moved(client):
    _enable(client, guest_links=True)
    h = _token(client, "alice")
    _anon(client)
    key = "moved" + "m" * 12
    j = client.post(f"{O}/meetings", json={**_body(key=key), **LINK}, headers=h).json()
    before = _item(client, "alice", j["id"])
    moved = client.post(f"{O}/meetings", json={**_body(key=key, start_in=240), **LINK}, headers=h).json()
    after = _item(client, "alice", j["id"])
    assert moved["id"] == j["id"] and after["start"] != before["start"]
    assert after["guest_token"] == before["guest_token"] and after["url"] == before["url"], "после переноса ссылки прежние"


# ------------------------------------------------------------------------------------------------ временные и постоянные комнаты
def test_temporary_room_issue_follows_the_temporary_room_policy(client):
    put_settings(client, "general", temp_room_allow_guest_link=False)
    login(client, "alice")
    room = client.post(f"{API}/rooms/temporary", json={"name": "Созвон"}).json()
    assert _item(client, "alice", room["id"])["guest_issuable"] is False
    assert _issue(client, "alice", room["id"]).status_code == 403
    put_settings(client, "general", temp_room_allow_guest_link=True)
    it = _item(client, "alice", room["id"])
    assert it["guest_issuable"] is True and it["guest_token"] is None
    tok = _issue(client, "alice", room["id"]).json()["guest_token"]
    assert tok and _issue(client, "alice", room["id"]).json()["guest_token"] == tok
    assert _issue(client, "bob", room["id"]).status_code in (403, 404), "чужую комнату выпустить нельзя"


def test_permanent_room_guest_token_is_given_only_to_managers_and_issue_never_enables_guests(client):
    login(client, "root")
    room = make_room(client, name="Совещания", guest_access_enabled=True)
    rid = room["id"]
    login(client, "root")
    mine = {r["id"]: r for r in client.get(f"{API}/rooms").json()}
    assert mine[rid]["guest_token"], "руководитель и администратор видят гостевую ссылку"
    login(client, "bob")
    listed = {r["id"]: r for r in client.get(f"{API}/rooms").json()}
    assert rid not in listed or listed[rid]["guest_token"] is None, "обычному сотруднику секрет гостевой ссылки не отдаётся"
    login(client, "root")
    off = make_room(client, name="Без гостей")
    assert _issue(client, "root", off["id"]).status_code == 403, "в постоянной комнате без гостевого входа «выпустить» его не включает"
