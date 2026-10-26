"""Календарь Outlook: карточка события, изменение и отмена любых событий своего календаря (в том числе «только это занятие» / «вся серия»), несколько календарей.
Graph — подставной транспорт (tests/fake_graph.py); на настоящем Exchange не проверялось."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx

from app.models import Room, utcnow
from sqlalchemy import select

from .conftest import login
from .fake_graph import FakeGraph
from .test_calendar import C, _cfg, _connect, _ev, _graph, _window, _slot
from .test_outlook import iso


def _att(email, name="", kind="required", resp="none"):
    return {"emailAddress": {"address": email, "name": name}, "type": kind, "status": {"response": resp}}


def _setup(client):
    _cfg(client)
    g = _graph(client)
    _connect(client)
    return g


def _open(client, eid):
    r = client.post(f"{C}/event/open", json={"id": eid})
    assert r.status_code == 200, r.text
    return r.json()


def _calls(g: FakeGraph, method: str):
    return [p for m, p in g.calls if m == method]


# ------------------------------------------------------------------------------------------------ карточка
def test_card_shows_organizer_attendees_place_description_and_edit_right(client):
    g = _setup(client)
    eid = g.add_event("alice@corp.test", **_ev(subject="Планёрка", location={"displayName": "Переговорная 3"}, attendees=[_att("bob@corp.test", "Bob B", resp="accepted"), _att("ext@example.org", "", "optional")],
                                                body={"contentType": "html", "content": "<p>Повестка:<br>1. Бюджет</p><script>alert(1)</script>"}, showAs="tentative"))
    c = _open(client, eid)
    assert c["title"] == "Планёрка" and c["location"] == "Переговорная 3" and c["organizer_is_me"] is True and c["can_edit"] is True and c["show_as"] == "tentative"
    assert [(a["email"], a["type"], a["response"]) for a in c["attendees"]] == [("bob@corp.test", "required", "accepted"), ("ext@example.org", "optional", "none")]
    assert "Бюджет" in c["description"] and "<" not in c["description"] and "alert" not in c["description"], "описание — только текст"


def test_card_of_an_invitation_is_read_only_and_exchange_outage_is_not_shown_as_success(client):
    g = _setup(client)
    eid = g.add_event("alice@corp.test", **_ev(subject="Чужая", organizer={"emailAddress": {"address": "boss@corp.test"}}, attendees=[_att("alice@corp.test")]))
    assert _open(client, eid)["can_edit"] is False
    g.down = True
    r = client.post(f"{C}/event/open", json={"id": eid})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "exchange"
    assert client.post(f"{C}/event/open", json={}).status_code == 422


# ------------------------------------------------------------------------------------------------ изменение
def test_edit_single_event_sends_changes_with_etag_and_conflict_is_reported(client):
    g = _setup(client)
    eid = g.add_event("alice@corp.test", **_ev(subject="Старое", attendees=[_att("bob@corp.test")]))
    s, e, _ = _slot(180)
    etag = _open(client, eid)["etag"]
    r = client.post(f"{C}/event/update", json={"id": eid, "subject": "Новое", "location": "Каб. 5", "start": s, "end": e, "etag": etag, "tz": "Europe/Moscow",
                                                "attendees": [{"email": "carol@corp.test", "name": "Carol"}]})
    assert r.status_code == 200, r.text
    ev = g.events[eid]
    assert ev["subject"] == "Новое" and ev["location"] == {"displayName": "Каб. 5"} and [a["emailAddress"]["address"] for a in ev["attendees"]] == ["carol@corp.test"]
    g.events[eid]["@odata.etag"] = 'W/"other"'                                   # событие изменили в Outlook
    r2 = client.post(f"{C}/event/update", json={"id": eid, "subject": "Ещё", "etag": etag})
    assert r2.status_code == 200, "etag берётся с сервера заново (по самому событию), а не из браузера: чужая правка между чтением и записью — отдельный случай Exchange (If-Match, 412)"


def test_attendee_cannot_edit_or_cancel_and_nothing_is_sent(client):
    g = _setup(client)
    eid = g.add_event("alice@corp.test", **_ev(subject="Чужая", organizer={"emailAddress": {"address": "boss@corp.test"}}, attendees=[_att("alice@corp.test")]))
    n = len(g.calls)
    for path, body in (("update", {"id": eid, "subject": "Взлом"}), ("cancel", {"id": eid})):
        r = client.post(f"{C}/event/{path}", json=body)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "not_organizer"
    assert not _calls(g, "PATCH") and len(_calls(g, "POST")) == 0 and len(g.calls) > n, "после чтения карточки запись не отправлялась"
    assert g.events[eid]["subject"] == "Чужая" and not g.events[eid]["isCancelled"]


def test_edit_is_refused_for_past_cancelled_and_when_writing_is_off(client):
    g = _setup(client)
    past = g.add_event("alice@corp.test", **_ev(-300, subject="Прошла"))
    done = g.add_event("alice@corp.test", **_ev(60, subject="Отменена", isCancelled=True))
    for eid, code in ((past, "past"), (done, "cancelled")):
        r = client.post(f"{C}/event/update", json={"id": eid, "subject": "Х"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == code
    live = g.add_event("alice@corp.test", **_ev(90, subject="Живая"))
    assert client.post(f"{C}/event/update", json={"id": live}).json()["detail"]["code"] == "nothing"
    login(client, "root")
    assert client.put("/api/v1/admin/settings/outlook", json={"calendar_write": False}).status_code == 200
    login(client, "alice")
    assert client.post(f"{C}/event/update", json={"id": live, "subject": "Х"}).status_code == 403
    assert client.post(f"{C}/event/cancel", json={"id": live}).status_code == 403


# ------------------------------------------------------------------------------------------------ серии
def _series(g):
    """Мастер серии и три занятия (как отдаёт Graph: у занятий тип occurrence и seriesMasterId)."""
    base = utcnow() + timedelta(days=1)
    base = base.replace(minute=0, second=0, microsecond=0)
    master = g.add_event("alice@corp.test", id="MASTER", subject="Еженедельная", type="seriesMaster",
                         start={"dateTime": base.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}, end={"dateTime": (base + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"})
    g.events[master]["_hidden"] = True
    occ = []
    for i in range(3):
        s = base + timedelta(days=7 * i)
        occ.append(g.add_event("alice@corp.test", id=f"OCC{i}", subject="Еженедельная", type="occurrence", seriesMasterId=master,
                               start={"dateTime": s.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}, end={"dateTime": (s + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}))
    return master, occ, base


def test_series_requires_a_choice_and_edits_only_the_occurrence_or_the_whole_series(client):
    g = _setup(client)
    master, occ, base = _series(g)
    r = client.post(f"{C}/event/update", json={"id": occ[1], "subject": "Х"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "scope_required", "без выбора серия не меняется"
    assert g.events[master]["subject"] == "Еженедельная"
    assert client.post(f"{C}/event/update", json={"id": occ[1], "scope": "occurrence", "subject": "Только это"}).status_code == 200
    assert g.events[occ[1]]["subject"] == "Только это" and g.events[master]["subject"] == "Еженедельная" and g.events[occ[0]]["subject"] == "Еженедельная" and g.events[occ[2]]["subject"] == "Еженедельная"
    assert client.post(f"{C}/event/update", json={"id": occ[2], "scope": "series", "subject": "Вся серия"}).status_code == 200
    assert g.events[master]["subject"] == "Вся серия" and g.events[occ[1]]["subject"] == "Только это"


def test_series_time_change_keeps_the_first_date_and_changes_time_of_day_in_the_users_zone(client):
    g = _setup(client)
    master, occ, base = _series(g)
    z = ZoneInfo("Europe/Berlin")
    third = base + timedelta(days=14)
    new_local = third.astimezone(z).replace(hour=15, minute=30, second=0, microsecond=0)
    r = client.post(f"{C}/event/update", json={"id": occ[2], "scope": "series", "start": iso(new_local), "end": iso(new_local + timedelta(minutes=45)), "tz": "Europe/Berlin"})
    assert r.status_code == 200, r.text
    got = g.events[master]
    start = __import__("datetime").datetime.fromisoformat(got["start"]["dateTime"][:19]).replace(tzinfo=ZoneInfo("UTC")).astimezone(z)
    first_local_date = base.astimezone(z).date()
    assert start.date() == first_local_date and (start.hour, start.minute) == (15, 30), "дата серии прежняя, время суток новое — в поясе пользователя"
    end = __import__("datetime").datetime.fromisoformat(got["end"]["dateTime"][:19])
    assert (end - __import__("datetime").datetime.fromisoformat(got["start"]["dateTime"][:19])) == timedelta(minutes=45)


def test_cancel_one_occurrence_or_the_whole_series_and_linked_room_follows(client):
    g = _setup(client)
    master, occ, base = _series(g)
    r = client.post(f"{C}/event/cancel", json={"id": occ[0]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "scope_required"
    assert not g.events[occ[0]]["isCancelled"]
    assert client.post(f"{C}/event/cancel", json={"id": occ[0], "scope": "occurrence"}).status_code == 200
    assert g.events[occ[0]]["isCancelled"] and not g.events[master].get("isCancelled") and not g.events[occ[1]]["isCancelled"]
    assert client.post(f"{C}/event/cancel", json={"id": occ[1], "scope": "series"}).status_code == 200
    assert g.events[master]["isCancelled"] and not g.events[occ[2]]["isCancelled"], "отмена серии идёт по мастер-событию, остальное снимет Exchange"
    cancel_paths = [p for m, p in g.calls if m == "POST" and p.endswith("/cancel")]
    assert [p.rsplit("/", 2)[-2] for p in cancel_paths] == ["OCC0", "MASTER"]


def test_conference_series_room_survives_one_cancelled_occurrence_and_closes_with_the_series(client):
    g = _setup(client)
    body = {"key": "k" + "0123456789abcdef" * 2, "subject": "Серия", "start": _slot(180)[0], "end": _slot(180)[1], "tz": "Europe/Moscow", "conference": True,
            "recurrence": {"type": "daily", "interval": 1, "count": 5}}
    r = client.post(f"{C}/events", json=body)
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    g.events[eid]["type"] = "seriesMaster"
    s = utcnow() + timedelta(hours=3)
    g.add_event("alice@corp.test", id="OCCX", subject="Серия", type="occurrence", seriesMasterId=eid, start={"dateTime": s.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
                end={"dateTime": (s + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"})
    room_id = r.json()["room_id"]
    assert client.post(f"{C}/event/cancel", json={"id": "OCCX", "scope": "occurrence"}).status_code == 200

    async def state():
        async with client.app_obj.state.session_maker() as db:
            rm = (await db.execute(select(Room).where(Room.id == uuid_of(room_id)))).scalar_one()
            return rm.cancelled_at, rm.outlook_synced_at
    from uuid import UUID as uuid_of
    cancelled, synced = client.portal.call(state)
    assert cancelled is None and synced is None, "отмена одного занятия не закрывает комнату серии; сверка подхватит исключение"
    g.events["OCCX"]["isCancelled"] = False
    assert client.post(f"{C}/event/cancel", json={"id": "OCCX", "scope": "series"}).status_code == 200
    cancelled, _ = client.portal.call(state)
    assert cancelled is not None, "отмена всей серии закрывает комнату"


# ------------------------------------------------------------------------------------------------ несколько календарей
def test_calendars_list_and_events_of_selected_calendars_are_cached_separately(client):
    g = _setup(client)
    a = g.add_event("alice@corp.test", **_ev(60, subject="Основной"))
    b = g.add_event("alice@corp.test", **_ev(70, subject="Проектный"), calendar="CALPROJ")
    cals = client.get(f"{C}/calendars").json()
    assert [(c["id"], c["default"]) for c in cals["calendars"]] == [("primary", True), ("CALPROJ", False)] and cals["error"] is None
    w = _window()
    only = client.get(f"{C}/events", params=w).json()["events"]
    assert [e["title"] for e in only] == ["Основной"] and only[0]["calendar_id"] == "primary"
    both = client.get(f"{C}/events", params={**w, "calendars": "primary,CALPROJ"}).json()["events"]
    assert sorted(e["title"] for e in both) == ["Основной", "Проектный"] and {e["calendar_id"] for e in both} == {"primary", "CALPROJ"}
    n = len(g.calls)
    client.get(f"{C}/events", params={**w, "calendars": "primary,CALPROJ"})
    assert len(g.calls) == n, "повтор за минуту — из кэша, к Exchange не ходим"
    assert a and b


def test_calendars_list_failure_keeps_the_primary_calendar_working(client):
    g = _setup(client)
    g.down = True
    r = client.get(f"{C}/calendars", params={"refresh": "true"}).json()
    assert r["calendars"][0]["id"] == "primary" and r["error"]


# ------------------------------------------------------------------------------------------------ создание: место и описание
def test_create_with_location_and_description_and_without_conference(client):
    g = _setup(client)
    s, e, _ = _slot(200)
    r = client.post(f"{C}/events", json={"key": "n" + "0123456789abcdef" * 2, "subject": "Обычное событие", "start": s, "end": e, "tz": "Europe/Moscow", "conference": False,
                                          "location": "Переговорная 2", "description": "Строка 1\nСтрока 2 <b>"})
    assert r.status_code == 201, r.text
    ev = next(iter(g.events.values()))
    assert ev["location"] == {"displayName": "Переговорная 2"} and "<p>Строка 1</p><p>Строка 2 &lt;b&gt;</p>" in ev["body"]["content"], "описание экранируется"
    assert r.json()["room_id"] is None and r.json()["join_url"] is None
