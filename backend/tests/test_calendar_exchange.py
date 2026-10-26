"""Обмен с Exchange для календаря: EWS (разбор FindItem, CreateItem/UpdateItem/DeleteItem) и шаблоны повторения на подставных ответах. Запись — только с имперсонацией ящика сотрудника.
Реального Exchange нет: формат запросов построен по документации Microsoft; проверяется структура и правила, а не работа на сервере организации."""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
import pytest

from app.services.calendar_exchange import (CalendarError, EventSpec, EwsCalendar, recurrence_to_ews, recurrence_to_graph)

T = "http://schemas.microsoft.com/exchange/services/2006/types"
M = "http://schemas.microsoft.com/exchange/services/2006/messages"
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS = {"t": T, "m": M, "s": SOAP}
URL = "https://mail.example.org/EWS/Exchange.asmx"


def _resp(inner: str) -> httpx.Response:
    return httpx.Response(200, content=f'<?xml version="1.0"?><s:Envelope xmlns:s="{SOAP}" xmlns:m="{M}" xmlns:t="{T}"><s:Body>{inner}</s:Body></s:Envelope>'.encode())


def _find(items: str, cls="Success", code="NoError") -> httpx.Response:
    return _resp(f'<m:FindItemResponse><m:ResponseMessages><m:FindItemResponseMessage ResponseClass="{cls}"><m:ResponseCode>{code}</m:ResponseCode>'
                 f'<m:RootFolder><t:Items>{items}</t:Items></m:RootFolder></m:FindItemResponseMessage></m:ResponseMessages></m:FindItemResponse>')


def _write(kind: str, cls="Success", code="NoError", item="") -> httpx.Response:
    return _resp(f'<m:{kind}Response><m:ResponseMessages><m:{kind}ResponseMessage ResponseClass="{cls}"><m:ResponseCode>{code}</m:ResponseCode>'
                 f'<m:Items>{item}</m:Items></m:{kind}ResponseMessage></m:ResponseMessages></m:{kind}Response>')


class Recorder:
    def __init__(self, *answers):
        self.answers, self.reqs = list(answers), []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.reqs.append(ET.fromstring(req.content))
        a = self.answers.pop(0) if self.answers else _find("")
        return a(req) if callable(a) else a

    def header_mailbox(self, i=-1):
        return self.reqs[i].findtext(".//t:ExchangeImpersonation/t:ConnectingSID/t:PrimarySmtpAddress", None, NS)


def client(rec, access="impersonation"):
    return EwsCalendar(URL, "svc", "pw", httpx.MockTransport(rec), auth="basic", access=access)


def run(coro):
    return asyncio.run(coro)


D = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
E = datetime(2026, 11, 2, 7, 45, tzinfo=timezone.utc)
ITEM = ('<t:CalendarItem><t:ItemId Id="AAEWS1" ChangeKey="CK1"/><t:Subject>Планёрка</t:Subject><t:Start>2026-11-02T07:00:00Z</t:Start><t:End>2026-11-02T07:45:00Z</t:End>'
        '<t:IsAllDayEvent>false</t:IsAllDayEvent><t:IsCancelled>false</t:IsCancelled><t:Location>https://meet.example.org/m/abcdefghjkmnpqrstuvw</t:Location>'
        '<t:CalendarItemType>RecurringMaster</t:CalendarItemType><t:Organizer><t:Mailbox><t:EmailAddress>Owner@example.org</t:EmailAddress></t:Mailbox></t:Organizer></t:CalendarItem>')


def test_list_parses_events_and_reads_the_requested_mailbox_with_a_window():
    rec = Recorder(_find(ITEM))
    ev = run(client(rec).list_events("alice@example.org", D, E))
    assert len(ev) == 1 and ev[0].id == "AAEWS1" and ev[0].change == "CK1" and ev[0].subject == "Планёрка" and ev[0].kind == "series"
    assert ev[0].location.endswith("/m/abcdefghjkmnpqrstuvw") and ev[0].organizer == "owner@example.org" and ev[0].start == D
    assert rec.header_mailbox() == "alice@example.org"
    cv = rec.reqs[0].find(".//m:CalendarView", NS)
    assert cv.get("StartDate") == "2026-11-02T07:00:00Z" and cv.get("EndDate") == "2026-11-02T07:45:00Z"


