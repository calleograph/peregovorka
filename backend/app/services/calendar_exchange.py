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
            return CalEvent(str(j["id"]), str(j.get("subject") or ""), _utc(j["start"]["dateTime"]), _utc(j["end"]["dateTime"]), j.get("@odata.etag"), bool(j.get("isAllDay")),
                            bool(j.get("isCancelled")), kind, str(loc)[:300], str(org).lower(), j.get("seriesMasterId"))
        except (KeyError, TypeError, ValueError):
            return None

    async def list_events(self, mailbox: str, start: datetime, end: datetime) -> list[CalEvent]:
        out: list[CalEvent] = []
        params: dict[str, Any] | None = {"startDateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "endDateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                          "$select": "id,subject,start,end,isAllDay,isCancelled,type,seriesMasterId,location,organizer", "$top": "100", "$orderby": "start/dateTime"}
        url: str | None = None
        for _ in range(MAX_PAGES):
            r = await self._req("GET", mailbox, "/calendarView", params=params, absolute=url)
            if r.status_code != 200:
                raise self._fail(r)
            j = r.json()
            out += [e for e in (self._event(x) for x in j.get("value") or []) if e]
            url, params = j.get("@odata.nextLink"), None
            if not url or len(out) >= MAX_EVENTS:
                break
        return out[:MAX_EVENTS]

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

    async def update_event(self, mailbox: str, event_id: str, change: str | None, *, subject: str | None, start: datetime | None, end: datetime | None, tz: str = "UTC") -> Created:
        patch: dict[str, Any] = {}
        if subject is not None:
            patch["subject"] = subject
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
        for kind in ("FindItem", "GetItem", "GetFolder"):
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

    async def list_events(self, mailbox: str, start: datetime, end: datetime) -> list[CalEvent]:
        fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

        def body_for(mode: str) -> str:
            folder = ('<t:DistinguishedFolderId Id="calendar"/>' if mode == "impersonation" else
                      f'<t:DistinguishedFolderId Id="calendar"><t:Mailbox><t:EmailAddress>{escape(mailbox)}</t:EmailAddress></t:Mailbox></t:DistinguishedFolderId>')
            return ('<m:FindItem Traversal="Shallow"><m:ItemShape><t:BaseShape>IdOnly</t:BaseShape><t:AdditionalProperties><t:FieldURI FieldURI="item:Subject"/>'
                    '<t:FieldURI FieldURI="calendar:Start"/><t:FieldURI FieldURI="calendar:End"/><t:FieldURI FieldURI="calendar:IsAllDayEvent"/><t:FieldURI FieldURI="calendar:IsCancelled"/>'
                    '<t:FieldURI FieldURI="calendar:Location"/><t:FieldURI FieldURI="calendar:CalendarItemType"/><t:FieldURI FieldURI="calendar:Organizer"/></t:AdditionalProperties></m:ItemShape>'
                    f'<m:CalendarView MaxEntriesReturned="{MAX_EVENTS}" StartDate="{fmt(start)}" EndDate="{fmt(end)}"/><m:ParentFolderIds>{folder}</m:ParentFolderIds></m:FindItem>')

        root, cls, code, mode = await self._with_mode(body_for, mailbox)
        if cls != "Success":
            if code in _DENIED:
                raise self._denied(mode)
            raise CalendarError(f"EWS ответил ошибкой {code or 'неизвестной'}.")
        out: list[CalEvent] = []
        for it in root.findall(".//t:CalendarItem", NS):
            try:
                iid = it.find("t:ItemId", NS)
                kind = {"Single": "single", "RecurringMaster": "series", "Occurrence": "occurrence", "Exception": "exception"}.get(it.findtext("t:CalendarItemType", "", NS), "single")
                out.append(CalEvent(iid.get("Id", ""), it.findtext("t:Subject", "", NS) or "", _utc(it.findtext("t:Start", "", NS)), _utc(it.findtext("t:End", "", NS)), iid.get("ChangeKey"),
                                    (it.findtext("t:IsAllDayEvent", "false", NS) or "").lower() == "true", (it.findtext("t:IsCancelled", "false", NS) or "").lower() == "true", kind,
                                    (it.findtext("t:Location", "", NS) or "")[:300], (it.findtext("t:Organizer/t:Mailbox/t:EmailAddress", "", NS) or "").lower()))
            except (AttributeError, ValueError):
                continue
        return out

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

    async def update_event(self, mailbox: str, event_id: str, change: str | None, *, subject: str | None, start: datetime | None, end: datetime | None, tz: str = "UTC") -> Created:
        ups = []
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
