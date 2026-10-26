"""Подставной Microsoft Graph для испытаний календаря (тесты backend и живой стенд): токен, calendarView, создание (идемпотентно по transactionId),
правка с If-Match, отмена и удаление. Только для испытаний; реального Exchange не заменяет."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx


def utc(p: dict) -> dict:
    """Как настоящий Graph при Prefer: outlook.timezone="UTC": ответ всегда в UTC."""
    d = datetime.fromisoformat(p["dateTime"]).replace(tzinfo=ZoneInfo(p["timeZone"]))
    return {"dateTime": d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}


class FakeGraph:
    """Минимальный Graph: токен, calendarView, создание (идемпотентно по transactionId), правка с If-Match, отмена и удаление."""

    def __init__(self):
        self.events: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.deny = False
        self.down = False
        self.n = 0

    def add_event(self, mailbox: str, **kw):
        self.n += 1
        eid = kw.pop("id", f"EVT{self.n:04d}")
        self.events[eid] = {"id": eid, "@odata.etag": f'W/"v{self.n}"', "mailbox": mailbox, "type": "singleInstance", "isAllDay": False, "isCancelled": False,
                            "location": {"displayName": ""}, "organizer": {"emailAddress": {"address": mailbox}}, **kw}
        return eid

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        if "/oauth2/v2.0/token" in path:
            return httpx.Response(200, json={"access_token": "tok"})
        self.calls.append((method, path))
        if self.down:
            raise httpx.ConnectError("down")
        if self.deny:
            return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})
        m = re.match(r"^/v1\.0/users/([^/]+)(/.*)$", path)
        if not m:
            return httpx.Response(404)
        mb, rest = m.group(1).replace("%40", "@"), m.group(2)
        if rest == "/calendarView" and method == "GET":
            a, b = req.url.params["startDateTime"], req.url.params["endDateTime"]
            vals = [{k: v for k, v in e.items() if k != "mailbox"} for e in self.events.values() if e["mailbox"] == mb and e["start"]["dateTime"][:19] < b[:19] and e["end"]["dateTime"][:19] > a[:19]]
            return httpx.Response(200, json={"value": vals})
        if rest == "/events" and method == "POST":
            j = json.loads(req.content)
            for e in self.events.values():
                if e.get("transactionId") and e.get("transactionId") == j.get("transactionId"):
                    return httpx.Response(201, json=e)                                   # Graph не создаёт второе событие с тем же transactionId
            eid = self.add_event(mb, subject=j["subject"], start=utc(j["start"]), end=utc(j["end"]),
                                 location=j["location"], attendees=j["attendees"], body=j["body"], recurrence=j.get("recurrence"), transactionId=j.get("transactionId"))
            self.events[eid]["raw"] = j
            return httpx.Response(201, json=self.events[eid])
        em = re.match(r"^/events/([^/]+)(/cancel)?$", rest)
        if em:
            eid = em.group(1)
            e = self.events.get(eid)
            if e is None or e["mailbox"] != mb:
                return httpx.Response(404)
            if method == "PATCH":
                if req.headers.get("If-Match") and req.headers["If-Match"] != e["@odata.etag"]:
                    return httpx.Response(412)
                j = json.loads(req.content)
                e.update({k: (utc(v) if k in ("start", "end") else v) for k, v in j.items() if k in ("subject", "start", "end")})
                self.n += 1
                e["@odata.etag"] = f'W/"v{self.n}"'
                return httpx.Response(200, json=e)
            if method == "POST" and em.group(2):
                e["isCancelled"] = True
                return httpx.Response(202)
            if method == "DELETE":
                del self.events[eid]
                return httpx.Response(204)
        return httpx.Response(404)
