"""Календарь Outlook в Peregovorka: допуск, согласие сотрудника, события, создание, перенос, отмена, защита от дублей и недоступности Exchange.
Реального Exchange/Microsoft 365 нет: Graph и EWS — подставные HTTP-транспорты по документированным форматам (адреса вымышленные, example.org).
Результат не подтверждает работу на настоящем сервере организации — см. docs/OUTLOOK_CALENDAR.md."""
from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta, timezone

import httpx
from sqlalchemy import select

from app.models import AuditLog, CalendarLink, Room, utcnow

from .conftest import login
from .fake_graph import FakeGraph
from .test_outlook import iso

C = "/api/v1/calendar"
GRAPH = {"sync_mode": "graph", "graph_tenant_id": "tenant-1", "graph_client_id": "app-1", "graph_client_secret": "s3cret-value"}


def _cfg(client, **extra):
    login(client, "root")
    r = client.put("/api/v1/admin/settings/outlook", json={"enabled": True, "calendar_enabled": True, "calendar_write": True, **GRAPH, **extra})
    assert r.status_code == 200, r.text


def _graph(client) -> FakeGraph:
    g = FakeGraph()
    client.app_obj.state.calendar_transport = httpx.MockTransport(g)
    return g


def _slot(minutes_from_now=120, length=30):
    s = utcnow() + timedelta(minutes=minutes_from_now)
    return iso(s), iso(s + timedelta(minutes=length)), s


