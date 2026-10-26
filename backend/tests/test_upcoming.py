"""«Предстоящие встречи» на главной: конференции из Outlook и временные комнаты отдельно от постоянных переговорок; кто видит встречу, приглашённых
и их адреса; перенос, отмена, состав приглашённых (надстройка и серверная сверка), жизненный цикл серии, отсутствие дублей и N+1 при сотнях встреч."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import event, func, select

from app.models import Meeting, Room, RoomInvitee, utcnow

from .conftest import login, make_room
from .test_outlook import O, _anon, _body, _enable, _token, iso
from .test_outlook_sync import LINK, _run, ev, ews_item, ews_response, ews_transport, graph_transport
from .test_transcripts import _join

API = "/api/v1"
PEOPLE = [{"email": "Bob@corp.test", "name": "Bob B"}, {"email": "guest@example.org", "name": "Внешний Гость"}, {"email": "x@example.org", "name": "", "optional": True}]


def _schedule(client, attendees=PEOPLE, **body):
    """alice создаёт конференцию из Outlook (с идентификаторами события для сверки)."""
    _enable(client)
    h = _token(client, "alice")
    _anon(client)
    b = {**_body(), **LINK, "attendees": attendees, **body}
    r = client.post(f"{O}/meetings", json=b, headers=h)
    assert r.status_code == 200, r.text
    return r.json(), h, b


def _up(client, user):
    login(client, user)
    r = client.get(f"{API}/rooms/upcoming")
    assert r.status_code == 200, r.text
    return r.json()


def _item(client, user, rid):
    return next((x for x in _up(client, user)["items"] if x["id"] == rid), None)


def _db(client, fn):
    async def go():
        async with client.app_obj.state.session_maker() as db:
            return await fn(db)
    return client.portal.call(go)


def test_scheduled_meeting_is_listed_apart_from_permanent_rooms_with_invitees_for_those_allowed(client):
    login(client, "bob")                                       # bob уже входил: его адрес известен — сопоставление с сотрудником при сохранении
    perm = make_room(client, name="Постоянная")
    j, _h, b = _schedule(client)
    me = _up(client, "alice")
    assert me["server_time"] and [x["id"] for x in me["items"]] == [j["id"]]
    assert perm["id"] in [r["id"] for r in client.get(f"{API}/rooms").json()] and j["id"] not in [r["id"] for r in client.get(f"{API}/rooms").json()]
    it = me["items"][0]
    assert it["kind"] == "scheduled" and it["mine"] and it["organizer"] == "Alice A" and it["url"] == "/m/" + j["url"].rsplit("/", 1)[1]
    assert it["start"].startswith(b["start"][:16]) and it["end"] and it["opens_at"] and not it["cancelled"] and it["live"] is None
    assert it["invitees"]["count"] == 3 and it["invitees"]["can_list"] and it["invitees"]["names"][:2] == ["Bob B", "Внешний Гость"]
    assert it["can_manage"] and it["can_join"] and it["sync"] is None
    full = client.get(f"{API}/rooms/{j['id']}/invitees").json()["items"]
    assert {x["email"] for x in full} == {"bob@corp.test", "guest@example.org", "x@example.org"}, "организатор видит адреса"
    assert next(x for x in full if x["email"] == "bob@corp.test")["employee"] is True and full[-1]["optional"] is True
    # приглашённый: видит встречу и имена, но не адреса
    bob = _item(client, "bob", j["id"])
    assert bob and bob["mine"] and bob["invitees"]["names"] and not bob["can_manage"]
    got = client.get(f"{API}/rooms/{j['id']}/invitees").json()["items"]
    assert all(x["email"] is None for x in got) and "Bob B" in [x["name"] for x in got] and None in [x["name"] for x in got], "без имени и без права на адрес — пусто"
    # не приглашённый: встречи не видит, список не получает (404 — как будто встречи нет)
    assert _item(client, "carol", j["id"]) is None and client.get(f"{API}/rooms/{j['id']}/invitees").status_code == 404
    # администратор видит (чужая — mine=false), с адресами
    adm = _item(client, "root", j["id"])
    assert adm and adm["mine"] is False and adm["can_manage"]
    assert client.get(f"{API}/rooms/{j['id']}/invitees").json()["items"][0]["email"]


def test_invitee_who_logs_in_later_is_matched_by_email_and_acl_only_user_sees_count_without_names(client):
    j, *_ = _schedule(client, attendees=[{"email": "carol@corp.test", "name": "Carol C"}, {"email": "guest@example.org"}])
    assert _db(client, lambda db: _scalar(db, select(RoomInvitee.user_id).where(RoomInvitee.email == "carol@corp.test"))) is None, "carol ещё не входила"
    assert _item(client, "carol", j["id"])["mine"] is True, "сопоставление по адресу при первом входе"
    # bob получил доступ к комнате отдельно (список доступа) — видит встречу и число приглашённых, но не их имена
    async def grant(db):
        from app.models import RoomAcl, User                                  # noqa: PLC0415
        bob = (await db.execute(select(User).where(User.sam_account_name == "bob"))).scalars().first()
        db.add(RoomAcl(room_id=uuid.UUID(j["id"]), subject_type="user", subject_ref=bob.ad_guid.lower(), display_name="Bob B"))
        await db.commit()
    login(client, "bob")
    _db(client, grant)
    it = _item(client, "bob", j["id"])
    assert it and it["invitees"]["count"] == 2 and it["invitees"]["names"] is None and not it["invitees"]["can_list"]
    assert client.get(f"{API}/rooms/{j['id']}/invitees").status_code == 403


async def _scalar(db, q):
    return (await db.execute(q)).scalar_one_or_none()


def test_repeated_sync_is_idempotent_and_updates_time_and_invitees(client):
    j, h, b = _schedule(client)
    s = utcnow() + timedelta(hours=3)
    again = client.post(f"{O}/meetings", json={**b, "start": iso(s), "end": iso(s + timedelta(minutes=45)),
                                               "attendees": [{"email": "guest@example.org", "name": "Гость"}, {"email": "carol@corp.test", "name": "Carol C"}]}, headers=h).json()
    assert again["id"] == j["id"] and again["created"] is False
    assert _db(client, lambda db: _scalar(db, select(func.count()).select_from(Room).where(Room.outlook_key == b["key"]))) == 1, "дубля комнаты нет"
    it = _item(client, "alice", j["id"])
    assert it["start"].startswith(iso(s)[:16]) and it["invitees"]["count"] == 2
    assert _item(client, "bob", j["id"]) is None, "убранный из приглашения больше встречу не видит"
    assert _item(client, "carol", j["id"]) is not None
    # отправка без сведений о приглашённых (старая надстройка, не прочитались) список не трогает
    client.post(f"{O}/meetings", json={k: v for k, v in b.items() if k != "attendees"}, headers=h)
    assert _item(client, "alice", j["id"])["invitees"]["count"] == 2


def test_cancelled_meeting_is_not_listed_at_all_and_stays_out_after_its_time_passes(client):
    j, h, _b = _schedule(client)
    assert _item(client, "alice", j["id"]) is not None
    _anon(client)
    assert client.delete(f"{O}/meetings/{j['id']}", headers=h).status_code == 204
    assert _item(client, "alice", j["id"]) is None, "отменённая встреча на главной не показывается ни со статусом, ни в счётчиках"
    assert j["id"] not in [x["id"] for x in _up(client, "root")["items"]], "и администратору"

    async def past(db):
        r = await db.get(Room, uuid.UUID(j["id"]))
        r.scheduled_start, r.scheduled_end = utcnow() - timedelta(hours=2), utcnow() - timedelta(hours=1)
        await db.commit()
    _db(client, past)
    assert _item(client, "alice", j["id"]) is None


def test_meeting_whose_time_has_passed_is_not_listed_unless_people_are_in_it_and_nothing_is_deleted(client):
    j, *_ = _schedule(client, **_body(start_in=-5, length=60))
    assert _item(client, "alice", j["id"]) is not None, "началась, идти может — ещё актуальна"

    async def over(db):
        r = await db.get(Room, uuid.UUID(j["id"]))
        r.scheduled_start, r.scheduled_end = utcnow() - timedelta(hours=3), utcnow() - timedelta(hours=2)
        await db.commit()
    _db(client, over)
    assert _item(client, "alice", j["id"]) is None, "плановое время прошло, никто не вошёл — на главной нет"
    row = _db(client, lambda db: db.get(Room, uuid.UUID(j["id"])))
    assert row is not None and row.lifecycle != "deleted", "запись в базе остаётся"
    a = _join(client, "alice", j["id"])
    assert _item(client, "alice", j["id"])["live"], "если в комнате идёт встреча — она показывается, даже если плановое время вышло"
    login(client, "alice")
    assert client.post(f"{API}/meetings/{a['meeting_id']}/end").status_code == 204
    assert _item(client, "alice", j["id"]) is None, "встреча закончилась — из списка ушла"
    login(client, "alice")
    assert a["meeting_id"] in [m["id"] for m in client.get(f"{API}/meetings").json()], "в «Истории» осталась"


def test_finished_occurrence_does_not_hide_the_series_and_an_ended_series_is_gone(client):
    j, *_ = _schedule(client, recurring=True)
    a = _join(client, "alice", j["id"])
    login(client, "alice")
    assert client.post(f"{API}/meetings/{a['meeting_id']}/end").status_code == 204
    it = _item(client, "alice", j["id"])
    assert it and it["recurring"] and it["live"] is None and it["last_ended_at"], "занятие закончилось — серия остаётся с ближайшим занятием"

    ended = {"type": "daily", "interval": 1, "start": (utcnow() - timedelta(days=10)).date().isoformat(), "end": (utcnow() - timedelta(days=2)).date().isoformat(), "count": None,
             "time": "10:00", "minutes": 30, "tz": "UTC"}

    async def finish(db):
        r = await db.get(Room, uuid.UUID(j["id"]))
        r.outlook_recurrence, r.outlook_tz = ended, "UTC"
        await db.commit()
    _db(client, finish)
    assert _item(client, "alice", j["id"]) is None, "у серии больше нет занятий — на главной её нет"


def test_series_room_survives_its_sessions_and_live_status_reflects_the_real_meeting(client):
    j, *_ = _schedule(client, recurring=True)
    it = _item(client, "alice", j["id"])
    assert it["recurring"] and it["live"] is None
    a = _join(client, "alice", j["id"])
    it = _item(client, "alice", j["id"])
    assert it["live"] and it["live"]["participants"] == 1, "«Идёт» — по фактической встрече"
    login(client, "alice")
    assert client.post(f"{API}/meetings/{a['meeting_id']}/end").status_code == 204
    row = _db(client, lambda db: db.get(Room, uuid.UUID(j["id"])))
    assert row.lifecycle != "closed", "занятие серии закончилось — комната серии остаётся"
    it = _item(client, "alice", j["id"])
    assert it and it["live"] is None and it["last_ended_at"]
    b2 = _join(client, "alice", j["id"])
    assert b2["meeting_id"] != a["meeting_id"], "следующее занятие — новая встреча в той же комнате (свои материалы)"
    assert _db(client, lambda db: _scalar(db, select(func.count()).select_from(Meeting).where(Meeting.room_id == uuid.UUID(j["id"])))) == 2


def test_single_meeting_room_is_not_closed_by_an_early_empty_session(client):
    j, *_ = _schedule(client, **_body(start_in=-5, length=60))           # встреча уже идёт по расписанию, вход открыт
    a = _join(client, "alice", j["id"])
    login(client, "alice")
    assert client.post(f"{API}/meetings/{a['meeting_id']}/leave").status_code == 204
    _db(client, lambda db: _end_empty(db, client, a["meeting_id"]))
    assert _db(client, lambda db: db.get(Room, uuid.UUID(j["id"]))).lifecycle != "closed", "до планового окончания в комнату можно вернуться"
    assert _join(client, "bob", j["id"])["meeting_id"] != a["meeting_id"]


async def _end_empty(db, client, meeting_id):
    m = await db.get(Meeting, uuid.UUID(meeting_id))
    m.empty_since = utcnow() - timedelta(minutes=30)
    await db.commit()
    await client.app_obj.state.meetings.reconcile(db, m)


def test_calendar_sync_updates_invitees_from_graph_and_ews(client):
    j, *_ = _schedule(client, attendees=[{"email": "bob@corp.test", "name": "Bob B"}])
    s = utcnow() + timedelta(hours=4)
    g = ev(s, s + timedelta(hours=1))
    g["attendees"] = [{"type": "required", "emailAddress": {"name": "Carol C", "address": "carol@corp.test"}},
                      {"type": "optional", "emailAddress": {"name": "Гость", "address": "guest@example.org"}},
                      {"type": "resource", "emailAddress": {"name": "Переговорная 5", "address": "room5@example.org"}}]
    assert _run(client, graph_transport(g))["checked"] == 1
    login(client, "alice")
    got = {x["email"]: x for x in client.get(f"{API}/rooms/{j['id']}/invitees").json()["items"]}
    assert set(got) == {"carol@corp.test", "guest@example.org"} and got["guest@example.org"]["optional"], "ресурсы — не люди; bob убран календарём"
    # EWS: один адрес не в SMTP-виде — прежних приглашённых не удаляем, остальных добавляем
    item = ews_item(s, s + timedelta(hours=1)).replace("</t:CalendarItem>", "<t:RequiredAttendees><t:Attendee><t:Mailbox><t:Name>Dave</t:Name><t:EmailAddress>dave@corp.test</t:EmailAddress>"
                                                       "</t:Mailbox></t:Attendee><t:Attendee><t:Mailbox><t:Name>Legacy</t:Name><t:EmailAddress>/O=EXCH/CN=legacy</t:EmailAddress></t:Mailbox>"
                                                       "</t:Attendee></t:RequiredAttendees></t:CalendarItem>")
    assert _run(client, ews_transport(ews_response(item=item)), mode="ews")["checked"] == 1
    login(client, "alice")
    assert {x["email"] for x in client.get(f"{API}/rooms/{j['id']}/invitees").json()["items"]} == {"carol@corp.test", "guest@example.org", "dave@corp.test"}
    # календарь недоступен — последнее подтверждённое состояние сохраняется, карточка видит ошибку сверки
    assert _run(client, graph_transport(503))["error"] == 1
    it = _item(client, "alice", j["id"])
    assert it["invitees"]["count"] == 3 and it["sync"]["state"] == "error" and it["sync"]["at"]


def test_hundreds_of_meetings_are_served_with_a_constant_number_of_queries(client):
    """Сотни встреч: число SQL-запросов не зависит от числа встреч (нет запроса на каждую карточку)."""
    _enable(client)
    login(client, "alice")
    alice = client.get(f"{API}/auth/me").json()["user"]

    async def seed(db, n):
        for i in range(n):
            s = utcnow() + timedelta(hours=1 + i)
            r = Room(slug=f"ol-seed{i:04d}{uuid.uuid4().hex[:6]}", name=f"Встреча {i}", lifetime="temporary", lifecycle="active", peer_access="link", history_access="participants",
                     created_by_user_id=uuid.UUID(alice["id"]) if i % 2 else None, created_by_name="Alice A", scheduled_start=s, scheduled_end=s + timedelta(minutes=30),
                     join_opens_at=s - timedelta(minutes=10), auto_close_at=s + timedelta(hours=2))
            db.add(r)
            await db.flush()
            db.add(RoomInvitee(room_id=r.id, email=f"p{i}@example.org", name=f"Участник {i}"))
        await db.commit()

    def count_queries(user):
        n = [0]
        eng = client.app_obj.state.engine.sync_engine

        def hit(_c, _cur, stmt, *_a):
            n[0] += stmt.lstrip().upper().startswith("SELECT")      # записи журнала и аудита идут фоном параллельно — их не считаем
        event.listen(eng, "before_cursor_execute", hit)
        try:
            items = _up(client, user)["items"]
        finally:
            event.remove(eng, "before_cursor_execute", hit)
        return n[0], items

    _db(client, lambda db: seed(db, 30))
    count_queries("root")                    # прогрев: первый запрос наполняет кэши (настройки и т. п.) — это не зависит от числа встреч
    q30, items30 = count_queries("root")
    _db(client, lambda db: seed(db, 300))
    q330, items330 = count_queries("root")
    assert len(items30) == 30 and len(items330) == 330
    assert q330 == q30, f"запросов: 30 встреч — {q30}, 330 встреч — {q330}"
    assert [x["start"] for x in items330] == sorted(x["start"] for x in items330), "ближайшие первыми"
    assert len([x for x in _up(client, "alice")["items"] if x["mine"]]) == 165, "alice видит только свои (организатор)"


def test_temporary_room_guest_link_only_for_whoever_manages_it(client):
    """Гостевая ссылка временной комнаты в «Предстоящих» — только тому, кто управляет (как раньше в каталоге), и только при включённом гостевом входе."""
    from .conftest import put_settings
    put_settings(client, "general", temp_room_allow_guest_link=True)
    login(client, "alice")
    room = client.post(f"{API}/rooms/temporary", json={"name": "Быстрый созвон"}).json()
    assert _item(client, "alice", room["id"])["guest_token"] is None, "гостевой вход ещё не включён"
    login(client, "alice")
    tok = client.patch(f"{API}/rooms/{room['id']}/manage", json={"guest_access_enabled": True}).json()["guest_token"]
    it = _item(client, "alice", room["id"])
    assert it["kind"] == "temporary" and it["guest_token"] == tok and it["url"] == f"/rooms/{room['slug']}"
    assert _item(client, "root", room["id"])["guest_token"] == tok, "администратор управляет любой комнатой"


def test_finished_meeting_leaves_upcoming_by_schedule_and_stays_in_history(client):
    """После планового окончания (и запаса «держать открытой») комнату закрывает расписание: из «Предстоящих» встреча уходит, в «Истории» остаётся."""
    j, *_ = _schedule(client, **_body(start_in=-5, length=60))
    a = _join(client, "alice", j["id"])
    login(client, "alice")
    assert client.post(f"{API}/meetings/{a['meeting_id']}/end").status_code == 204
    assert _item(client, "alice", j["id"]) is None, "встреча закончилась — на главной её больше нет (не ждём закрытия по расписанию)"

    async def expire(db):
        r = await db.get(Room, uuid.UUID(j["id"]))
        r.auto_close_at = utcnow() - timedelta(minutes=1)
        await db.commit()
        return await client.app_obj.state.meetings.cleanup_temporary(db)
    assert _db(client, expire) >= 1
    assert _item(client, "alice", j["id"]) is None, "закрытая по расписанию — не «предстоящая»"
    login(client, "alice")
    assert a["meeting_id"] in [m["id"] for m in client.get(f"{API}/meetings").json()], "встреча и её материалы — в «Истории»"


def test_series_shows_its_next_session_by_pattern_and_calendar_sync_updates_the_pattern(client):
    """Серия с шаблоном: в «Предстоящих» — ближайшее занятие (время и сортировка вместе с разовыми); шаблон обновляют надстройка и сверка (Graph, EWS)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    soon = (now + timedelta(hours=2)).astimezone(timezone.utc)
    rec = {"type": "daily", "props": {"interval": 1}, "start_date": (now - timedelta(days=3)).date().isoformat(), "end_date": None,
           "start_time": soon.strftime("%H:%M"), "duration": 30}
    j, h, b = _schedule(client, recurring=True, recurrence=rec, tz="UTC")
    single, *_ = _schedule(client, **_body(start_in=60 * 5, length=30))
    items = _up(client, "alice")["items"]
    it = next(x for x in items if x["id"] == j["id"])
    assert it["recurring"] and it["end"] and it["start"].startswith(soon.strftime("%Y-%m-%dT%H:%M")), it
    ids = [x["id"] for x in items]
    assert ids.index(j["id"]) < ids.index(single["id"]), "занятие серии через 2 ч — раньше разовой встречи через 5 ч"
    # мусорный шаблон от клиента не затирает хороший
    client.post(f"{O}/meetings", json={**b, "recurrence": {"type": "hourly"}}, headers=h)
    assert _db(client, lambda db: db.get(Room, uuid.UUID(j["id"]))).outlook_recurrence["type"] == "daily"
    # Graph: серия стала еженедельной по понедельникам 10:00 UTC
    g = ev(now, now + timedelta(minutes=45))
    g["recurrence"] = {"pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["monday"]}, "range": {"type": "noEnd", "startDate": "2026-09-07"}}
    g["start"]["dateTime"], g["end"]["dateTime"] = "2026-09-07T10:00:00.0000000", "2026-09-07T10:45:00.0000000"
    assert _run(client, graph_transport(g))["checked"] >= 1
    row = _db(client, lambda db: db.get(Room, uuid.UUID(j["id"])))
    assert row.outlook_recurrence["type"] == "weekly" and row.outlook_recurrence["days"] == [0] and row.outlook_recurrence["minutes"] == 45
    st = _item(client, "alice", j["id"])
    assert datetime.fromisoformat(st["start"].replace("Z", "+00:00")).weekday() == 0 and st["start"][11:16] == "10:00"
    # EWS: последний четверг месяца
    item = ews_item(now, now + timedelta(hours=1)).replace("</t:CalendarItem>", "<t:Recurrence><t:RelativeMonthlyRecurrence><t:Interval>1</t:Interval><t:DaysOfWeek>Thursday</t:DaysOfWeek>"
                                                          "<t:DayOfWeekIndex>Last</t:DayOfWeekIndex></t:RelativeMonthlyRecurrence><t:NoEndRecurrence><t:StartDate>2026-09-01+03:00</t:StartDate>"
                                                          "</t:NoEndRecurrence></t:Recurrence></t:CalendarItem>")
    assert _run(client, ews_transport(ews_response(item=item)), mode="ews")["checked"] >= 1
    row = _db(client, lambda db: db.get(Room, uuid.UUID(j["id"])))
    assert row.outlook_recurrence["type"] == "monthly_nth" and row.outlook_recurrence["nth"] == 5 and row.outlook_recurrence["days"] == [3] and row.outlook_recurrence["start"] == "2026-09-01"
    # встречу сделали разовой — шаблон сбрасывается
    client.post(f"{O}/meetings", json={**b, "recurring": False, "start": iso(now + timedelta(hours=1)), "end": iso(now + timedelta(hours=2))}, headers=h)
    assert _db(client, lambda db: db.get(Room, uuid.UUID(j["id"]))).outlook_recurrence is None
