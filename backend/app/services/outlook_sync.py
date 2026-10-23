"""Серверная сверка запланированных конференций с календарём организатора (Microsoft Graph или EWS).

Нужна, когда встречу изменили или отменили не через надстройку: на другом устройстве, в мобильном клиенте, в календаре помощника. Раз в `sync_interval_minutes`
по каждой активной запланированной комнате читается одно событие организатора **только по идентификатору** (тема, начало, окончание, признак отмены, приглашённые;
тело письма не читается):

* событие удалено или отменено → комната переводится в «отменена» (ссылка перестаёт пускать);
* изменилось время или тема → расписание комнаты обновляется, ссылка прежняя;
* изменился список приглашённых → обновляется список для «Предстоящих встреч» (адреса, которые календарь вернул не в SMTP-виде, прежних не удаляют);
* любая **ошибка связи или прав никогда не отменяет комнату**: пишется состояние `error` и причина, комната остаётся как есть. Отмена — только по явному ответу «события нет».

Минимальные права: Microsoft Graph — приложение с правом `Calendars.ReadBasic.All` (без тела и вложений); EWS — учётная запись с ролью ApplicationImpersonation
(или доступом «Рецензент» к календарям организаторов — «делегированный доступ»); вход — Basic, NTLM, Negotiate (Kerberos) или Kerberos по HTTPS,
несколько серверов за балансировщиком. Успешный вход прав на чужие календари не даёт: права проверяются отдельно.
Идентификаторы события присылает надстройка (при отправке приглашения или повторном нажатии кнопки в сохранённой встрече).
"""
from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Room, utcnow
from . import ews_auth
from . import outlook as ol
from . import recurrence as rc
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
    attendees: list | None = None          # [(адрес, имя, необязательный)]; None — календарь их не вернул
    attendees_complete: bool = True        # все адреса разобраны (иначе прежних приглашённых не удаляем)
    recurrence: dict | None = None          # шаблон серии в виде Microsoft Graph (patternedRecurrence); EWS приводится к нему же


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
                r = await c.get(f"{self._api}/users/{quote(organizer)}/events/{quote(rest_id, safe='')}", params={"$select": "subject,start,end,isCancelled,attendees,recurrence"},
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
        raw = [a for a in (j.get("attendees") or []) if isinstance(a, dict) and a.get("type") != "resource"]       # переговорные комнаты и оборудование — не люди
        people = [{"email": (a.get("emailAddress") or {}).get("address"), "name": (a.get("emailAddress") or {}).get("name"), "optional": a.get("type") == "optional"} for a in raw]
        att = ol.parse_attendees(people) if "attendees" in j else None
        return EventInfo(True, bool(j.get("isCancelled")), j.get("subject") or "", _utc(j["start"]["dateTime"]), _utc(j["end"]["dateTime"]), att,
                         att is None or len(att) == len(people), j.get("recurrence") if isinstance(j.get("recurrence"), dict) else None)

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


_DENIED = ("ErrorImpersonateUserDenied", "ErrorImpersonationDenied", "ErrorAccessDenied", "ErrorNonPrimarySmtpAddress")
_AUTH_CACHE: dict[tuple[str, str], tuple[str, float]] = {}      # (адрес, настройка) → (схема, время): автоопределение не повторяется на каждом проходе
_MODE_CACHE: dict[str, str] = {}                                 # адрес → impersonation | delegate: какой способ доступа к календарям сработал


class EwsClient:
    """EWS локального Exchange: Basic / NTLM / Negotiate (Kerberos) / Kerberos, несколько серверов за балансировщиком, имперсонация или делегированный доступ («Рецензент»).

    Каждая ошибка называет причину и место: нет связи (с каким сервером), сервер не предлагает выбранный способ, логин/пароль отклонены, нет прав на календарь организатора.
    Успешный вход прав на календари не даёт: права проверяются отдельным шагом. Пароль в тексты ошибок и журнал не попадает.
    """
    kind = "ews"

    def __init__(self, url: str, user: str, password: str, transport: httpx.AsyncBaseTransport | None = None, *, auth: str = "auto", domain: str = "", access: str = "auto",
                 realm: str = "", kdc: str = "", verify: Any = True):
        try:
            self._urls = ews_auth.normalize_urls(url)
        except ValueError as exc:
            raise CalendarError(str(exc)) from None
        if not self._urls:
            raise CalendarError("Не заполнен адрес EWS.")
        self._user, self._pw, self._transport, self._pref, self._domain, self._access = user, password, transport, auth if auth in ews_auth.AUTH_CHOICES else "auto", domain, access
        self._verify, self._good = verify, 0
        self._realm, self._kdc = realm, kdc
        self.last_detail = ""
        self.used_scheme = ""
        self.used_url = ""

    # ---- транспорт -------------------------------------------------------------------------------------
    def _red(self, text: str) -> str:
        return ews_auth.redact(text, self._pw)

    def _client(self, auth=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, verify=self._verify, auth=auth, timeout=httpx.Timeout(20.0, connect=8.0),
                                 limits=httpx.Limits(max_connections=1, max_keepalive_connections=1), follow_redirects=False)       # NTLM привязан к соединению

    async def _scheme(self, url: str) -> str:
        """Явная настройка — как задана. auto: найденная ранее схема, иначе Basic (как работало раньше); если сервер Basic не предлагает, схема выбирается по его ответу 401 (см. _post)."""
        if self._pref in ("basic", "ntlm", "negotiate", "kerberos"):
            return self._pref
        hit = _AUTH_CACHE.get((url, self._user))
        return hit[0] if hit and time.monotonic() - hit[1] < 600 else "basic"

    async def detect(self) -> list[dict]:
        """По каждому адресу: доступность, предложенные способы входа, рекомендация. Без учётных данных (ничего не отправляется, кроме пустого запроса)."""
        out = []
        for url in self._urls:
            host = urlsplit(url).hostname or url
            try:
                async with self._client() as c:
                    r = await c.get(url)
                offered = ews_auth.parse_schemes(r.headers.get_list("www-authenticate"))
                out.append({"url": url, "reachable": True, "status": r.status_code, "schemes": offered, "recommended": ews_auth.choose(offered, "auto") if r.status_code == 401 else None,
                            "message": (f"{host}: предлагает {', '.join(ews_auth.SCHEME_LABEL.get(x, x) for x in offered)}." if offered else f"{host}: ответил HTTP {r.status_code} без предложения способов входа (проверьте адрес Exchange.asmx).")})
            except httpx.HTTPError as exc:
                out.append({"url": url, "reachable": False, "status": None, "schemes": [], "recommended": None, "message": f"{host}: {self._net_reason(exc)}"})
        return out

    @staticmethod
    def _net_reason(exc: Exception) -> str:
        from . import outlook_diag  # noqa: PLC0415

        return outlook_diag.connect_failure(exc)[1] if isinstance(exc, httpx.HTTPError) else type(exc).__name__

    async def _send(self, url: str, scheme: str, xml: str) -> httpx.Response:
        host = urlsplit(url).hostname or ""
        if scheme in ("negotiate", "kerberos"):
            ews_auth.apply_kerberos_env(self._realm, self._kdc)
        auth = ews_auth.build_auth(scheme, self._user, self._pw, self._domain, host)
        async with self._client(auth) as c:
            return await c.post(url, content=xml.encode("utf-8"), headers={"Content-Type": "text/xml; charset=utf-8"})

    def _auth_failure(self, scheme: str, r: httpx.Response, url: str) -> CalendarError:
        offered = ews_auth.parse_schemes(r.headers.get_list("www-authenticate"))
        host = urlsplit(url).hostname
        need = "negotiate" if scheme == "kerberos" else scheme
        label = ews_auth.SCHEME_LABEL.get(scheme, scheme)
        if offered and need not in offered:
            return CalendarError(f"Сервер {host} не предлагает способ «{label}»; доступны: {', '.join(ews_auth.SCHEME_LABEL.get(x, x) for x in offered)}. Выберите другой способ входа.")
        hint = {"ntlm": " Для NTLM укажите имя в виде ДОМЕН\\пользователь или user@домен.", "negotiate": " Для Kerberos имя сервера в адресе должно совпадать с зарегистрированным именем службы HTTP/<имя>; проверьте область Kerberos и доступность KDC.",
                "kerberos": " Для Kerberos имя сервера в адресе должно совпадать с зарегистрированным именем службы HTTP/<имя>; проверьте область Kerberos и доступность KDC.", "basic": ""}.get(scheme, "")
        return CalendarError(f"Сервер {host} отклонил учётную запись ({label}): неверный логин или пароль, учётная запись заблокирована или не допущена к EWS.{hint}")

    async def _post(self, xml: str) -> ET.Element:
        failures: list[str] = []
        n = len(self._urls)
        for k in range(n):
            idx = (self._good + k) % n
            url = self._urls[idx]
            host = urlsplit(url).hostname or url
            try:
                scheme = await self._scheme(url)
                r = await self._send(url, scheme, xml)
            except CalendarError:
                raise
            except httpx.HTTPError as exc:
                failures.append(f"{host}: {self._net_reason(exc)}")
                continue
            except ImportError as exc:
                raise CalendarError(f"Способ входа недоступен в этой сборке: {self._red(str(exc))[:160]}") from None
            except Exception as exc:  # noqa: BLE001 — ошибки Kerberos/NTLM контекста (нет KDC, неверный пароль до отправки)
                msg = self._red(str(exc))[:220]
                if scheme in ("negotiate", "kerberos"):
                    raise CalendarError(f"Kerberos: не удалось получить билет для {host} ({type(exc).__name__}: {msg}). Проверьте область, KDC и имя службы HTTP/{host}.") from None
                raise CalendarError(f"Ошибка входа в EWS ({scheme}): {type(exc).__name__}: {msg}") from None
            if r.status_code in (502, 503, 504):
                failures.append(f"{host}: HTTP {r.status_code} (ошибка сервера или балансировщика)")
                continue
            if r.status_code == 401 and self._pref == "auto":
                offered = ews_auth.parse_schemes(r.headers.get_list("www-authenticate"))
                alt = ews_auth.choose(offered, "auto")
                if "basic" not in offered and alt and alt != scheme:        # Basic сервер не принимает: берём то, что он предлагает (при Basic в списке — настоящая ошибка пароля, повторов нет)
                    try:
                        r = await self._send(url, alt, xml)
                    except httpx.HTTPError as exc:
                        failures.append(f"{host}: {self._net_reason(exc)}")
                        continue
                    except Exception as exc:  # noqa: BLE001
                        raise CalendarError(f"Ошибка входа в EWS ({alt}): {type(exc).__name__}: {self._red(str(exc))[:220]}") from None
                    scheme = alt
                    if r.status_code != 401:
                        _AUTH_CACHE[(url, self._user)] = (alt, time.monotonic())
            if r.status_code == 401:
                _AUTH_CACHE.pop((url, self._user), None)
                raise self._auth_failure(scheme, r, url)               # другой сервер с теми же данными не пробуем: это блокировало бы учётную запись
            if r.status_code == 403:
                raise CalendarError(f"EWS на {host} запретил доступ (HTTP 403): проверьте права учётной записи на EWS и правила брандмауэра/WAF.")
            if r.status_code == 404:
                raise CalendarError(f"Адрес EWS на {host} не найден (HTTP 404): проверьте путь (обычно /EWS/Exchange.asmx).")
            if r.status_code != 200 and r.status_code != 500:
                raise CalendarError(f"EWS на {host} ответил HTTP {r.status_code}.")
            self._good, self.used_scheme, self.used_url = idx, scheme, url
            try:
                return ET.fromstring(r.content)
            except ET.ParseError:
                raise CalendarError(f"{host} вернул не SOAP-ответ: проверьте адрес Exchange.asmx (за балансировщиком он должен вести на CAS/EWS).") from None
        self.last_detail = "; ".join(failures)
        raise CalendarError("Exchange недоступен: " + "; ".join(failures) + ".")

    # ---- разбор ответов -------------------------------------------------------------------------------
    @staticmethod
    def _code(root: ET.Element) -> tuple[str, str]:
        msg = root.find(".//m:GetItemResponseMessage", NS)
        if msg is None:
            msg = root.find(".//m:GetFolderResponseMessage", NS)
        if msg is None:
            return "Error", "NoResponse"
        return msg.get("ResponseClass", "Error"), (msg.findtext("m:ResponseCode", "", NS) or "")

    def _mode(self) -> str:
        return self._access if self._access in ("impersonation", "delegate") else _MODE_CACHE.get(self._urls[0], "impersonation")

    async def _with_mode(self, body_for, organizer: str):
        """Выполняет запрос выбранным способом доступа; при auto и отказе имперсонации пробует делегированный доступ и запоминает, что сработало."""
        mode = self._mode()
        root = await self._post(_envelope(body_for(mode), organizer if mode == "impersonation" else None))
        cls, code = self._code(root)
        if cls != "Success" and code in _DENIED and self._access == "auto" and mode == "impersonation":
            root2 = await self._post(_envelope(body_for("delegate"), None))
            cls2, code2 = self._code(root2)
            if cls2 == "Success" or code2 not in _DENIED:
                _MODE_CACHE[self._urls[0]] = "delegate"
                return root2, cls2, code2, "delegate"
        if cls == "Success" and self._access == "auto":
            _MODE_CACHE[self._urls[0]] = mode
        return root, cls, code, mode

    def _denied(self, mode: str, probe: bool = False) -> CalendarError:
        if mode == "impersonation":
            tail = "Назначьте учётной записи роль ApplicationImpersonation (только на нужные ящики) либо выберите способ «Делегированный доступ» и выдайте право «Рецензент» на календари организаторов."
        else:
            tail = "Выдайте учётной записи право «Рецензент» на календарь организатора (Add-MailboxFolderPermission … -AccessRights Reviewer) либо роль ApplicationImpersonation."
        return CalendarError(("Вход выполнен, но " if probe else "") + "прав на календарь организатора нет. Успешный вход в EWS сам по себе прав на чужие календари не даёт. " + tail)

    async def get_event(self, organizer: str, rest_id: str | None, ews_id: str | None) -> EventInfo:
        if not ews_id:
            raise CalendarError("У события нет идентификатора EWS.")
        body = ('<m:GetItem><m:ItemShape><t:BaseShape>IdOnly</t:BaseShape><t:AdditionalProperties><t:FieldURI FieldURI="item:Subject"/><t:FieldURI FieldURI="calendar:Start"/>'
                '<t:FieldURI FieldURI="calendar:End"/><t:FieldURI FieldURI="calendar:IsCancelled"/><t:FieldURI FieldURI="calendar:RequiredAttendees"/>'
                '<t:FieldURI FieldURI="calendar:OptionalAttendees"/><t:FieldURI FieldURI="calendar:Recurrence"/></t:AdditionalProperties></m:ItemShape>'
                f'<m:ItemIds><t:ItemId Id="{escape(ews_id, {chr(34): "&quot;"})}"/></m:ItemIds></m:GetItem>')
        root, cls, code, mode = await self._with_mode(lambda _m: body, organizer)
        if cls == "Success":
            it = root.find(".//t:CalendarItem", NS)
            if it is None:
                raise CalendarError("EWS вернул не событие календаря.")
            people = [{"email": a.findtext("t:Mailbox/t:EmailAddress", "", NS), "name": a.findtext("t:Mailbox/t:Name", "", NS), "optional": kind == "Optional"}
                      for kind in ("Required", "Optional") for a in it.findall(f"t:{kind}Attendees/t:Attendee", NS)]
            att = ol.parse_attendees(people)
            return EventInfo(True, (it.findtext("t:IsCancelled", "false", NS) or "").lower() == "true", it.findtext("t:Subject", "", NS) or "",
                             _utc(it.findtext("t:Start", "", NS)), _utc(it.findtext("t:End", "", NS)), att, len(att or []) == len(people), _ews_recurrence(it))
        if code in ("ErrorItemNotFound", "ErrorInvalidIdMalformed", "ErrorInvalidId"):
            return EventInfo(False) if code == "ErrorItemNotFound" else self._bad_id()
        if code in _DENIED:
            raise self._denied(mode)
        raise CalendarError(f"EWS ответил ошибкой {code or 'неизвестной'}.")

    @staticmethod
    def _bad_id() -> EventInfo:
        raise CalendarError("Идентификатор события не принят EWS (событие пересохранено?). Нажмите кнопку в Outlook ещё раз.")

    async def probe(self, mailbox: str) -> str:
        """Два отдельных шага: 1) вход (способ, сервер); 2) права на календарь указанного ящика (имперсонация или делегированный доступ)."""
        def body_for(mode: str) -> str:
            folder = ('<t:DistinguishedFolderId Id="calendar"/>' if mode == "impersonation" else
                      f'<t:DistinguishedFolderId Id="calendar"><t:Mailbox><t:EmailAddress>{escape(mailbox)}</t:EmailAddress></t:Mailbox></t:DistinguishedFolderId>')
            return f'<m:GetFolder><m:FolderShape><t:BaseShape>IdOnly</t:BaseShape></m:FolderShape><m:FolderIds>{folder}</m:FolderIds></m:GetFolder>'
        root, cls, code, mode = await self._with_mode(body_for, mailbox)
        login = f"Вход выполнен ({ews_auth.SCHEME_LABEL.get(self.used_scheme, self.used_scheme)}, сервер {urlsplit(self.used_url).hostname})."
        way = "имперсонация" if mode == "impersonation" else "делегированный доступ"
        if cls == "Success":
            return f"{login} Права на календарь {mailbox}: есть ({way}), календарь читается."
        if code in _DENIED:
            raise CalendarError(f"{login} " + self._denied(mode).args[0])
        raise CalendarError(f"{login} EWS ответил ошибкой {code or 'неизвестной'}.")


_EWS_PATTERN = {"DailyRecurrence": "daily", "WeeklyRecurrence": "weekly", "AbsoluteMonthlyRecurrence": "absoluteMonthly", "RelativeMonthlyRecurrence": "relativeMonthly",
                "AbsoluteYearlyRecurrence": "absoluteYearly", "RelativeYearlyRecurrence": "relativeYearly"}


def _ews_recurrence(it: ET.Element) -> dict | None:
    """EWS `Recurrence` → та же форма, что у Microsoft Graph (pattern + range): разбор шаблона один для обоих источников."""
    rec = it.find("t:Recurrence", NS)
    if rec is None:
        return None
    pat: dict = {}
    for tag, kind in _EWS_PATTERN.items():
        el = rec.find(f"t:{tag}", NS)
        if el is not None:
            pat = {"type": kind, "interval": int(el.findtext("t:Interval", "1", NS) or 1), "daysOfWeek": (el.findtext("t:DaysOfWeek", "", NS) or "").split(),
                   "dayOfMonth": int(el.findtext("t:DayOfMonth", "0", NS) or 0) or None, "index": (el.findtext("t:DayOfWeekIndex", "", NS) or "").lower() or None,
                   "month": el.findtext("t:Month", "", NS) or None}
            break
    rng: dict = {}
    for tag, kind in (("NoEndRecurrence", "noEnd"), ("EndDateRecurrence", "endDate"), ("NumberedRecurrence", "numbered")):
        el = rec.find(f"t:{tag}", NS)
        if el is not None:
            rng = {"type": kind, "startDate": (el.findtext("t:StartDate", "", NS) or "")[:10], "endDate": (el.findtext("t:EndDate", "", NS) or "")[:10] or None,
                   "numberOfOccurrences": int(el.findtext("t:NumberOfOccurrences", "0", NS) or 0) or None}
            break
    return {"pattern": pat, "range": rng} if pat and rng else None


def _default_verify():
    from . import tls_chain  # noqa: PLC0415

    ca = os.environ.get("OUTLOOK_CHECK_CA_FILE") or ("/data/ca/outlook-ca.pem" if os.path.isfile("/data/ca/outlook-ca.pem") else "")
    return tls_chain.system_context(ca or None)


def make_client(cfg: Any, transport: httpx.AsyncBaseTransport | None = None):
    if cfg.sync_mode == "graph":
        if not (cfg.graph_tenant_id and cfg.graph_client_id and cfg.graph_client_secret):
            raise CalendarError("Не заполнены каталог, приложение или секрет Microsoft Graph.")
        return GraphClient(cfg.graph_tenant_id, cfg.graph_client_id, cfg.graph_client_secret, transport)
    if cfg.sync_mode == "ews":
        if not (cfg.ews_url and cfg.ews_user and cfg.ews_password):
            raise CalendarError("Не заполнены адрес EWS, логин или пароль.")
        return EwsClient(cfg.ews_url, cfg.ews_user, cfg.ews_password, transport, auth=getattr(cfg, "ews_auth", "auto"), domain=getattr(cfg, "ews_domain", ""),
                         access=getattr(cfg, "ews_access", "auto"), realm=getattr(cfg, "ews_kerberos_realm", ""), kdc=getattr(cfg, "ews_kdc", ""), verify=_default_verify())
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
        if ev.attendees is not None:
            await ol.set_invitees(db, room, ev.attendees, "calendar", prune=ev.attendees_complete)
        if room.recurring and ev.recurrence:
            pat = rc.from_graph(ev.recurrence, ev.start, ev.end, room.outlook_tz)
            if pat is not None:
                room.outlook_recurrence = pat
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
            journal.emit("outlook", f"sync_{res}", level="warn" if res == "error" else "info", room=room.name, message=(room.outlook_sync_error or "")[:300] if res == "error" else None,
                         data={"client": client.kind, "scheme": getattr(client, "used_scheme", None) or None, "server": (urlsplit(client.used_url).hostname if getattr(client, "used_url", "") else None)})
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