def _ev(minutes=120, **kw):
    _, _, s = _slot(minutes)
    kw.setdefault("subject", "Разбор планов")
    return dict(start={"dateTime": s.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}, end={"dateTime": (s + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}, **kw)


def _window():
    a = utcnow() - timedelta(days=1)
    return {"from": iso(a), "to": iso(a + timedelta(days=10))}


def _connect(client, user="alice"):
    login(client, user)
    r = client.post(f"{C}/connect", json={"accept": True})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ допуск и согласие
def test_calendar_is_off_by_default_and_gives_the_reason(client):
    login(client, "alice")
    st = client.get(f"{C}/status").json()
    assert st["available"] is False and st["reason"] == "disabled"
    assert client.get(f"{C}/events", params=_window()).status_code == 403
    assert client.post(f"{C}/connect", json={"accept": True}).status_code == 403


def test_calendar_needs_the_server_connection_and_group_membership(client):
    login(client, "root")
    client.put("/api/v1/admin/settings/outlook", json={"enabled": True, "calendar_enabled": True})
    login(client, "alice")
    assert client.get(f"{C}/status").json()["reason"] == "no_connection"
    _cfg(client, calendar_groups="cn=other,ou=groups,dc=corp,dc=test")
    login(client, "alice")
    assert client.get(f"{C}/status").json()["reason"] == "group"
    login(client, "carol")                                   # carol в нужной группе
    assert client.get(f"{C}/status").json()["available"] is True


def test_connection_requires_explicit_consent_and_proves_access_first(client):
    _cfg(client)
    g = _graph(client)
    login(client, "alice")
    assert client.get(f"{C}/status").json()["connected"] is False
    assert client.post(f"{C}/connect", json={}).status_code == 422                           # без явного согласия — нет
    assert client.get(f"{C}/events", params=_window()).status_code == 409                    # без подключения календарь не читается
    assert not any("calendarView" in p for _, p in g.calls)
    g.deny = True
    r = client.post(f"{C}/connect", json={"accept": True})
    assert r.status_code == 502 and "Calendars.ReadBasic.All" in r.json()["detail"]["message"]
    assert client.get(f"{C}/status").json()["connected"] is False, "при отказе Exchange согласие не записывается"
    g.deny = False
    st = _connect(client)
    assert st["connected"] is True and st["email"] == "alice@corp.test" and st["can_write"] is True
    assert client.delete(f"{C}/connect").json()["connected"] is False
    assert client.get(f"{C}/events", params=_window()).status_code == 409


def test_consent_is_per_user_and_not_inherited(client):
    _cfg(client)
    g = _graph(client)
    g.add_event("alice@corp.test", **_ev(subject="Чужая встреча Алисы"))
    _connect(client, "alice")
    login(client, "bob")
    assert client.get(f"{C}/events", params=_window()).status_code == 409                    # bob не подключал календарь
    _connect(client, "bob")
    titles = [e["title"] for e in client.get(f"{C}/events", params=_window()).json()["events"]]
    assert "Чужая встреча Алисы" not in titles, "сотрудник видит только свой ящик"


def test_address_change_in_directory_invalidates_the_consent(client, directory):
    _cfg(client)
    _graph(client)
    _connect(client)
    directory.users["alice"][1].__dict__["email"] = "other@corp.test"        # DirectoryIdentity — dataclass: имитируем смену адреса
    async def change():
        async with client.app_obj.state.session_maker() as db:
            from app.models import User
            u = (await db.execute(select(User).where(User.sam_account_name == "alice"))).scalar_one()
            u.email = "other@corp.test"
            await db.commit()
    client.portal.call(change)
    r = client.get(f"{C}/events", params=_window())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected"


# ------------------------------------------------------------------------------------------------ чтение
def test_events_are_classified_and_peregovorka_links_are_recognised(client):
    _cfg(client)
    g = _graph(client)
    base = "http://testserver"
    code = "abcdefghjkmnpqrstuvw"
    login(client, "alice")
    ids = {
        "plain": g.add_event("alice@corp.test", **_ev(subject="Обед", minutes=60)),
        "series": g.add_event("alice@corp.test", **_ev(subject="Планёрка", minutes=90, type="seriesMaster")),
        "cancelled": g.add_event("alice@corp.test", **_ev(subject="Отменено", minutes=200, isCancelled=True)),
    }
    ids["meet"] = g.add_event("alice@corp.test", **_ev(subject="Созвон", minutes=150, location={"displayName": f"{base}/m/{code}"}))
    _connect(client)
    # комната, которую создала надстройка: ссылка в поле «Место» и ключ события
    async def mk():
        async with client.app_obj.state.session_maker() as db:
            from app.models import User
            uid = (await db.execute(select(User.id).where(User.sam_account_name == "alice"))).scalar_one()
            s = utcnow() + timedelta(minutes=150)
            db.add(Room(slug="ol-" + code, name="Созвон", lifetime="temporary", lifecycle="active", created_by_user_id=uid, created_by_name="Alice A", scheduled_start=s,
                        scheduled_end=s + timedelta(minutes=30), join_opens_at=s - timedelta(minutes=10), outlook_key="k" * 20))
            await db.commit()
    client.portal.call(mk)
    r = client.get(f"{C}/events", params=_window())
    assert r.status_code == 200, r.text
    by = {e["title"]: e for e in r.json()["events"]}
    assert by["Обед"]["kind"] == "event" and by["Обед"]["status"] == "scheduled"
    assert by["Планёрка"]["kind"] == "event" and by["Планёрка"]["recurring"] is True, "повторяющееся событие Outlook без конференции — обычное, но помечено как серия"
    assert by["Отменено"]["status"] == "cancelled"
    assert by["Созвон"]["kind"] == "meeting" and by["Созвон"]["join_url"].endswith("/m/" + code) and by["Созвон"]["mine"] is True
    assert by["Обед"]["join_url"] is None and by["Обед"]["editable"] is False


def test_exchange_calls_are_cached_and_limited(client):
    _cfg(client)
    g = _graph(client)
    _connect(client)
    n0 = sum(1 for _, p in g.calls if p.endswith("/calendarView"))
    w = _window()       # одно окно: ключ кэша считается по секундам, пересчёт в цикле даёт ложный промах на границе секунды
    for _ in range(5):
        assert client.get(f"{C}/events", params=w).status_code == 200
    assert sum(1 for _, p in g.calls if p.endswith("/calendarView")) - n0 == 1, "повторные запросы за минуту идут из кэша"
    assert client.get(f"{C}/events", params=dict(w, refresh="true")).status_code == 200
    assert sum(1 for _, p in g.calls if p.endswith("/calendarView")) - n0 == 2
    assert client.get(f"{C}/events", params={"from": iso(utcnow()), "to": iso(utcnow() + timedelta(days=120))}).status_code == 422


def test_exchange_outage_shows_last_known_events_and_a_clear_message(client):
    _cfg(client)
    g = _graph(client)
    g.add_event("alice@corp.test", **_ev(subject="До сбоя"))
    _connect(client)
    w = _window()
    assert [e["title"] for e in client.get(f"{C}/events", params=w).json()["events"]] == ["До сбоя"]
    g.down = True
    r = client.get(f"{C}/events", params=dict(w, refresh="true")).json()
    assert r["stale"] is True and r["error"] and "Нет связи" in r["error"] and [e["title"] for e in r["events"]] == ["До сбоя"]
    r2 = client.get(f"{C}/events", params=dict(_window(), to=iso(utcnow() + timedelta(days=12)), refresh="true")).json()      # окна нет в кэше: пусто, но раздел жив
    assert r2["events"] == [] and r2["stale"] is False and r2["error"]


# ------------------------------------------------------------------------------------------------ создание
def _create_body(key=None, **kw):
    s, e, _ = _slot(180)
    return {"key": key or ("k" + uuid.uuid4().hex), "subject": "Согласование бюджета", "start": s, "end": e, "tz": "Europe/Moscow", "conference": True,
            "attendees": [{"email": "bob@corp.test", "name": "Bob B"}, {"email": "ext@example.org", "optional": True}], **kw}


def test_create_conference_in_the_users_own_calendar_with_invitations_and_links(client):
    _cfg(client, guest_links=True)
    g = _graph(client)
    _connect(client)
    r = client.post(f"{C}/events", json=_create_body(guest_link=True))
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["join_url"].startswith("http://testserver/m/") and j["guest_url"].startswith("http://testserver/guest/") and j["room_id"]
    posts = [(m, p) for m, p in g.calls if m == "POST" and p.endswith("/events")]
    assert [(m, p.replace("%40", "@")) for m, p in posts] == [("POST", "/v1.0/users/alice@corp.test/events")], "событие создаётся в ящике самого сотрудника, не служебного"
    e = next(iter(g.events.values()))
    assert e["mailbox"] == "alice@corp.test" and e["raw"]["start"]["timeZone"] == "Europe/Moscow"
    assert {a["emailAddress"]["address"]: a["type"] for a in e["raw"]["attendees"]} == {"bob@corp.test": "required", "ext@example.org": "optional"}     # приглашения рассылает Exchange
    assert j["join_url"] in e["raw"]["body"]["content"] and j["guest_url"] in e["raw"]["body"]["content"] and e["raw"]["location"]["displayName"] == j["join_url"]
    assert e["raw"]["transactionId"]

    async def room():
        async with client.app_obj.state.session_maker() as db:
            return await db.get(Room, uuid.UUID(j["room_id"]))
    rm = client.portal.call(room)
    assert rm.outlook_organizer == "alice@corp.test" and rm.outlook_rest_id == e["id"] and rm.outlook_tz == "Europe/Moscow", "связь с событием записана: серверная сверка подхватит правки и отмену"
    ev = [x for x in client.get(f"{C}/events", params=dict(_window(), to=iso(utcnow() + timedelta(days=5)), refresh="true")).json()["events"] if x["id"] == e["id"]][0]
    assert ev["kind"] == "meeting" and ev["mine"] is True and ev["editable"] is True and ev["guest_url"] == j["guest_url"]


def test_staff_attendees_are_resolved_on_the_server_and_missing_addresses_are_reported(client):
    _cfg(client)
    g = _graph(client)
    _connect(client)
    login(client, "bob")                                               # bob уже входил: он есть во внутреннем справочнике
    login(client, "alice")
    bob = [u for u in client.get("/api/v1/chats/people", params={"q": "bob"}).json()["users"] if u["login"] == "bob" and u["id"]]
    assert bob
    r = client.post(f"{C}/events", json=_create_body(attendees=[{"id": bob[0]["id"]}, {"email": "ext@example.org"}, {"id": bob[0]["id"], "email": "evil@example.org"}]))
    assert r.status_code == 201, r.text
    got = {a["emailAddress"]["address"] for a in next(iter(g.events.values()))["raw"]["attendees"]}
    assert got == {"bob@corp.test", "ext@example.org"}, "адрес сотрудника берётся из его профиля; подставленный клиентом чужой адрес игнорируется"
    assert client.post(f"{C}/events", json=_create_body(attendees=[{"id": str(uuid.uuid4())}])).status_code == 404
    assert client.post(f"{C}/events", json=_create_body(attendees="x")).status_code == 422


def test_second_click_or_retry_never_creates_a_duplicate(client):
    _cfg(client)
    g = _graph(client)
    _connect(client)
    body = _create_body()
    a = client.post(f"{C}/events", json=body).json()
    b = client.post(f"{C}/events", json=body)
    assert b.status_code == 201 and b.json()["duplicate"] is True and b.json()["room_id"] == a["room_id"]
    assert len(g.events) == 1
    # потеряли запись о завершении (Redis очистили), комната уже связана — второе событие всё равно не создаётся
    async def forget_done():
        r = client.app_obj.state.redis
        async for k in r.scan_iter(match="cal:created:*"):
            await r.delete(k)
    client.portal.call(forget_done)
    c = client.post(f"{C}/events", json=body)
    assert c.status_code == 201 and len(g.events) == 1


def test_a_request_that_is_still_running_is_not_started_twice(client):
    _cfg(client)
    g = _graph(client)
    _connect(client)
    body = _create_body()
    from sqlalchemy import select as sel
    from app.models import User
    async def put_pending():
        async with client.app_obj.state.session_maker() as db:
            uid = (await db.execute(sel(User.id).where(User.sam_account_name == "alice"))).scalar_one()
        await client.app_obj.state.redis.set(f"cal:created:{uid}:{body['key']}", "pending", ex=60)
    client.portal.call(put_pending)
    r = client.post(f"{C}/events", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "busy" and not g.events


def test_guest_link_only_when_asked_and_allowed(client):
    _cfg(client, guest_links=True)
    _graph(client)
    _connect(client)
    assert client.post(f"{C}/events", json=_create_body()).json()["guest_url"] is None                    # не просили — не выпускается
    _cfg(client, guest_links=False)
    login(client, "alice")
    assert client.post(f"{C}/events", json=_create_body(guest_link=True)).json()["guest_url"] is None     # запрещено администратором — не обходится


def test_event_without_conference_creates_only_the_calendar_item(client):
    _cfg(client)
    g = _graph(client)
    _connect(client)
    j = client.post(f"{C}/events", json=_create_body(conference=False)).json()
    assert j["room_id"] is None and j["join_url"] is None and len(g.events) == 1
    e = next(iter(g.events.values()))
    assert e["raw"]["location"]["displayName"] == "" and "/m/" not in e["raw"]["body"]["content"]


def test_recurring_event_is_sent_as_a_pattern_with_the_users_zone(client):
    _cfg(client)
    g = _graph(client)
    _connect(client)
    s = utcnow() + timedelta(days=1, hours=2)
    body = _create_body(recurrence={"type": "weekly", "days": [0, 2], "count": 6}, start=iso(s), end=iso(s + timedelta(minutes=45)))
    r = client.post(f"{C}/events", json=body)
    assert r.status_code == 201, r.text
    rec = next(iter(g.events.values()))["raw"]["recurrence"]
    assert rec["pattern"]["type"] == "weekly" and rec["pattern"]["daysOfWeek"] == ["monday", "wednesday"] and rec["range"] == {
        "type": "numbered", "startDate": rec["range"]["startDate"], "numberOfOccurrences": 6, "recurrenceTimeZone": "Europe/Moscow"}
    bad = client.post(f"{C}/events", json=_create_body(recurrence={"type": "yearly"}))
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "bad_recurrence"


def test_create_is_refused_without_write_permission_consent_or_valid_input(client):
    _cfg(client, calendar_write=False)
    _graph(client)
    _connect(client)
    assert client.post(f"{C}/events", json=_create_body()).json()["detail"]["code"] == "write_disabled"
    _cfg(client)
    login(client, "alice")
    for bad, code in (({"subject": " "}, "bad_subject"), ({"key": "short"}, "bad_key"), ({"end": iso(utcnow())}, "bad_time"), ({"start": "вчера"}, "bad_time")):
        r = client.post(f"{C}/events", json=_create_body(**bad))
        assert r.status_code == 422 and r.json()["detail"]["code"] in (code, "in_past", "bad_time"), (bad, r.text)
    client.delete(f"{C}/connect")
    assert client.post(f"{C}/events", json=_create_body()).json()["detail"]["code"] == "not_connected"


def test_exchange_failure_leaves_no_orphan_room_and_allows_retry(client):
    _cfg(client)
    g = _graph(client)
    _connect(client)
    g.deny = True
    body = _create_body()
    r = client.post(f"{C}/events", json=body)
    assert r.status_code == 502 and "Calendars.ReadWrite" in r.json()["detail"]["message"]
    async def alive():
        async with client.app_obj.state.session_maker() as db:
            return (await db.execute(select(Room).where(Room.outlook_key.like("pgcal%"), Room.cancelled_at.is_(None)))).scalars().all()
    assert client.portal.call(alive) == [], "комната без события не остаётся"
    g.deny = False
    assert client.post(f"{C}/events", json=body).status_code == 201 and len(g.events) == 1


# ------------------------------------------------------------------------------------------------ перенос и отмена
def _created(client):
    j = client.post(f"{C}/events", json=_create_body()).json()
    g_event = next(iter(client.app_obj.state.calendar_transport.handler.events.values()))
    return j, g_event


def test_reschedule_checks_etag_and_updates_room_and_exchange(client):
    _cfg(client)
    _graph(client)
    _connect(client)
    j, e = _created(client)
    s, en, _ = _slot(300)
    stale = client.patch(f"{C}/events/{j['room_id']}", json={"start": s, "end": en, "etag": 'W/"old"'})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "conflict"
    ok = client.patch(f"{C}/events/{j['room_id']}", json={"start": s, "end": en, "subject": "Перенесено", "etag": e["@odata.etag"]})
    assert ok.status_code == 200, ok.text
    assert e["subject"] == "Перенесено"

    async def room():
        async with client.app_obj.state.session_maker() as db:
            return await db.get(Room, uuid.UUID(j["room_id"]))
    r = client.portal.call(room)
    assert r.name == "Перенесено" and abs((r.scheduled_start - _slot(300)[2].replace(tzinfo=timezone.utc)).total_seconds()) < 5
    assert client.patch(f"{C}/events/{j['room_id']}", json={}).status_code == 422


def test_only_the_organizer_can_change_or_cancel_and_series_are_not_edited_here(client):
    _cfg(client)
    _graph(client)
    _connect(client)
    j, _e = _created(client)
    _connect(client, "bob")
    assert client.patch(f"{C}/events/{j['room_id']}", json={"subject": "Взлом"}).status_code == 404
    assert client.delete(f"{C}/events/{j['room_id']}").status_code == 404
    login(client, "alice")
    s = utcnow() + timedelta(days=1)
    rj = client.post(f"{C}/events", json=_create_body(recurrence={"type": "daily", "count": 3}, start=iso(s), end=iso(s + timedelta(minutes=30)))).json()
    r = client.patch(f"{C}/events/{rj['room_id']}", json={"subject": "Новое"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "series"


def test_cancel_cancels_exchange_event_and_closes_the_link(client):
    _cfg(client, guest_links=True)
    g = _graph(client)
    _connect(client)
    j = client.post(f"{C}/events", json=_create_body(guest_link=True)).json()
    token = j["guest_url"].rsplit("/", 1)[1]
    assert client.get(f"/api/v1/guest/room/{token}").status_code == 200
    r = client.delete(f"{C}/events/{j['room_id']}")
    assert r.status_code == 200 and r.json()["cancelled"] is True
    assert next(iter(g.events.values()))["isCancelled"] is True
    assert client.get(f"/api/v1/guest/room/{token}").status_code == 404, "гостевая ссылка умирает вместе со встречей"
    assert client.delete(f"{C}/events/{j['room_id']}").status_code in (200, 409)


def test_audit_records_calendar_actions_without_secrets(client):
    _cfg(client, guest_links=True)
    _graph(client)
    _connect(client)
    j = client.post(f"{C}/events", json=_create_body(guest_link=True)).json()
    client.delete(f"{C}/events/{j['room_id']}")
    async def rows():
        async with client.app_obj.state.session_maker() as db:
            return [(r.action, str(r.details)) for r in (await db.execute(select(AuditLog))).scalars()]
    got = client.portal.call(rows)
    assert {"calendar.connect", "calendar.create", "calendar.cancel"} <= {a for a, _ in got}
    blob = " ".join(d for _, d in got)
    assert j["guest_url"].rsplit("/", 1)[1] not in blob and "s3cret-value" not in blob and "Согласование бюджета" not in blob
    assert "bob@corp.test" not in blob, "адреса участников в аудит не пишутся — только их число"


def test_link_table_has_no_secrets():
    cols = {c.name for c in CalendarLink.__table__.columns}
    assert cols == {"user_id", "email", "consented_at", "last_ok_at", "last_error"}