def test_read_denied_for_impersonation_falls_back_to_reviewer_access_in_auto_mode():
    rec = Recorder(_find("", "Error", "ErrorImpersonateUserDenied"), _find(ITEM))
    ev = run(EwsCalendar(URL, "svc", "pw", httpx.MockTransport(rec), auth="basic", access="auto").list_events("alice@example.org", D, E))
    assert len(ev) == 1
    assert rec.header_mailbox(0) == "alice@example.org" and rec.header_mailbox(1) is None
    assert rec.reqs[1].findtext(".//t:DistinguishedFolderId/t:Mailbox/t:EmailAddress", None, NS) == "alice@example.org"


def test_create_goes_into_the_users_mailbox_with_invitations_and_utc_time():
    rec = Recorder(_write("CreateItem", item='<t:CalendarItem><t:ItemId Id="NEWID" ChangeKey="CK9"/></t:CalendarItem>'))
    spec = EventSpec("Встреча «А&Б»", D, E, "Europe/Moscow", [("bob@example.org", "Bob", False), ("ext@example.org", None, True)], "<p>Ссылка</p>", "https://meet.example.org/m/x")
    out = run(client(rec).create_event("alice@example.org", spec))
    assert out.id == "NEWID" and out.change == "CK9"
    assert rec.header_mailbox() == "alice@example.org", "создание — только с заголовком имперсонации ящика сотрудника"
    ci = rec.reqs[0].find(".//m:CreateItem", NS)
    assert ci.get("SendMeetingInvitations") == "SendToAllAndSaveCopy"
    it = ci.find(".//t:CalendarItem", NS)
    assert it.findtext("t:Subject", None, NS) == "Встреча «А&Б»" and it.findtext("t:Start", None, NS) == "2026-11-02T07:00:00Z"
    assert it.findtext("t:RequiredAttendees/t:Attendee/t:Mailbox/t:EmailAddress", None, NS) == "bob@example.org"
    assert it.findtext("t:OptionalAttendees/t:Attendee/t:Mailbox/t:EmailAddress", None, NS) == "ext@example.org"
    assert it.find("t:Body", NS).get("BodyType") == "HTML" and "<p>Ссылка</p>" in it.findtext("t:Body", "", NS)


def test_create_without_attendees_sends_nothing_and_recurring_uses_local_time_and_zone():
    rec = Recorder(_write("CreateItem", item='<t:CalendarItem><t:ItemId Id="N1"/></t:CalendarItem>'), _write("CreateItem", item='<t:CalendarItem><t:ItemId Id="N2"/></t:CalendarItem>'))
    c = client(rec)
    run(c.create_event("alice@example.org", EventSpec("Один", D, E)))
    assert rec.reqs[0].find(".//m:CreateItem", NS).get("SendMeetingInvitations") == "SendToNone"
    pat = {"type": "weekly", "interval": 1, "days": [0, 2], "start": "2026-11-02", "end": None, "count": 8, "time": "10:00", "minutes": 45}
    run(c.create_event("alice@example.org", EventSpec("Серия", D, E, "Europe/Moscow", recurrence=pat)))
    it = rec.reqs[1].find(".//t:CalendarItem", NS)
    assert it.findtext("t:Start", None, NS) == "2026-11-02T10:00:00", "время серии — в поясе организатора, чтобы оно не «плыло» при смене сезона"
    assert it.find("t:StartTimeZone", NS).get("Id") == "Russian Standard Time"
    assert it.findtext("t:Recurrence/t:WeeklyRecurrence/t:DaysOfWeek", None, NS) == "Monday Wednesday"
    assert it.findtext("t:Recurrence/t:NumberedRecurrence/t:NumberOfOccurrences", None, NS) == "8"


def test_writing_never_falls_back_to_the_service_accounts_own_calendar():
    # отказ имперсонации при записи — ошибка и ОДИН запрос: повторной попытки без заголовка (она создала бы событие в календаре служебной учётной записи) нет
    rec = Recorder(_write("CreateItem", "Error", "ErrorImpersonateUserDenied"))
    with pytest.raises(CalendarError) as e:
        run(EwsCalendar(URL, "svc", "pw", httpx.MockTransport(rec), auth="basic", access="auto").create_event("alice@example.org", EventSpec("Х", D, E)))
    assert "ApplicationImpersonation" in str(e.value) and len(rec.reqs) == 1 and rec.header_mailbox() == "alice@example.org"


def test_reviewer_mode_cannot_write_at_all():
    rec = Recorder()
    c = client(rec, access="delegate")
    for call in (c.create_event("a@example.org", EventSpec("Х", D, E)), c.cancel_event("a@example.org", "ID"), c.update_event("a@example.org", "ID", None, subject="Н", start=None, end=None)):
        with pytest.raises(CalendarError):
            run(call)
    assert rec.reqs == [], "без имперсонации запись даже не отправляется"


