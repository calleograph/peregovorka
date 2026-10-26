"""Чтение и ведение календаря сотрудника в Exchange (Microsoft Graph и EWS) для раздела «Календарь».

Тот же серверный вход, что у серверной сверки (`outlook_sync`): Graph — приложение Entra ID, EWS — учётная запись с ApplicationImpersonation (или «Рецензент» только для чтения).
Здесь только обмен с Exchange; решения «можно ли этому сотруднику» (согласие, группы, права) принимает `calendar_svc`. Секреты в ошибки и журнал не попадают.

Что НЕ проверено на настоящем Exchange: запросы построены по документации Microsoft и проверены на подставных ответах (tests/test_calendar_exchange.py); перед включением в организации
администратор нажимает «Проверить мой календарь» — там видны отказ прав или неверный адрес. Подробности — docs/OUTLOOK_CALENDAR.md.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from xml.sax.saxutils import escape

from .outlook_sync import NS, CalendarError, EwsClient, GraphClient, _DENIED, _envelope, _utc
from .windows_zones import WINDOWS_TO_IANA

log = logging.getLogger("app.calendar")
MAX_EVENTS = 500
MAX_PAGES = 5
_IANA_TO_WINDOWS: dict[str, str] = {}
for _win, _iana in WINDOWS_TO_IANA.items():
    _IANA_TO_WINDOWS.setdefault(_iana, _win)

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_EWS_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class CalEvent:
    id: str
    subject: str
    start: datetime
    end: datetime
    change: str | None = None            # etag (Graph) или ChangeKey (EWS): защита от перезаписи чужой правки
    all_day: bool = False
    cancelled: bool = False
    kind: str = "single"                 # single | series | occurrence | exception
    location: str = ""
    organizer: str = ""
    series_id: str | None = None
    show_as: str = "busy"                # free | tentative | busy | oof | workingElsewhere (занятость на это время)
    calendar_id: str | None = None       # какой из календарей ящика (None — основной)
    meeting: bool = False                # собрание с участниками (а не личная запись в календаре)


@dataclass
class CalendarInfo:
    id: str                              # "primary" — основной календарь ящика
    name: str
    default: bool = False
    can_edit: bool = True


@dataclass
class EventDetail:
    event: CalEvent
    organizer_name: str = ""
    attendees: list[dict] = field(default_factory=list)      # {email, name, type: required|optional, response}
    body: str = ""                                          # описание текстом (без разметки), не длиннее MAX_BODY
    web_link: str | None = None
    sensitivity: str = "normal"


MAX_BODY = 4000
_SHOW_AS = {"free", "tentative", "busy", "oof", "workingElsewhere"}


def _plain(text: str) -> str:
    """Описание события: только текст, без разметки и лишних пробелов (в интерфейсе показывается как текст, не как HTML)."""
    import html as _html  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    t = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=_re.S | _re.I)
    t = _re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", t, flags=_re.I)
    t = _re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t)
    t = _re.sub(r"[ \t\r\f\v]+", " ", t)
    return _re.sub(r"\n\s*\n+", "\n\n", t).strip()[:MAX_BODY]


@dataclass
class EventSpec:
    subject: str
    start: datetime                      # UTC
    end: datetime                        # UTC
    tz: str = "UTC"                      # пояс организатора (IANA): для серий время в этом поясе не «плывёт» при смене сезонного времени
    attendees: list[tuple[str, str | None, bool]] = field(default_factory=list)      # (адрес, имя, необязательный)
    body_html: str = ""
    location: str = ""
    recurrence: dict | None = None       # шаблон в формате recurrence.validate
    key: str = ""                        # ключ повтора: Graph не создаёт второе событие с тем же transactionId


@dataclass
class Created:
    id: str
    change: str | None = None
    web_link: str | None = None


# ------------------------------------------------------------------------------------------------ шаблон повторения
def recurrence_to_graph(p: dict, tz: str) -> dict:
    """Шаблон Peregovorka (recurrence.validate) → patternedRecurrence Microsoft Graph. Поддерживаются ежедневное, еженедельное и ежемесячное по числу."""
    t = p.get("type")
    pat: dict[str, Any] = {"interval": int(p.get("interval") or 1)}
    if t == "daily":
        pat["type"] = "daily"
    elif t == "weekly":
        pat.update(type="weekly", daysOfWeek=[_DAYS[d] for d in p.get("days") or []], firstDayOfWeek="monday")
    elif t == "monthly":
        pat.update(type="absoluteMonthly", dayOfMonth=int(p["day"]))
    else:
        raise CalendarError("Такое повторение из Peregovorka пока не создаётся: выберите «каждый день», «каждую неделю» или «каждый месяц».")
    if p.get("count"):
        rng: dict[str, Any] = {"type": "numbered", "startDate": p["start"], "numberOfOccurrences": int(p["count"])}
    elif p.get("end"):
        rng = {"type": "endDate", "startDate": p["start"], "endDate": p["end"]}
    else:
        rng = {"type": "noEnd", "startDate": p["start"]}
    rng["recurrenceTimeZone"] = tz
    return {"pattern": pat, "range": rng}


def recurrence_to_ews(p: dict) -> str:
    t = p.get("type")
    iv = int(p.get("interval") or 1)
    if t == "daily":
        pat = f'<t:DailyRecurrence><t:Interval>{iv}</t:Interval></t:DailyRecurrence>'
    elif t == "weekly":
        pat = f'<t:WeeklyRecurrence><t:Interval>{iv}</t:Interval><t:DaysOfWeek>{" ".join(_EWS_DAYS[d] for d in p.get("days") or [])}</t:DaysOfWeek></t:WeeklyRecurrence>'
    elif t == "monthly":
        pat = f'<t:AbsoluteMonthlyRecurrence><t:Interval>{iv}</t:Interval><t:DayOfMonth>{int(p["day"])}</t:DayOfMonth></t:AbsoluteMonthlyRecurrence>'
    else:
        raise CalendarError("Такое повторение из Peregovorka пока не создаётся: выберите «каждый день», «каждую неделю» или «каждый месяц».")
    if p.get("count"):
        rng = f'<t:NumberedRecurrence><t:StartDate>{p["start"]}</t:StartDate><t:NumberOfOccurrences>{int(p["count"])}</t:NumberOfOccurrences></t:NumberedRecurrence>'
    elif p.get("end"):
        rng = f'<t:EndDateRecurrence><t:StartDate>{p["start"]}</t:StartDate><t:EndDate>{p["end"]}</t:EndDate></t:EndDateRecurrence>'
    else:
        rng = f'<t:NoEndRecurrence><t:StartDate>{p["start"]}</t:StartDate></t:NoEndRecurrence>'
    return f"<t:Recurrence>{pat}{rng}</t:Recurrence>"


def _local(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------------------------------------ Microsoft Graph
class GraphCalendar(GraphClient):
    """Календарь через Graph: чтение `calendarView` (Calendars.ReadBasic.All достаточно) и ведение событий (нужно право приложения Calendars.ReadWrite)."""

    async def _req(self, method: str, mailbox: str, path: str, *, params: dict | None = None, json: Any = None, extra: dict | None = None, absolute: str | None = None) -> httpx.Response:
        try:
            async with self._http() as c:
                h = {"Authorization": f"Bearer {await self._token(c)}", "Prefer": 'outlook.timezone="UTC"', **(extra or {})}
                url = absolute or f"{self._api}/users/{quote(mailbox)}{path}"
                return await c.request(method, url, params=params, json=json, headers=h)
        except httpx.HTTPError as exc:
            raise CalendarError(f"Нет связи с Microsoft Graph ({type(exc).__name__}).") from None

    @staticmethod
    def _fail(r: httpx.Response, write: bool = False) -> CalendarError:
        if r.status_code in (401, 403):
            need = "Calendars.ReadWrite" if write else "Calendars.ReadBasic.All"
            return CalendarError(f"Graph отказал в доступе к календарю: выдайте приложению право {need} (с согласием администратора). Если доступ ограничен политикой Application Access Policy, добавьте ящик сотрудника в разрешённую группу.")
        if r.status_code == 404:
            return CalendarError("Почтовый ящик или событие не найдены.")
        if r.status_code == 412:
            return CalendarError("conflict")
        if r.status_code == 429:
            return CalendarError("Exchange просит подождать (слишком много запросов). Повторите через минуту.")
        return CalendarError(f"Graph ответил HTTP {r.status_code}.")

    @staticmethod
    def _event(j: dict) -> CalEvent | None:
        try:
            kind = {"singleInstance": "single", "seriesMaster": "series", "occurrence": "occurrence", "exception": "exception"}.get(str(j.get("type")), "single")
            loc = (j.get("location") or {}).get("displayName") or ""
            org = ((j.get("organizer") or {}).get("emailAddress") or {}).get("address") or ""
            show = str(j.get("showAs") or "busy")
            return CalEvent(str(j["id"]), str(j.get("subject") or ""), _utc(j["start"]["dateTime"]), _utc(j["end"]["dateTime"]), j.get("@odata.etag"), bool(j.get("isAllDay")),
                            bool(j.get("isCancelled")), kind, str(loc)[:300], str(org).lower(), j.get("seriesMasterId"), show if show in _SHOW_AS else "busy",
                            None, bool(j.get("attendees")))
        except (KeyError, TypeError, ValueError):
            return None

    async def list_calendars(self, mailbox: str) -> list[CalendarInfo]:
        r = await self._req("GET", mailbox, "/calendars", params={"$select": "id,name,isDefaultCalendar,canEdit", "$top": "50"})
        if r.status_code != 200:
            raise self._fail(r)
        out = [CalendarInfo("primary" if x.get("isDefaultCalendar") else str(x["id"]), str(x.get("name") or "Календарь")[:120], bool(x.get("isDefaultCalendar")), bool(x.get("canEdit", True)))
               for x in r.json().get("value") or [] if x.get("id")]
        out.sort(key=lambda c: (not c.default, c.name.lower()))
        return out

    async def list_events(self, mailbox: str, start: datetime, end: datetime, calendar_id: str | None = None) -> list[CalEvent]:
        out: list[CalEvent] = []
        params: dict[str, Any] | None = {"startDateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "endDateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                          "$select": "id,subject,start,end,isAllDay,isCancelled,type,seriesMasterId,location,organizer,showAs,attendees", "$top": "100", "$orderby": "start/dateTime"}
        path = "/calendarView" if not calendar_id or calendar_id == "primary" else f"/calendars/{quote(calendar_id, safe='')}/calendarView"
        url: str | None = None
        for _ in range(MAX_PAGES):
            r = await self._req("GET", mailbox, path, params=params, absolute=url)
            if r.status_code != 200:
                raise self._fail(r)
            j = r.json()
            out += [e for e in (self._event(x) for x in j.get("value") or []) if e]
            url, params = j.get("@odata.nextLink"), None
            if not url or len(out) >= MAX_EVENTS:
                break
        for e in out:
            e.calendar_id = calendar_id or "primary"
        return out[:MAX_EVENTS]

    async def get_event(self, mailbox: str, event_id: str) -> EventDetail:
        r = await self._req("GET", mailbox, f"/events/{quote(event_id, safe='')}", params={"$select": "id,subject,start,end,isAllDay,isCancelled,type,seriesMasterId,location,organizer,showAs,attendees,body,webLink,sensitivity"},
                            extra={"Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"'})
        if r.status_code != 200:
            raise self._fail(r)
        j = r.json()
        ev = self._event(j)
        if ev is None:
            raise CalendarError("Exchange вернул событие в непонятном виде.")
        att = [{"email": str((a.get("emailAddress") or {}).get("address") or "").lower(), "name": str((a.get("emailAddress") or {}).get("name") or "")[:120],
                "type": "optional" if a.get("type") == "optional" else "required", "response": str((a.get("status") or {}).get("response") or "none")} for a in j.get("attendees") or []][:MAX_EVENTS]
        body = j.get("body") or {}
        text = str(body.get("content") or "")
        return EventDetail(ev, str(((j.get("organizer") or {}).get("emailAddress") or {}).get("name") or "")[:120], [a for a in att if a["email"]],
                           _plain(text) if str(body.get("contentType")).lower() == "html" else text.strip()[:MAX_BODY], j.get("webLink"), str(j.get("sensitivity") or "normal"))

    async def master_id(self, mailbox: str, event_id: str) -> str | None:
        """Идентификатор серии для занятия (Graph отдаёт его в самом событии)."""
        r = await self._req("GET", mailbox, f"/events/{quote(event_id, safe='')}", params={"$select": "id,seriesMasterId,type"})
        if r.status_code != 200:
            raise self._fail(r)
        j = r.json()
        return str(j["seriesMasterId"]) if j.get("seriesMasterId") else (str(j["id"]) if j.get("type") == "seriesMaster" else None)

    def _payload(self, s: EventSpec) -> dict:
        body: dict[str, Any] = {
            "subject": s.subject, "start": {"dateTime": _local(s.start, s.tz), "timeZone": s.tz}, "end": {"dateTime": _local(s.end, s.tz), "timeZone": s.tz},
            "attendees": [{"emailAddress": {"address": a, **({"name": n} if n else {})}, "type": "optional" if opt else "required"} for a, n, opt in s.attendees],
            "body": {"contentType": "html", "content": s.body_html}, "location": {"displayName": s.location}, "allowNewTimeProposals": True,
        }
        if s.recurrence:
            body["recurrence"] = recurrence_to_graph(s.recurrence, s.tz)
        if s.key:
            body["transactionId"] = s.key[:40]
        return body

    async def create_event(self, mailbox: str, s: EventSpec) -> Created:
        r = await self._req("POST", mailbox, "/events", json=self._payload(s))
        if r.status_code not in (200, 201):
            raise self._fail(r, write=True)
        j = r.json()
        return Created(str(j["id"]), j.get("@odata.etag"), j.get("webLink"))

    async def update_event(self, mailbox: str, event_id: str, change: str | None, *, subject: str | None, start: datetime | None, end: datetime | None, tz: str = "UTC",
                           location: str | None = None, attendees: list[tuple[str, str | None, bool]] | None = None) -> Created:
        patch: dict[str, Any] = {}
        if subject is not None:
            patch["subject"] = subject
        if location is not None:
            patch["location"] = {"displayName": location}
        if attendees is not None:
            patch["attendees"] = [{"emailAddress": {"address": a, **({"name": n} if n else {})}, "type": "optional" if opt else "required"} for a, n, opt in attendees]
        if start is not None and end is not None:
            patch["start"] = {"dateTime": _local(start, tz), "timeZone": tz}
            patch["end"] = {"dateTime": _local(end, tz), "timeZone": tz}
        r = await self._req("PATCH", mailbox, f"/events/{quote(event_id, safe='')}", json=patch, extra={"If-Match": change} if change else None)
        if r.status_code != 200:
            raise self._fail(r, write=True)
        j = r.json()
        return Created(str(j.get("id") or event_id), j.get("@odata.etag"), j.get("webLink"))

    async def cancel_event(self, mailbox: str, event_id: str, comment: str = "") -> None:
        path = f"/events/{quote(event_id, safe='')}"
        r = await self._req("POST", mailbox, path + "/cancel", json={"comment": comment[:500]})
        if r.status_code in (200, 202, 204):
            return
        if r.status_code in (400, 404, 405):          # событие без участников отменить «письмом» нельзя — удаляется
            d = await self._req("DELETE", mailbox, path)
            if d.status_code in (200, 202, 204):
                return
            if d.status_code == 404:
                return
            raise self._fail(d, write=True)
        raise self._fail(r, write=True)


# ------------------------------------------------------------------------------------------------ EWS
def _ews_code(root: ET.Element, kind: str) -> tuple[str, str]:
    msg = root.find(f".//m:{kind}ResponseMessage", NS)
    if msg is None:
        return "Error", "NoResponse"
    return msg.get("ResponseClass", "Error"), (msg.findtext("m:ResponseCode", "", NS) or "")


class EwsCalendar(EwsClient):
    """Календарь через EWS: чтение — имперсонация или «Рецензент»; создание и изменение — только имперсонация (роль ApplicationImpersonation).
    Запись всегда идёт с заголовком имперсонации конкретного ящика и никогда не переключается на «делегированный» путь: без заголовка событие попало бы в календарь самой серверной учётной записи."""

    @staticmethod
    def _code(root: ET.Element) -> tuple[str, str]:
        for kind in ("FindItem", "GetItem", "GetFolder", "FindFolder"):
            msg = root.find(f".//m:{kind}ResponseMessage", NS)
            if msg is not None:
                return msg.get("ResponseClass", "Error"), (msg.findtext("m:ResponseCode", "", NS) or "")
        return "Error", "NoResponse"

    async def _write(self, mailbox: str, kind: str, body: str) -> tuple[ET.Element, str, str]:
        if self._access == "delegate":
            raise CalendarError("Создавать и менять встречи сотрудников можно только в режиме «Имперсонация» (роль ApplicationImpersonation): право «Рецензент» даёт лишь чтение.")
        root = await self._post(_envelope(body, mailbox))
        cls, code = _ews_code(root, kind)
        return root, cls, code

    @staticmethod
    def _folder(mailbox: str, mode: str, calendar_id: str | None) -> str:
        if calendar_id and calendar_id != "primary":
            return f'<t:FolderId Id="{escape(calendar_id, {chr(34): "&quot;"})}"/>'
        return ('<t:DistinguishedFolderId Id="calendar"/>' if mode == "impersonation" else
                f'<t:DistinguishedFolderId Id="calendar"><t:Mailbox><t:EmailAddress>{escape(mailbox)}</t:EmailAddress></t:Mailbox></t:DistinguishedFolderId>')

    async def list_calendars(self, mailbox: str) -> list[CalendarInfo]:
        """Основной календарь и дополнительные календари ящика (подпапки календаря с классом IPF.Appointment)."""
        def body_for(mode: str) -> str:
            return ('<m:FindFolder Traversal="Deep"><m:FolderShape><t:BaseShape>IdOnly</t:BaseShape><t:AdditionalProperties><t:FieldURI FieldURI="folder:DisplayName"/>'
                    '<t:FieldURI FieldURI="folder:FolderClass"/></t:AdditionalProperties></m:FolderShape>'
                    f'<m:ParentFolderIds>{self._folder(mailbox, mode, None)}</m:ParentFolderIds></m:FindFolder>')

        root, cls, code, mode = await self._with_mode(body_for, mailbox)
        if cls != "Success":
            if code in _DENIED:
                raise self._denied(mode)
            raise CalendarError(f"EWS ответил ошибкой {code or 'неизвестной'}.")
        out = [CalendarInfo("primary", "Календарь", True, True)]
        for f in root.findall(".//t:CalendarFolder", NS):
            fid = f.find("t:FolderId", NS)
            if fid is None or not fid.get("Id"):
                continue
            out.append(CalendarInfo(fid.get("Id", ""), (f.findtext("t:DisplayName", "", NS) or "Календарь")[:120], False, True))
        out[1:] = sorted(out[1:], key=lambda c: c.name.lower())
        return out

    async def list_events(self, mailbox: str, start: datetime, end: datetime, calendar_id: str | None = None) -> list[CalEvent]:
        fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

        def body_for(mode: str) -> str:
            folder = self._folder(mailbox, mode, calendar_id)
            return ('<m:FindItem Traversal="Shallow"><m:ItemShape><t:BaseShape>IdOnly</t:BaseShape><t:AdditionalProperties><t:FieldURI FieldURI="item:Subject"/>'
                    '<t:FieldURI FieldURI="calendar:Start"/><t:FieldURI FieldURI="calendar:End"/><t:FieldURI FieldURI="calendar:IsAllDayEvent"/><t:FieldURI FieldURI="calendar:IsCancelled"/>'
                    '<t:FieldURI FieldURI="calendar:Location"/><t:FieldURI FieldURI="calendar:CalendarItemType"/><t:FieldURI FieldURI="calendar:Organizer"/>'
                    '<t:FieldURI FieldURI="calendar:LegacyFreeBusyStatus"/><t:FieldURI FieldURI="calendar:IsMeeting"/></t:AdditionalProperties></m:ItemShape>'
                    f'<m:CalendarView MaxEntriesReturned="{MAX_EVENTS}" StartDate="{fmt(start)}" EndDate="{fmt(end)}"/><m:ParentFolderIds>{folder}</m:ParentFolderIds></m:FindItem>')

        root, cls, code, mode = await self._with_mode(body_for, mailbox)
        if cls != "Success":
            if code in _DENIED:
                raise self._denied(mode)
            raise CalendarError(f"EWS ответил ошибкой {code or 'неизвестной'}.")
        out: list[CalEvent] = []
        for it in root.findall(".//t:CalendarItem", NS):
            ev = self._parse(it)
            if ev is not None:
                ev.calendar_id = calendar_id or "primary"
                out.append(ev)
        return out

    @staticmethod
    def _parse(it: ET.Element) -> CalEvent | None:
        try:
            iid = it.find("t:ItemId", NS)
            kind = {"Single": "single", "RecurringMaster": "series", "Occurrence": "occurrence", "Exception": "exception"}.get(it.findtext("t:CalendarItemType", "", NS), "single")
            show = {"Free": "free", "Tentative": "tentative", "Busy": "busy", "OOF": "oof", "WorkingElsewhere": "workingElsewhere"}.get(it.findtext("t:LegacyFreeBusyStatus", "Busy", NS), "busy")
            return CalEvent(iid.get("Id", ""), it.findtext("t:Subject", "", NS) or "", _utc(it.findtext("t:Start", "", NS)), _utc(it.findtext("t:End", "", NS)), iid.get("ChangeKey"),
                            (it.findtext("t:IsAllDayEvent", "false", NS) or "").lower() == "true", (it.findtext("t:IsCancelled", "false", NS) or "").lower() == "true", kind,
                            (it.findtext("t:Location", "", NS) or "")[:300], (it.findtext("t:Organizer/t:Mailbox/t:EmailAddress", "", NS) or "").lower(), None, show, None,
                            (it.findtext("t:IsMeeting", "false", NS) or "").lower() == "true")
        except (AttributeError, ValueError):
            return None

    async def get_event(self, mailbox: str, event_id: str) -> EventDetail:
        eid = escape(event_id, {chr(34): "&quot;"})

        def body_for(_mode: str) -> str:
            return ('<m:GetItem><m:ItemShape><t:BaseShape>IdOnly</t:BaseShape><t:BodyType>Text</t:BodyType><t:AdditionalProperties><t:FieldURI FieldURI="item:Subject"/>'
                    '<t:FieldURI FieldURI="calendar:Start"/><t:FieldURI FieldURI="calendar:End"/><t:FieldURI FieldURI="calendar:IsAllDayEvent"/><t:FieldURI FieldURI="calendar:IsCancelled"/>'
                    '<t:FieldURI FieldURI="calendar:Location"/><t:FieldURI FieldURI="calendar:CalendarItemType"/><t:FieldURI FieldURI="calendar:Organizer"/>'
                    '<t:FieldURI FieldURI="calendar:RequiredAttendees"/><t:FieldURI FieldURI="calendar:OptionalAttendees"/><t:FieldURI FieldURI="calendar:LegacyFreeBusyStatus"/>'
                    f'<t:FieldURI FieldURI="item:Body"/></t:AdditionalProperties></m:ItemShape><m:ItemIds><t:ItemId Id="{eid}"/></m:ItemIds></m:GetItem>')

        root, cls, code, mode = await self._with_mode(body_for, mailbox)
        if cls != "Success":
            if code in _DENIED:
                raise self._denied(mode)
            if code == "ErrorItemNotFound":
                raise CalendarError("Почтовый ящик или событие не найдены.")
            raise CalendarError(f"EWS ответил ошибкой {code or 'неизвестной'}.")
        it = root.find(".//t:CalendarItem", NS)
        ev = self._parse(it) if it is not None else None
        if ev is None:
            raise CalendarError("Exchange вернул событие в непонятном виде.")
        att: list[dict] = []
        for tag, kind in (("RequiredAttendees", "required"), ("OptionalAttendees", "optional")):
            for a in it.findall(f"t:{tag}/t:Attendee", NS):
                email = (a.findtext("t:Mailbox/t:EmailAddress", "", NS) or "").lower()
                if email:
                    att.append({"email": email, "name": (a.findtext("t:Mailbox/t:Name", "", NS) or "")[:120], "type": kind, "response": (a.findtext("t:ResponseType", "Unknown", NS) or "none").lower()})
        body = it.find("t:Body", NS)
        text = (body.text or "") if body is not None else ""
        return EventDetail(ev, (it.findtext("t:Organizer/t:Mailbox/t:Name", "", NS) or "")[:120], att[:MAX_EVENTS], text.strip()[:MAX_BODY] if (body is None or body.get("BodyType") != "HTML") else _plain(text))

    async def master_id(self, mailbox: str, event_id: str) -> str | None:
        """Идентификатор серии для занятия: EWS отдаёт мастер-событие по RecurringMasterItemId."""
        eid = escape(event_id, {chr(34): "&quot;"})

        def body_for(_mode: str) -> str:
            return (f'<m:GetItem><m:ItemShape><t:BaseShape>IdOnly</t:BaseShape><t:AdditionalProperties><t:FieldURI FieldURI="calendar:CalendarItemType"/></t:AdditionalProperties></m:ItemShape>'
                    f'<m:ItemIds><t:RecurringMasterItemId OccurrenceId="{eid}"/></m:ItemIds></m:GetItem>')

        root, cls, code, mode = await self._with_mode(body_for, mailbox)
        if cls != "Success":
            if code in _DENIED:
                raise self._denied(mode)
            return None                       # событие не из серии или серия удалена
        iid = root.find(".//t:CalendarItem/t:ItemId", NS)
        return iid.get("Id") if iid is not None else None

    def _item_xml(self, s: EventSpec) -> str:
        tzid = _IANA_TO_WINDOWS.get(s.tz)
        local_times = bool(s.recurrence) and bool(tzid)
        start = _local(s.start, s.tz) if local_times else s.start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = _local(s.end, s.tz) if local_times else s.end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        req = "".join(f'<t:Attendee><t:Mailbox>{f"<t:Name>{escape(n)}</t:Name>" if n else ""}<t:EmailAddress>{escape(a)}</t:EmailAddress></t:Mailbox></t:Attendee>' for a, n, opt in s.attendees if not opt)
        opt = "".join(f'<t:Attendee><t:Mailbox>{f"<t:Name>{escape(n)}</t:Name>" if n else ""}<t:EmailAddress>{escape(a)}</t:EmailAddress></t:Mailbox></t:Attendee>' for a, n, o in s.attendees if o)
        return ("<t:CalendarItem>"
                f"<t:Subject>{escape(s.subject)}</t:Subject><t:Body BodyType=\"HTML\">{escape(s.body_html)}</t:Body>"
                f"<t:Start>{start}</t:Start><t:End>{end}</t:End><t:Location>{escape(s.location)}</t:Location>"
                + (f"<t:RequiredAttendees>{req}</t:RequiredAttendees>" if req else "") + (f"<t:OptionalAttendees>{opt}</t:OptionalAttendees>" if opt else "")
                + (recurrence_to_ews(s.recurrence) if s.recurrence else "")
                + (f'<t:StartTimeZone Id="{escape(tzid)}"/><t:EndTimeZone Id="{escape(tzid)}"/>' if local_times else "")
                + "</t:CalendarItem>")

    async def create_event(self, mailbox: str, s: EventSpec) -> Created:
        send = "SendToAllAndSaveCopy" if s.attendees else "SendToNone"
        body = f'<m:CreateItem SendMeetingInvitations="{send}"><m:SavedItemFolderId><t:DistinguishedFolderId Id="calendar"/></m:SavedItemFolderId><m:Items>{self._item_xml(s)}</m:Items></m:CreateItem>'
        root, cls, code = await self._write(mailbox, "CreateItem", body)
        if cls == "Success":
            iid = root.find(".//t:CalendarItem/t:ItemId", NS)
            if iid is None:
                raise CalendarError("EWS не вернул идентификатор созданного события.")
            return Created(iid.get("Id", ""), iid.get("ChangeKey"))
        if code in _DENIED:
            raise self._denied("impersonation")
        raise CalendarError(f"EWS не создал событие: {code or 'неизвестная ошибка'}.")

    async def update_event(self, mailbox: str, event_id: str, change: str | None, *, subject: str | None, start: datetime | None, end: datetime | None, tz: str = "UTC",
                           location: str | None = None, attendees: list[tuple[str, str | None, bool]] | None = None) -> Created:
        ups = []
        if location is not None:
            ups.append(f'<t:SetItemField><t:FieldURI FieldURI="calendar:Location"/><t:CalendarItem><t:Location>{escape(location)}</t:Location></t:CalendarItem></t:SetItemField>')
        if attendees is not None:
            for field_, want in (("RequiredAttendees", False), ("OptionalAttendees", True)):
                rows = [(a, n) for a, n, opt in attendees if opt == want]
                if rows:
                    xml = "".join(f'<t:Attendee><t:Mailbox>{f"<t:Name>{escape(n)}</t:Name>" if n else ""}<t:EmailAddress>{escape(a)}</t:EmailAddress></t:Mailbox></t:Attendee>' for a, n in rows)
                    ups.append(f'<t:SetItemField><t:FieldURI FieldURI="calendar:{field_}"/><t:CalendarItem><t:{field_}>{xml}</t:{field_}></t:CalendarItem></t:SetItemField>')
                else:
                    ups.append(f'<t:DeleteItemField><t:FieldURI FieldURI="calendar:{field_}"/></t:DeleteItemField>')
        if subject is not None:
            ups.append(f'<t:SetItemField><t:FieldURI FieldURI="item:Subject"/><t:CalendarItem><t:Subject>{escape(subject)}</t:Subject></t:CalendarItem></t:SetItemField>')
        if start is not None and end is not None:
            f = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
            ups.append(f'<t:SetItemField><t:FieldURI FieldURI="calendar:Start"/><t:CalendarItem><t:Start>{f(start)}</t:Start></t:CalendarItem></t:SetItemField>')
            ups.append(f'<t:SetItemField><t:FieldURI FieldURI="calendar:End"/><t:CalendarItem><t:End>{f(end)}</t:End></t:CalendarItem></t:SetItemField>')
        ck = f' ChangeKey="{escape(change, {chr(34): "&quot;"})}"' if change else ""
        body = ('<m:UpdateItem ConflictResolution="NeverOverwrite" MessageDisposition="SaveOnly" SendMeetingInvitationsOrCancellations="SendToAllAndSaveCopy"><m:ItemChanges><t:ItemChange>'
                f'<t:ItemId Id="{escape(event_id, {chr(34): "&quot;"})}"{ck}/><t:Updates>{"".join(ups)}</t:Updates></t:ItemChange></m:ItemChanges></m:UpdateItem>')
        root, cls, code = await self._write(mailbox, "UpdateItem", body)
        if cls == "Success":
            iid = root.find(".//t:CalendarItem/t:ItemId", NS)
            return Created(iid.get("Id", event_id) if iid is not None else event_id, iid.get("ChangeKey") if iid is not None else None)
        if code in ("ErrorIrresolvableConflict", "ErrorStaleObject"):
            raise CalendarError("conflict")
        if code in _DENIED:
            raise self._denied("impersonation")
        raise CalendarError(f"EWS не изменил событие: {code or 'неизвестная ошибка'}.")

    async def cancel_event(self, mailbox: str, event_id: str, comment: str = "") -> None:
        body = ('<m:DeleteItem DeleteType="MoveToDeletedItems" SendMeetingCancellations="SendToAllAndSaveCopy"><m:ItemIds>'
                f'<t:ItemId Id="{escape(event_id, {chr(34): "&quot;"})}"/></m:ItemIds></m:DeleteItem>')
        root, cls, code = await self._write(mailbox, "DeleteItem", body)
        if cls == "Success" or code == "ErrorItemNotFound":
            return
        if code in _DENIED:
            raise self._denied("impersonation")
        raise CalendarError(f"EWS не удалил событие: {code or 'неизвестная ошибка'}.")


def make_calendar_client(cfg: Any, transport: httpx.AsyncBaseTransport | None = None):
    """Клиент календаря по настройкам сверки: те же подключение и учётные данные, что у `outlook_sync.make_client`, но с чтением списка и записью."""
    from . import outlook_sync as osync  # noqa: PLC0415

    base = osync.make_client(cfg, transport)
    if isinstance(base, GraphClient):
        return GraphCalendar(cfg.graph_tenant_id, cfg.graph_client_id, cfg.graph_client_secret, transport)
    if isinstance(base, EwsClient):
        return EwsCalendar(cfg.ews_url, cfg.ews_user, cfg.ews_password, transport, auth=getattr(cfg, "ews_auth", "auto"), domain=getattr(cfg, "ews_domain", ""),
                           access=getattr(cfg, "ews_access", "auto"), realm=getattr(cfg, "ews_kerberos_realm", ""), kdc=getattr(cfg, "ews_kdc", ""), verify=osync._default_verify())
    raise CalendarError("Календарь не настроен: включите серверную сверку (Graph или EWS).")


def day_bounds(day: datetime, tz: str) -> tuple[datetime, datetime]:
    """Начало и конец суток в поясе пользователя в UTC (для запросов «день»)."""
    z = ZoneInfo(tz)
    s = day.astimezone(z).replace(hour=0, minute=0, second=0, microsecond=0)
    return s.astimezone(timezone.utc), (s + timedelta(days=1)).astimezone(timezone.utc)
