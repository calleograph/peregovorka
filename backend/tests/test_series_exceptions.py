"""Исключения серии Outlook: перенос и отмена одного занятия (надстройка, сверка Graph и EWS), пояс серии из календаря, повторная сверка без дублей,
сохранение истории. Реального Exchange/Microsoft 365 нет: ответы — по документированным форматам (Graph v1.0 `exceptionOccurrences`/`cancelledOccurrences`,
EWS `ModifiedOccurrences`/`DeletedOccurrences`/`StartTimeZone`). Адреса вымышленные (example.org)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select

from app.models import Meeting, Room, RoomInvitee

from .test_outlook import O
from .test_outlook_sync import LINK, _run, ews_response, ews_transport
from .test_upcoming import _db, _item, _schedule

MSK = ZoneInfo("Europe/Moscow")
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MASTER, OCC = LINK["rest_id"], "AAMkAGOccurrence0123456789"


def _plan():
    """Еженедельная серия в 10:00 по Москве; первое занятие — послезавтра (по Москве), дальше каждые 7 дней."""
    d1 = datetime.now(MSK).date() + timedelta(days=2)
    at = lambda d, h=10, m=0: datetime(d.year, d.month, d.day, h, m, tzinfo=MSK).astimezone(timezone.utc)  # noqa: E731
    return d1, at


def _utc(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.0000000")


def _master(d1: date, at, **extra) -> dict:
    s = at(d1)
    return {"subject": "Еженедельная планёрка", "isCancelled": False, "type": "seriesMaster", "originalStartTimeZone": "Russian Standard Time",
            "start": {"dateTime": _utc(s), "timeZone": "UTC"}, "end": {"dateTime": _utc(s + timedelta(hours=1)), "timeZone": "UTC"},
            "attendees": [{"type": "required", "emailAddress": {"address": "bob@example.org", "name": "Bob B"}}],
            "recurrence": {"pattern": {"type": "weekly", "interval": 1, "daysOfWeek": [DAYS[d1.weekday()]]},
                           "range": {"type": "noEnd", "startDate": d1.isoformat(), "recurrenceTimeZone": "Russian Standard Time"}}, **extra}


def graph_series(master: dict, ext: dict | int, calls: list | None = None, occurrence: dict | None = None):
    """Graph: мастер-событие серии, его исключения (второй запрос) и, если задано, отдельное занятие (комната связана с ним прежней надстройкой)."""
    def h(req: httpx.Request) -> httpx.Response:
        url = unquote(str(req.url))
        if calls is not None:
            calls.append(url)
        if "oauth2/v2.0/token" in url:
            return httpx.Response(200, json={"access_token": "tok"})
        assert req.headers["authorization"] == "Bearer tok" and 'outlook.timezone="UTC"' in req.headers["prefer"] and "body" not in url
        if "cancelledOccurrences" in url:
            assert "$expand=exceptionOccurrences" in url and f"/events/{MASTER}?" in url, url
            return httpx.Response(ext, json={"error": {}}) if isinstance(ext, int) else httpx.Response(200, json=ext)
        if f"/events/{OCC}?" in url and occurrence is not None:
            return httpx.Response(200, json=occurrence)
        return httpx.Response(200, json=master)
    return httpx.MockTransport(h)


def _series(client):
    d1, at = _plan()
    j, h, b = _schedule(client, recurring=True, **{"start": at(d1).isoformat(), "end": (at(d1) + timedelta(hours=1)).isoformat()})
    return j, h, b, d1, at


def _room(client, rid) -> Room:
    return _db(client, lambda db: db.get(Room, uuid.UUID(rid)))


def _start(client, rid) -> str:
    return _item(client, "alice", rid)["start"][:16]


def test_graph_cancelled_and_moved_occurrences_change_only_their_own_slots(client):
    j, _h, _b, d1, at = _series(client)
    d2, d3 = d1 + timedelta(days=7), d1 + timedelta(days=14)
    moved_to = at(d2 + timedelta(days=1), 15, 30)                                    # занятие второй недели — на следующий день в 15:30
    ext = {"cancelledOccurrences": [f"OID.{MASTER}.{d1.isoformat()}"],
           "exceptionOccurrences": [{"id": "AAMkExc1", "originalStart": at(d2).strftime("%Y-%m-%dT%H:%M:%SZ"), "start": {"dateTime": _utc(moved_to), "timeZone": "UTC"},
                                     "end": {"dateTime": _utc(moved_to + timedelta(minutes=45)), "timeZone": "UTC"}}]}
    calls: list = []
    res = _run(client, graph_series(_master(d1, at), ext, calls))
    assert res["rescheduled"] == 1 and res["cancelled"] == 0
    assert any("cancelledOccurrences" in c for c in calls), "исключения читаются у мастер-события"
    r = _room(client, j["id"])
    assert r.cancelled_at is None and r.lifecycle == "active", "отмена одного занятия серию не отменяет"
    assert r.outlook_recurrence["tz"] == "Europe/Moscow" and r.outlook_recurrence["time"] == "10:00", "пояс серии — из календаря"
    assert r.outlook_exceptions == {"cancelled": [d1.isoformat()], "moved": {d2.isoformat(): [moved_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                                                              (moved_to + timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")]}}
    it = _item(client, "alice", j["id"])
    assert it["start"][:16] == moved_to.strftime("%Y-%m-%dT%H:%M"), "отменённое занятие не предстоящее; ближайшее — перенесённое, в новое время"
    assert it["end"][:16] == (moved_to + timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M") and not it["cancelled"] and it["can_join"]
    # повторная сверка: те же исключения, без дублей приглашённых и комнат
    res = _run(client, graph_series(_master(d1, at), ext))
    assert res["rescheduled"] == 0 and res["ok"] == 1
    assert _room(client, j["id"]).outlook_exceptions["cancelled"] == [d1.isoformat()]
    assert _db(client, lambda db: _count(db, RoomInvitee, RoomInvitee.room_id == uuid.UUID(j["id"]))) == 1
    assert _db(client, lambda db: _count(db, Room, Room.outlook_key == r.outlook_key)) == 1
    # перенос отменили в календаре (занятие вернулось на место), третье занятие отменили
    ext2 = {"cancelledOccurrences": [f"OID.{MASTER}.{d1.isoformat()}", f"OID.{MASTER}.{d3.isoformat()}"], "exceptionOccurrences": []}
    assert _run(client, graph_series(_master(d1, at), ext2))["rescheduled"] == 1
    assert _start(client, j["id"]) == at(d2).strftime("%Y-%m-%dT%H:%M")


def _count(db, model, cond):
    async def go():
        return (await db.execute(select(func.count()).select_from(model).where(cond))).scalar_one()
    return go()


def test_history_of_finished_sessions_survives_occurrence_cancellation(client):
    j, _h, _b, d1, at = _series(client)
    rid = uuid.UUID(j["id"])

    async def past_session(db):
        db.add(Meeting(room_id=rid, livekit_room="m-" + uuid.uuid4().hex, started_at=at(d1) - timedelta(days=7), ended_at=at(d1) - timedelta(days=7) + timedelta(hours=1)))
        await db.commit()
    _db(client, past_session)
    ext = {"cancelledOccurrences": [f"OID.{MASTER}.{d1.isoformat()}"], "exceptionOccurrences": []}
    _run(client, graph_series(_master(d1, at), ext))
    assert _db(client, lambda db: _count(db, Meeting, Meeting.room_id == rid)) == 1, "прошедшая встреча серии и её материалы остаются"
    r = _room(client, j["id"])
    assert r.cancelled_at is None and r.closed_at is None
    assert _start(client, j["id"]) == at(d1 + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")


def test_exceptions_request_failure_keeps_previous_exceptions_and_never_cancels(client):
    j, _h, _b, d1, at = _series(client)
    _run(client, graph_series(_master(d1, at), {"cancelledOccurrences": [f"OID.{MASTER}.{d1.isoformat()}"]}))
    for status in (400, 403, 500):
        res = _run(client, graph_series(_master(d1, at), status))
        assert res["cancelled"] == 0 and res["error"] == 0, status
        r = _room(client, j["id"])
        assert r.cancelled_at is None and r.outlook_exceptions["cancelled"] == [d1.isoformat()] and str(status) in (r.outlook_sync_error or ""), status
    # серию удалили целиком — комната отменяется, как раньше
    assert _run(client, graph_series(_master(d1, at, isCancelled=True), {}))["cancelled"] == 1


def test_room_linked_to_one_occurrence_is_relinked_to_the_series_master(client):
    """Прежняя надстройка привязывала комнату серии к занятию, которое открыл организатор: сверка переводит связь на мастер-событие."""
    j, _h, _b, d1, at = _series(client)

    async def corrupt(db):
        (await db.get(Room, uuid.UUID(j["id"]))).outlook_rest_id = OCC
        await db.commit()
    _db(client, corrupt)
    occ = {**_master(d1, at), "type": "occurrence", "seriesMasterId": MASTER, "recurrence": None}
    calls: list = []
    _run(client, graph_series(_master(d1, at), {"cancelledOccurrences": [f"OID.{MASTER}.{d1.isoformat()}"]}, calls, occurrence=occ))
    r = _room(client, j["id"])
    assert r.outlook_rest_id == MASTER and r.outlook_exceptions["cancelled"] == [d1.isoformat()] and r.cancelled_at is None
    assert any(f"/events/{OCC}?" in c for c in calls) and any(f"/events/{MASTER}?" in c and "cancelledOccurrences" not in c for c in calls)


def test_addin_send_of_one_occurrence_does_not_move_or_relink_the_series(client):
    j, h, b, d1, at = _series(client)
    rec = {"type": "weekly", "props": {"interval": 1, "days": [DAYS[d1.weekday()][:3]]}, "start_date": d1.isoformat(), "end_date": None, "start_time": "10:00",
           "duration": 60, "tz": "Russian Standard Time"}
    assert client.post(f"{O}/meetings", json={**b, "recurrence": rec}, headers=h).status_code == 200
    before = _room(client, j["id"])
    assert before.outlook_recurrence["tz"] == "Europe/Moscow"
    # организатор открыл одно занятие, перенёс его на 15:00 и отправил обновление: Office.js отдаёт шаблон всей серии и идентификаторы занятия
    moved = at(d1, 15)
    one = {**b, "recurrence": rec, "instance": True, "series_id": "AAMkSeriesMasterEws==", "ews_id": "AAMkOccurrenceEws==", "rest_id": OCC,
           "start": moved.isoformat(), "end": (moved + timedelta(hours=1)).isoformat(), "subject": "Только это занятие",
           "attendees": [{"email": "carol@example.org", "name": "Carol C"}]}
    r = client.post(f"{O}/meetings", json=one, headers=h)
    assert r.status_code == 200 and r.json()["id"] == j["id"] and r.json()["created"] is False
    after = _room(client, j["id"])
    assert (after.scheduled_start, after.scheduled_end, after.name, after.recurring) == (before.scheduled_start, before.scheduled_end, before.name, True)
    assert (after.outlook_rest_id, after.outlook_ews_id) == (before.outlook_rest_id, before.outlook_ews_id), "связь — с серией, не с занятием"
    assert after.outlook_recurrence == before.outlook_recurrence and after.outlook_synced_at is None, "серия не изменилась; календарь сверится при ближайшем проходе"
    emails = _db(client, lambda db: _emails(db, after.id))
    assert emails == {"bob@corp.test", "guest@example.org", "x@example.org", "carol@example.org"}, "приглашённые занятия добавлены, список серии не урезан"
    # повтор той же отправки (сетевой сбой) — без изменений и дублей
    assert client.post(f"{O}/meetings", json=one, headers=h).json()["id"] == j["id"]
    assert len(_db(client, lambda db: _emails(db, after.id))) == 4
    # конференцию добавили только к одному занятию (у него свой ключ) — это разовая встреча
    solo = client.post(f"{O}/meetings", json={**one, "key": "k" + uuid.uuid4().hex}, headers=h).json()
    s = _room(client, solo["id"])
    assert solo["created"] and s.recurring is False and s.outlook_recurrence is None and s.outlook_rest_id == OCC


def _emails(db, rid):
    async def go():
        return set((await db.execute(select(RoomInvitee.email).where(RoomInvitee.room_id == rid))).scalars())
    return go()


def _ews_master(d1: date, at, extra: str = "", kind="RecurringMaster", item_id="AAMkMasterEws==") -> str:
    s = at(d1)
    return (f'<t:CalendarItem><t:ItemId Id="{item_id}" ChangeKey="ck"/><t:Subject>EWS серия</t:Subject><t:Start>{s.strftime("%Y-%m-%dT%H:%M:%SZ")}</t:Start>'
            f'<t:End>{(s + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}</t:End><t:IsCancelled>false</t:IsCancelled>'
            f'<t:Recurrence><t:WeeklyRecurrence><t:Interval>1</t:Interval><t:DaysOfWeek>{DAYS[d1.weekday()].capitalize()}</t:DaysOfWeek></t:WeeklyRecurrence>'
            f'<t:NoEndRecurrence><t:StartDate>{d1.isoformat()}+03:00</t:StartDate></t:NoEndRecurrence></t:Recurrence><t:CalendarItemType>{kind}</t:CalendarItemType>'
            f'<t:StartTimeZone Id="Russian Standard Time" Name="(UTC+03:00) Москва"/>{extra}</t:CalendarItem>')


def test_ews_deleted_and_modified_occurrences(client):
    j, _h, _b, d1, at = _series(client)
    d2 = d1 + timedelta(days=7)
    moved_to = at(d2, 12)
    extra = (f'<t:ModifiedOccurrences><t:Occurrence><t:ItemId Id="AAMkOccEws=="/><t:Start>{moved_to.strftime("%Y-%m-%dT%H:%M:%SZ")}</t:Start>'
             f'<t:End>{(moved_to + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}</t:End><t:OriginalStart>{at(d2).strftime("%Y-%m-%dT%H:%M:%SZ")}</t:OriginalStart>'
             f'</t:Occurrence></t:ModifiedOccurrences><t:DeletedOccurrences><t:DeletedOccurrence><t:Start>{at(d1).strftime("%Y-%m-%dT%H:%M:%SZ")}</t:Start>'
             '</t:DeletedOccurrence></t:DeletedOccurrences>')
    seen: list = []
    res = _run(client, ews_transport(ews_response(item=_ews_master(d1, at, extra)), seen=seen), mode="ews")
    assert res["rescheduled"] == 1 and res["cancelled"] == 0
    xml = seen[0].content.decode()
    assert "calendar:ModifiedOccurrences" in xml and "calendar:DeletedOccurrences" in xml and "calendar:StartTimeZone" in xml
    r = _room(client, j["id"])
    assert r.cancelled_at is None and r.outlook_recurrence["tz"] == "Europe/Moscow" and r.outlook_exceptions["cancelled"] == [d1.isoformat()]
    assert _start(client, j["id"]) == moved_to.strftime("%Y-%m-%dT%H:%M")
    # непонятный ответ об исключениях — прежние сохраняются, комната не трогается
    bad = extra.replace(at(d1).strftime("%Y-%m-%dT%H:%M:%SZ"), "вчера")
    assert _run(client, ews_transport(ews_response(item=_ews_master(d1, at, bad))), mode="ews")["error"] == 0
    r = _room(client, j["id"])
    assert r.outlook_exceptions["cancelled"] == [d1.isoformat()] and "непонятном" in (r.outlook_sync_error or "")


def test_ews_room_linked_to_occurrence_reads_and_relinks_master(client):
    j, _h, _b, d1, at = _series(client)
    reqs: list = []

    def h(req: httpx.Request) -> httpx.Response:
        x = req.content.decode()
        reqs.append(x)
        if "RecurringMasterItemId" in x:
            return httpx.Response(200, content=ews_response(item=_ews_master(d1, at)).encode())
        return httpx.Response(200, content=ews_response(item=_ews_master(d1, at, kind="Occurrence", item_id=LINK["ews_id"])).encode())
    _run(client, httpx.MockTransport(h), mode="ews")
    assert any(f'RecurringMasterItemId OccurrenceId="{LINK["ews_id"]}"' in x for x in reqs)
    assert _room(client, j["id"]).outlook_ews_id == "AAMkMasterEws=="