def test_update_uses_change_key_and_reports_conflict():
    rec = Recorder(_write("UpdateItem", item='<t:CalendarItem><t:ItemId Id="AAEWS1" ChangeKey="CK2"/></t:CalendarItem>'), _write("UpdateItem", "Error", "ErrorIrresolvableConflict"))
    c = client(rec)
    out = run(c.update_event("alice@example.org", "AAEWS1", "CK1", subject="Новое", start=D, end=E))
    assert out.change == "CK2"
    ui = rec.reqs[0].find(".//m:UpdateItem", NS)
    assert ui.get("ConflictResolution") == "NeverOverwrite" and ui.find(".//t:ItemId", NS).get("ChangeKey") == "CK1"
    assert {f.get("FieldURI") for f in ui.findall(".//t:FieldURI", NS)} == {"item:Subject", "calendar:Start", "calendar:End"}
    with pytest.raises(CalendarError) as e:
        run(c.update_event("alice@example.org", "AAEWS1", "CK1", subject="Ещё", start=None, end=None))
    assert str(e.value) == "conflict"


def test_cancel_sends_cancellations_and_missing_event_is_not_an_error():
    rec = Recorder(_write("DeleteItem"), _write("DeleteItem", "Error", "ErrorItemNotFound"))
    c = client(rec)
    run(c.cancel_event("alice@example.org", "AAEWS1"))
    d = rec.reqs[0].find(".//m:DeleteItem", NS)
    assert d.get("SendMeetingCancellations") == "SendToAllAndSaveCopy" and rec.header_mailbox(0) == "alice@example.org"
    run(c.cancel_event("alice@example.org", "AAEWS1"))


def test_recurrence_conversion_and_unsupported_patterns():
    p = {"type": "monthly", "interval": 2, "day": 15, "start": "2026-11-15", "end": "2027-05-15", "count": None}
    assert recurrence_to_graph(p, "Europe/Moscow") == {"pattern": {"interval": 2, "type": "absoluteMonthly", "dayOfMonth": 15},
                                                        "range": {"type": "endDate", "startDate": "2026-11-15", "endDate": "2027-05-15", "recurrenceTimeZone": "Europe/Moscow"}}
    assert "<t:AbsoluteMonthlyRecurrence><t:Interval>2</t:Interval><t:DayOfMonth>15</t:DayOfMonth>" in recurrence_to_ews(p)
    assert recurrence_to_graph({"type": "daily", "start": "2026-11-02"}, "UTC")["range"]["type"] == "noEnd"
    for fn in (lambda: recurrence_to_graph({"type": "yearly", "start": "2026-11-02"}, "UTC"), lambda: recurrence_to_ews({"type": "monthly_nth"})):
        with pytest.raises(CalendarError):
            fn()


# ------------------------------------------------------------------------------------------------ календари, карточка, серия, место и участники (EWS)
def _folders(items: str, cls="Success", code="NoError") -> httpx.Response:
    return _resp(f'<m:FindFolderResponse><m:ResponseMessages><m:FindFolderResponseMessage ResponseClass="{cls}"><m:ResponseCode>{code}</m:ResponseCode>'
                 f'<m:RootFolder><t:Folders>{items}</t:Folders></m:RootFolder></m:FindFolderResponseMessage></m:ResponseMessages></m:FindFolderResponse>')


def _get(item: str, cls="Success", code="NoError") -> httpx.Response:
    return _resp(f'<m:GetItemResponse><m:ResponseMessages><m:GetItemResponseMessage ResponseClass="{cls}"><m:ResponseCode>{code}</m:ResponseCode>'
                 f'<m:Items>{item}</m:Items></m:GetItemResponseMessage></m:ResponseMessages></m:GetItemResponse>')


def test_calendars_list_primary_plus_subfolders_with_impersonation_header():
    rec = Recorder(_folders('<t:CalendarFolder><t:FolderId Id="FPROJ"/><t:DisplayName>Проекты</t:DisplayName></t:CalendarFolder>'
                            '<t:CalendarFolder><t:FolderId Id="FARCH"/><t:DisplayName>Архив</t:DisplayName></t:CalendarFolder>'))
    cals = run(client(rec).list_calendars("alice@example.org"))
    assert [(c.id, c.name, c.default) for c in cals] == [("primary", "Календарь", True), ("FARCH", "Архив", False), ("FPROJ", "Проекты", False)]
    assert rec.header_mailbox() == "alice@example.org" and rec.reqs[0].find(".//m:FindFolder", NS).get("Traversal") == "Deep"
    with pytest.raises(CalendarError):
        run(client(Recorder(_folders("", "Error", "ErrorImpersonateUserDenied"))).list_calendars("alice@example.org"))


