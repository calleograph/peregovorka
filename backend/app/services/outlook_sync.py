"""Серверная сверка запланированных конференций с календарём организатора (Microsoft Graph или EWS).

Нужна, когда встречу изменили или отменили не через надстройку: на другом устройстве, в мобильном клиенте, в календаре помощника. Раз в `sync_interval_minutes`
по каждой активной запланированной комнате читается одно событие организатора **только по идентификатору** (тема, начало, окончание, признак отмены; тело письма не читается):

* событие удалено или отменено → комната переводится в «отменена» (ссылка перестаёт пускать);
* изменилось время или тема → расписание комнаты обновляется, ссылка прежняя;
* любая **ошибка связи или прав никогда не отменяет комнату**: пишется состояние `error` и причина, комната остаётся как есть. Отмена — только по явному ответу «события нет».

Минимальные права: Microsoft Graph — приложение с правом `Calendars.ReadBasic.All` (без тела и вложений); EWS — учётная запись с ролью ApplicationImpersonation
(или доступом «Рецензент» к календарям организаторов), базовая аутентификация по HTTPS. NTLM/Kerberos не поддержаны.
Идентификаторы события присылает надстройка (при отправке приглашения или повторном нажатии кнопки в сохранённой встрече).
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Room, utcnow
from . import outlook as ol
from .temp_rooms import TempRoomError

log = logging.getLogger("app.outlook_sync")
NS = {"s": "http://schemas.xmlsoap.org/soap/envelope/", "m": "http://schemas.microsoft.com/exchange/services/2006/messages", "t": "http://schemas.microsoft.com/exchange/services/2006/types"}


class CalendarError(Exception):
    """Календарь недоступен или нет прав. Не означает «события нет»."""


@dataclass
class EventInfo:
    exists: bool
    cancelled: bool = False
    subject: str = ""
    start: datetime | None = None
    end: datetime | None = None


def _utc(text: str) -> datetime:
    d = datetime.fromisoformat(text.strip().replace("Z", "+00:00")[:26] if "." in text else text.strip().replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


# ------------------------------------------------------------------------------------------------ Microsoft Graph
class GraphClient:
    kind = "graph"

    def __init__(self, tenant: str, client_id: str, secret: str, transport: httpx.AsyncBaseTransport | None = None, authority: str = "https://login.microsoftonline.com",
                 api: str = "https://graph.microsoft.com/v1.0"):
        self._t, self._id, self._secret, self._transport, self._auth, self._api = tenant, client_id, secret, transport, authority.rstrip("/"), api.rstrip("/")

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(15.0, connect=8.0))

    async def _token(self, c: httpx.AsyncClient) -> str:
        r = await c.post(f"{self._auth}/{quote(self._t)}/oauth2/v2.0/token", data={"client_id": self._id, "client_secret": self._secret, "scope": "https://graph.microsoft.com/.default",
                                                                                  "grant_type": "client_credentials"})
        if r.status_code != 200:
            raise CalendarError(f"Microsoft Entra ID отклонил вход приложения (HTTP {r.status_code}): проверьте идентификатор каталога, приложения и секрет.")
        return r.json()["access_token"]

    async def get_event(self, organizer: str, rest_id: str | None, ews_id: str | None) -> EventInfo:
        if not rest_id:
            raise CalendarError("У события нет идентификатора для Microsoft Graph.")
        try:
            async with self._http() as c:
                tok = await self._token(c)
                r = await c.get(f"{self._api}/users/{quote(organizer)}/events/{quote(rest_id, safe='')}", params={"$select": "subject,start,end,isCancelled"},
                                headers={"Authorization": f"Bearer {tok}", "Prefer": 'outlook.timezone="UTC"'})
        except httpx.HTTPError as exc:
            raise CalendarError(f"Нет связи с Microsoft Graph ({type(exc).__name__}).") from None
        if r.status_code == 404:
            return EventInfo(False)
        if r.status_code in (401, 403):
            raise CalendarError("Graph отказал в доступе к календарю: выдайте приложению право Calendars.ReadBasic.All и подтвердите его администратором.")
        if r.status_code != 200:
            raise CalendarError(f"Graph ответил HTTP {r.status_code}.")
        j = r.json()
        return EventInfo(True, bool(j.get("isCancelled")), j.get("subject") or "", _utc(j["start"]["dateTime"]), _utc(j["end"]["dateTime"]))

    async def probe(self, mailbox: str) -> str:
        """Проверка доступа: вход приложения и чтение календаря указанного почтового ящика. Возвращает пояснение; при ошибке — CalendarError."""
        try:
            async with self._http() as c:
                tok = await self._token(c)
                r = await c.get(f"{self._api}/users/{quote(mailbox)}/calendar", params={"$select": "name"}, headers={"Authorization": f"Bearer {tok}"})
        except httpx.HTTPError as exc:
            raise CalendarError(f"Нет связи с Microsoft Graph ({type(exc).__name__}).") from None
        if r.status_code == 200:
            return f"Вход приложения выполнен, календарь {mailbox} читается."
        if r.status_code in (401, 403):
            raise CalendarError("Вход выполнен, но чтение календаря запрещено: нужно право приложения Calendars.ReadBasic.All с согласием администратора.")
        if r.status_code == 404:
            raise CalendarError(f"Почтовый ящик {mailbox} не найден.")
        raise CalendarError(f"Graph ответил HTTP {r.status_code}.")


# ------------------------------------------------------------------------------------------------ EWS
def _envelope(body: str, mailbox: str | None) -> str:
    imp = (f'<t:ExchangeImpersonation><t:ConnectingSID><t:PrimarySmtpAddress>{escape(mailbox)}</t:PrimarySmtpAddress></t:ConnectingSID></t:ExchangeImpersonation>' if mailbox else "")
    return ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types" '
            'xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"><soap:Header><t:RequestServerVersion Version="Exchange2013"/>' + imp +
            f'</soap:Header><soap:Body>{body}</soap:Body></soap:Envelope>')


class EwsClient:
    kind = "ews"

    def __init__(self, url: str, user: str, password: str, transport: httpx.AsyncBaseTransport | None = None):
        self._url, self._user, self._pw, self._transport = url, user, password, transport

    async def _post(self, xml: str) -> ET.Element:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(20.0, connect=8.0), auth=httpx.BasicAuth(self._user, self._pw)) as c:
                r = await c.post(self._url, content=xml.encode("utf-8"), headers={"Content-Type": "text/xml; charset=utf-8"})
        except httpx.HTTPError as exc:
            raise CalendarError(f"Нет связи с EWS ({type(exc).__name__}).") from None
        if r.status_code == 401:
            raise CalendarError("EWS отклонил логин/пароль. Поддерживается только базовая аутентификация (NTLM и Kerberos не поддержаны).")
        if r.status_code != 200:
            raise CalendarError(f"EWS ответил HTTP {r.status_code}.")
        try:
            return ET.fromstring(r.content)
        except ET.ParseError:
            raise CalendarError("EWS вернул не SOAP-ответ: проверьте адрес Exchange.asmx.") from None

    @staticmethod
    def _code(root: ET.Element) -> tuple[str, str]:
        msg = root.find(".//m:GetItemResponseMessage", NS)
        if msg is None:
            msg = root.find(".//m:GetFolderResponseMessage", NS)
        if msg is None:
            return "Error", "NoResponse"
        return msg.get("ResponseClass", "Error"), (msg.findtext("m:ResponseCode", "", NS) or "")

    async def get_event(self, organizer: str, rest_id: str | None, ews_id: str | None) -> EventInfo:
        if not ews_id:
            raise CalendarError("У события нет идентификатора EWS.")
        body = ('<m:GetItem><m:ItemShape><t:BaseShape>IdOnly</t:BaseShape><t:AdditionalProperties><t:FieldURI FieldURI="item:Subject"/><t:FieldURI FieldURI="calendar:Start"/>'
                '<t:FieldURI FieldURI="calendar:End"/><t:FieldURI FieldURI="calendar:IsCancelled"/></t:AdditionalProperties></m:ItemShape>'
                f'<m:ItemIds><t:ItemId Id="{escape(ews_id, {chr(34): "&quot;"})}"/></m:ItemIds></m:GetItem>')
        root = await self._post(_envelope(body, organizer))
        cls, code = self._code(root)
        if cls == "Success":
            it = root.find(".//t:CalendarItem", NS)
            if it is None:
                raise CalendarError("EWS вернул не событие календаря.")
            return EventInfo(True, (it.findtext("t:IsCancelled", "false", NS) or "").lower() == "true", it.findtext("t:Subject", "", NS) or "",
                             _utc(it.findtext("t:Start", "", NS)), _utc(it.findtext("t:End", "", NS)))
        if code in ("ErrorItemNotFound", "ErrorInvalidIdMalformed", "ErrorInvalidId"):
            return EventInfo(False) if code == "ErrorItemNotFound" else self._bad_id()
        if code in ("ErrorImpersonateUserDenied", "ErrorImpersonationDenied", "ErrorAccessDenied"):
            raise CalendarError("EWS отказал в доступе к календарю организатора: назначьте учётной записи роль ApplicationImpersonation.")
        raise CalendarError(f"EWS ответил ошибкой {code or 'неизвестной'}.")

    @staticmethod
    def _bad_id() -> EventInfo:
        raise CalendarError("Идентификатор события не принят EWS (событие пересохранено?). Нажмите кнопку в Outlook ещё раз.")

    async def probe(self, mailbox: str) -> str:
        body = '<m:GetFolder><m:FolderShape><t:BaseShape>IdOnly</t:BaseShape></m:FolderShape><m:FolderIds><t:DistinguishedFolderId Id="calendar"/></m:FolderIds></m:GetFolder>'
        root = await self._post(_envelope(body, mailbox))
        cls, code = self._code(root)
        if cls == "Success":
            return f"Вход выполнен, календарь {mailbox} читается."
        if code in ("ErrorImpersonateUserDenied", "ErrorImpersonationDenied", "ErrorAccessDenied"):
            raise CalendarError("Вход выполнен, но доступа к календарю нет: назначьте роль ApplicationImpersonation.")
        raise CalendarError(f"EWS ответил ошибкой {code or 'неизвестной'}.")


def make_client(cfg: Any, transport: httpx.AsyncBaseTransport | None = None):
    if cfg.sync_mode == "graph":
        if not (cfg.graph_tenant_id and cfg.graph_client_id and cfg.graph_client_secret):
            raise CalendarError("Не заполнены каталог, приложение или секрет Microsoft Graph.")
        return GraphClient(cfg.graph_tenant_id, cfg.graph_client_id, cfg.graph_client_secret, transport)
    if cfg.sync_mode == "ews":
        if not (cfg.ews_url and cfg.ews_user and cfg.ews_password):
            raise CalendarError("Не заполнены адрес EWS, логин или пароль.")
        return EwsClient(cfg.ews_url, cfg.ews_user, cfg.ews_password, transport)
    raise CalendarError("Серверная сверка выключена.")


# ------------------------------------------------------------------------------------------------ сверка
async def reconcile_room(db: AsyncSession, room: Room, cfg: Any, client, now: datetime) -> str:
    """Одна комната: возвращает cancelled | rescheduled | ok | error. Ошибка связи комнату не трогает."""
    try:
        ev = await client.get_event(room.outlook_organizer or "", room.outlook_rest_id, room.outlook_ews_id)
    except CalendarError as exc:
        room.outlook_sync_state, room.outlook_sync_error, room.outlook_synced_at = "error", str(exc)[:300], now
        await db.commit()
        return "error"
    result = "ok"
    if not ev.exists or ev.cancelled:
        await ol.cancel(db, room)
        room.outlook_sync_state, result = "missing", "cancelled"
    else:
        room.outlook_sync_state = "ok"
        if not room.recurring and ev.start and ev.end:
            try:
                changed = await ol.reschedule(db, room, cfg, subject=" ".join(ev.subject.split())[:200] or None, start=ev.start, end=ev.end, recurring=False, now=now)
                result = "rescheduled" if changed else "ok"
            except TempRoomError as exc:
                room.outlook_sync_state, room.outlook_sync_error = "error", exc.message[:300]
                result = "error"
        if result != "error":
            room.outlook_sync_error = None
    room.outlook_synced_at = now
    await db.commit()
    return result


async def run_once(db: AsyncSession, cfg: Any, client, now: datetime | None = None, journal=None, limit: int = 200) -> dict:
    """Проход по активным запланированным комнатам, которым пора сверяться."""
    now = now or utcnow()
    due = now - timedelta(minutes=cfg.sync_interval_minutes)
    ident = Room.outlook_rest_id if client.kind == "graph" else Room.outlook_ews_id
    rows = (await db.execute(select(Room).where(Room.scheduled_start.is_not(None), Room.lifecycle != "closed", Room.cancelled_at.is_(None), Room.outlook_organizer.is_not(None),
                                                ident.is_not(None), (Room.outlook_synced_at.is_(None)) | (Room.outlook_synced_at < due)).order_by(Room.outlook_synced_at).limit(limit))).scalars().all()
    counts = {"checked": 0, "cancelled": 0, "rescheduled": 0, "error": 0, "ok": 0}
    for room in rows:
        res = await reconcile_room(db, room, cfg, client, now)
        counts["checked"] += 1
        counts[res] += 1
        if journal is not None and res in ("cancelled", "rescheduled", "error"):
            journal.emit("outlook", f"sync_{res}", level="warn" if res == "error" else "info", room=room.name, message=(room.outlook_sync_error or "")[:200] if res == "error" else None)
    return counts


class SyncRunner:
    """Вызывается из фонового цикла (reaper) примерно раз в минуту; сам решает, пора ли сверяться (настройки читаются каждый раз, поэтому изменения действуют сразу)."""

    def __init__(self, settings_svc, journal=None, transport: httpx.AsyncBaseTransport | None = None):
        self._svc, self.journal, self._transport = settings_svc, journal, transport
        self._last = 0.0

    async def tick(self, db: AsyncSession) -> dict | None:
        import time  # noqa: PLC0415

        cfg = await self._svc.get(db, "outlook")
        await db.commit()                    # не держать читающую транзакцию (SQLite)
        if not cfg.enabled or cfg.sync_mode == "off" or time.monotonic() - self._last < 60:
            return None
        self._last = time.monotonic()
        try:
            client = make_client(cfg, self._transport)
        except CalendarError as exc:
            log.warning("Серверная сверка Outlook не настроена", extra={"error": str(exc)[:100]})
            return None
        return await run_once(db, cfg, client, journal=self.journal)