def test_list_events_in_a_chosen_folder_and_busy_status():
    item = ITEM.replace("<t:CalendarItemType>", "<t:LegacyFreeBusyStatus>Free</t:LegacyFreeBusyStatus><t:CalendarItemType>")
    rec = Recorder(_find(item))
    ev = run(client(rec).list_events("alice@example.org", D, E, "FPROJ"))
    assert ev[0].show_as == "free" and ev[0].calendar_id == "FPROJ"
    assert rec.reqs[0].find(".//m:ParentFolderIds/t:FolderId", NS).get("Id") == "FPROJ"
    assert "calendar:LegacyFreeBusyStatus" in {f.get("FieldURI") for f in rec.reqs[0].findall(".//t:FieldURI", NS)}


def test_get_event_parses_attendees_organizer_and_text_body():
    item = ('<t:CalendarItem><t:ItemId Id="OCC1" ChangeKey="CK5"/><t:Subject>Планёрка</t:Subject><t:Body BodyType="Text">Повестка: бюджет</t:Body><t:Start>2026-11-02T07:00:00Z</t:Start>'
            '<t:End>2026-11-02T07:45:00Z</t:End><t:IsAllDayEvent>false</t:IsAllDayEvent><t:IsCancelled>false</t:IsCancelled><t:Location>Каб. 5</t:Location>'
            '<t:CalendarItemType>Occurrence</t:CalendarItemType><t:LegacyFreeBusyStatus>Busy</t:LegacyFreeBusyStatus>'
            '<t:Organizer><t:Mailbox><t:Name>Алиса</t:Name><t:EmailAddress>Alice@example.org</t:EmailAddress></t:Mailbox></t:Organizer>'
            '<t:RequiredAttendees><t:Attendee><t:Mailbox><t:Name>Bob</t:Name><t:EmailAddress>Bob@example.org</t:EmailAddress></t:Mailbox><t:ResponseType>Accept</t:ResponseType></t:Attendee></t:RequiredAttendees>'
            '<t:OptionalAttendees><t:Attendee><t:Mailbox><t:EmailAddress>ext@example.org</t:EmailAddress></t:Mailbox></t:Attendee></t:OptionalAttendees></t:CalendarItem>')
    rec = Recorder(_get(item))
    d = run(client(rec).get_event("alice@example.org", "OCC1"))
    assert d.event.kind == "occurrence" and d.event.organizer == "alice@example.org" and d.organizer_name == "Алиса" and d.body == "Повестка: бюджет"
    assert [(a["email"], a["type"], a["response"]) for a in d.attendees] == [("bob@example.org", "required", "accept"), ("ext@example.org", "optional", "unknown")]
    assert rec.reqs[0].findtext(".//t:BodyType", None, NS) == "Text" and rec.header_mailbox() == "alice@example.org"


def test_master_id_of_an_occurrence_uses_recurring_master_item_id():
    rec = Recorder(_get('<t:CalendarItem><t:ItemId Id="MASTER9" ChangeKey="K"/></t:CalendarItem>'), _get("", "Error", "ErrorItemNotFound"))
    c = client(rec)
    assert run(c.master_id("alice@example.org", "OCC1")) == "MASTER9"
    assert rec.reqs[0].find(".//t:RecurringMasterItemId", NS).get("OccurrenceId") == "OCC1"
    assert run(c.master_id("alice@example.org", "SINGLE")) is None


def test_update_sets_location_and_replaces_attendees_in_both_lists():
    rec = Recorder(_write("UpdateItem", item='<t:CalendarItem><t:ItemId Id="A1" ChangeKey="CK3"/></t:CalendarItem>'))
    run(client(rec).update_event("alice@example.org", "A1", "CK1", subject=None, start=None, end=None, location="Каб. 7", attendees=[("bob@example.org", "Bob", False)]))
    ui = rec.reqs[0].find(".//m:UpdateItem", NS)
    fields = {f.get("FieldURI") for f in ui.findall(".//t:FieldURI", NS)}
    assert fields == {"calendar:Location", "calendar:RequiredAttendees", "calendar:OptionalAttendees"}
    assert ui.findtext(".//t:SetItemField/t:CalendarItem/t:Location", None, NS) == "Каб. 7"
    assert ui.findtext(".//t:RequiredAttendees/t:Attendee/t:Mailbox/t:EmailAddress", None, NS) == "bob@example.org"
    assert ui.find(".//t:DeleteItemField/t:FieldURI", NS).get("FieldURI") == "calendar:OptionalAttendees", "пустой список необязательных — поле очищается"
