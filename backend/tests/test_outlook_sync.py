"""Outlook: связь комнаты с событием, серверная сверка (Graph и EWS на подставных HTTP-транспортах), пакет установки, статус и манифест.
Реального Exchange/Microsoft 365 нет: проверяется логика и разбор ответов по документированным форматам. Адреса и ящики вымышленные (example.org)."""
from __future__ import annotations

import io
import itertools
import json
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.models import Room, utcnow
from app.services import outlook as ol
from app.services import outlook_sync as osync
from app.services.outlook_package import build_package
from app.services.settings import OutlookSettings

from .conftest import login
from .test_outlook import O, _anon, _body, _enable, _token, iso

_TICK = itertools.count(1)          # каждый проход «позже» предыдущего: комната снова пора сверять
LINK = {"organizer": "Owner@Example.org", "rest_id": "AAMkAGRestId0123456789", "ews_id": "AAMkAGEwsId0123456789==", "tz": "Europe/Moscow"}


def _create(client, link=None, **kw):
    _enable(client, **kw)
    h = _token(client)
    _anon(client)
    b = _body(**(link or {}))
    b.update(LINK)
    r = client.post(f"{O}/meetings", json=b, headers=h)
    assert r.status_code == 200, r.text
    return r.json(), h, b


def _room(client, rid):
    async def get():
        async with client.app_obj.state.session_maker() as db:
            return await db.get(Room, uuid.UUID(rid))
    return client.portal.call(get)


# ------------------------------------------------------------------------------------------------ связь с событием
def test_create_stores_event_link_and_rejects_garbage(client):
    j, h, b = _create(client)
    r = _room(client, j["id"])
    assert r.outlook_organizer == "owner@example.org" and r.outlook_rest_id == LINK["rest_id"] and r.outlook_ews_id == LINK["ews_id"] and r.outlook_tz == "Europe/Moscow"
    bad = dict(b, organizer="<script>@x", rest_id="a b", ews_id="x", tz="DROP TABLE;")
    assert client.post(f"{O}/meetings", json=bad, headers=h).status_code == 200
    r = _room(client, j["id"])
    assert r.outlook_organizer == "owner@example.org" and r.outlook_rest_id == LINK["rest_id"] and r.outlook_tz == "Europe/Moscow"        # мусор не затёр хорошие значения


def test_addin_log_goes_to_journal_and_needs_token(client):
    _enable(client)
    h = _token(client)
    _anon(client)
    assert client.post(f"{O}/log", json={"message": "x"}).status_code == 401
    assert client.post(f"{O}/log", json={"message": "Не удалось вставить ссылку", "where": "insert"}, headers=h).status_code == 204
    client.portal.call(client.app_obj.state.journal.flush)
    login(client, "root")
    items = client.get("/api/v1/admin/journal", params={"category": "outlook", "limit": 20}).json()
    assert "Не удалось вставить ссылку" in json.dumps(items, ensure_ascii=False)


# ------------------------------------------------------------------------------------------------ Graph
def graph_transport(event: dict | int, calls: list | None = None):
    def h(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url))
        if "oauth2/v2.0/token" in str(req.url):
            return httpx.Response(200, json={"access_token": "tok"})
        if isinstance(event, int):
            return httpx.Response(event, json={"error": {}})
        assert req.headers["authorization"] == "Bearer tok" and 'outlook.timezone="UTC"' in req.headers["prefer"]
        assert "select=subject" in str(req.url) and "body" not in str(req.url)              # тело письма не запрашивается
        return httpx.Response(200, json=event)
    return httpx.MockTransport(h)


def ev(start, end, subject="Планёрка", cancelled=False):
    return {"subject": subject, "isCancelled": cancelled, "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
            "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}}


def _run(client, transport, mode="graph", **cfg_over):
    login(client, "root")
    patch = {"sync_mode": mode, "sync_interval_minutes": 1}
    patch.update({"graph_tenant_id": "tenant-1", "graph_client_id": "app-1", "graph_client_secret": "S3cret!", "ews_url": "https://mail.example.org/EWS/Exchange.asmx",
                  "ews_user": "svc", "ews_password": "P@ss"})
    patch.update(cfg_over)
    assert client.put("/api/v1/admin/settings/outlook", json=patch).status_code == 200

    async def go():
        async with client.app_obj.state.session_maker() as db:
            cfg = await client.app_obj.state.settings_svc.get(db, "outlook")
            cl = osync.make_client(cfg, transport)
            return await osync.run_once(db, cfg, cl, now=utcnow() + timedelta(minutes=5 * next(_TICK)))
    return client.portal.call(go)


def test_graph_reschedules_keeps_link_and_cancels_on_missing_event(client):
    j, h, b = _create(client)
    s = utcnow() + timedelta(hours=5)
    calls: list = []
    res = _run(client, graph_transport(ev(s, s + timedelta(hours=1), "Новая тема"), calls))
    assert res["rescheduled"] == 1 and any("/users/owner%40example.org/events/" in c for c in calls)
    r = _room(client, j["id"])
    assert r.name == "Новая тема" and abs((ol.aware(r.scheduled_start) - s).total_seconds()) < 2 and r.slug.endswith(j["url"].rsplit("/", 1)[1]) and r.outlook_sync_state == "ok"
    assert ol.aware(r.join_opens_at) == ol.aware(r.scheduled_start) - timedelta(minutes=10)         # окно входа пересчитано, ссылка прежняя
    # событие удалено из календаря → комната отменена
    res = _run(client, graph_transport(404))
    assert res["cancelled"] == 1
    r = _room(client, j["id"])
    assert r.cancelled_at is not None and r.outlook_sync_state == "missing"
    _anon(client)
    assert client.get(f"{O}/public/{j['url'].rsplit('/', 1)[1]}").json()["state"] == "cancelled"


def test_graph_cancelled_flag_cancels(client):
    j, *_ = _create(client)
    s = utcnow() + timedelta(hours=2)
    assert _run(client, graph_transport(ev(s, s + timedelta(hours=1), cancelled=True)))["cancelled"] == 1
    assert _room(client, j["id"]).cancelled_at is not None


def test_errors_never_cancel_a_room(client):
    j, *_ = _create(client)
    for status in (401, 403, 500, 429):
        res = _run(client, graph_transport(status))
        assert res["error"] == 1 and res["cancelled"] == 0, status
        r = _room(client, j["id"])
        assert r.cancelled_at is None and r.outlook_sync_state == "error" and r.outlook_sync_error, status
    # связь оборвана
    def boom(req):
        raise httpx.ConnectError("down")
    res = _run(client, httpx.MockTransport(boom))
    assert res["error"] == 1 and _room(client, j["id"]).cancelled_at is None
    assert "S3cret" not in (_room(client, j["id"]).outlook_sync_error or "")


def test_series_keeps_open_hours_but_follows_deletion(client):
    login(client, "root")
    j, h, b = _create(client, link={"recurring": True})
    assert _room(client, j["id"]).recurring is True
    before = _room(client, j["id"]).scheduled_start
    s = utcnow() + timedelta(days=3)
    assert _run(client, graph_transport(ev(s, s + timedelta(hours=1))))["rescheduled"] == 0
    assert _room(client, j["id"]).scheduled_start == before                 # время серии не трогаем: открыта всегда
    assert _run(client, graph_transport(404))["cancelled"] == 1


def test_run_only_touches_due_tracked_rooms(client):
    j, h, b = _create(client)
    _anon(client)
    other = _body(); other.pop("organizer", None)
    r2 = client.post(f"{O}/meetings", json=other, headers=h).json()          # без идентификаторов события: сверять нечем
    s = utcnow() + timedelta(hours=2)
    res = _run(client, graph_transport(ev(s, s + timedelta(hours=1))))
    assert res["checked"] == 1
    assert _room(client, r2["id"]).outlook_sync_state is None


def test_make_client_requires_settings():
    cfg = OutlookSettings(sync_mode="graph")
    try:
        osync.make_client(cfg)
        raise AssertionError("ожидалась ошибка")
    except osync.CalendarError as exc:
        assert "Graph" in str(exc)
    assert isinstance(osync.make_client(OutlookSettings(sync_mode="ews", ews_url="https://m.example.org/EWS/Exchange.asmx", ews_user="u", ews_password="p")), osync.EwsClient)


# ------------------------------------------------------------------------------------------------ EWS
def ews_response(code="NoError", cls="Success", item=""):
    return ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><m:GetItemResponse xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages" '
            'xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"><m:ResponseMessages>'
            f'<m:GetItemResponseMessage ResponseClass="{cls}"><m:ResponseCode>{code}</m:ResponseCode><m:Items>{item}</m:Items></m:GetItemResponseMessage>'
            '</m:ResponseMessages></m:GetItemResponse></s:Body></s:Envelope>')


def ews_item(start, end, subject="EWS встреча", cancelled="false"):
    return (f"<t:CalendarItem><t:Subject>{subject}</t:Subject><t:Start>{start.strftime('%Y-%m-%dT%H:%M:%SZ')}</t:Start><t:End>{end.strftime('%Y-%m-%dT%H:%M:%SZ')}</t:End>"
            f"<t:IsCancelled>{cancelled}</t:IsCancelled></t:CalendarItem>")


def ews_transport(body: str, status=200, seen: list | None = None):
    def h(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, content=body.encode("utf-8"))
    return httpx.MockTransport(h)


def test_ews_reschedule_missing_and_errors(client):
    j, *_ = _create(client)
    s = utcnow() + timedelta(hours=6)
    seen: list = []
    assert _run(client, ews_transport(ews_response(item=ews_item(s, s + timedelta(minutes=45))), seen=seen), mode="ews")["rescheduled"] == 1
    req = seen[0]
    xml = req.content.decode()
    assert "PrimarySmtpAddress>owner@example.org<" in xml and LINK["ews_id"] in xml and req.headers["authorization"].startswith("Basic ")
    assert _room(client, j["id"]).name == "EWS встреча"
    assert _run(client, ews_transport(ews_response("ErrorAccessDenied", "Error")), mode="ews")["error"] == 1
    assert _room(client, j["id"]).cancelled_at is None
    assert _run(client, ews_transport("", 401), mode="ews")["error"] == 1
    assert _run(client, ews_transport("<html>not soap</html>"), mode="ews")["error"] == 1
    assert _room(client, j["id"]).cancelled_at is None
    assert _run(client, ews_transport(ews_response("ErrorItemNotFound", "Error")), mode="ews")["cancelled"] == 1
    assert _room(client, j["id"]).cancelled_at is not None


def test_ews_cancelled_item(client):
    j, *_ = _create(client)
    s = utcnow() + timedelta(hours=3)
    assert _run(client, ews_transport(ews_response(item=ews_item(s, s + timedelta(hours=1), cancelled="true"))), mode="ews")["cancelled"] == 1


# ------------------------------------------------------------------------------------------------ администрирование
def test_admin_endpoints_need_admin_and_never_return_secrets(client):
    j, h, b = _create(client)
    login(client, "alice")
    for path in ("/status", "/meetings", "/package"):
        assert client.get(f"/api/v1/admin/outlook{path}").status_code == 403
    assert client.post("/api/v1/admin/outlook/sync-test", json={"mailbox": "a@example.org"}).status_code == 403
    login(client, "root")
    client.put("/api/v1/admin/settings/outlook", json={"sync_mode": "graph", "graph_tenant_id": "t", "graph_client_id": "c", "graph_client_secret": "S3cret!"})
    items = client.get("/api/v1/admin/outlook/meetings").json()["items"]
    assert items and items[0]["tracked"] is True and items[0]["organizer_email"] == "owner@example.org" and "S3cret" not in json.dumps(items)
    st = client.get("/api/v1/admin/outlook/status")
    assert st.status_code == 200 and "S3cret" not in st.text
    assert "S3cret" not in client.get("/api/v1/admin/settings/outlook").text


def test_status_checks_report_http_base_and_assets(client):
    _enable(client)
    login(client, "root")
    st = client.get("/api/v1/admin/outlook/status").json()
    by = {c["key"]: c for c in st["checks"]}
    assert by["url"]["ok"] is False and "HTTPS" in by["url"]["message"] and st["ok"] is False        # тестовый адрес — http://testserver


def test_status_with_https_url_uses_fake_transport(tmp_path, directory):
    from .conftest import make_settings, running_app

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/outlook/icon-64"):
            return httpx.Response(404)
        return httpx.Response(200, json={"enabled": True})

    with running_app(make_settings(tmp_path, app_public_url="https://meet.example.org"), directory, transports={"outlook": httpx.MockTransport(handler)}) as c:
        login(c, "root")
        c.put("/api/v1/admin/settings/outlook", json={"enabled": True})
        st = c.get("/api/v1/admin/outlook/status").json()
        by = {x["key"]: x for x in st["checks"]}
        assert by["url"]["ok"] is True and by["api"]["ok"] is True and by["files"]["ok"] is False and "icon-64.png" in by["files"]["message"] and by["manifest"]["ok"] is True


def test_sync_test_endpoint(client):
    login(client, "root")
    client.put("/api/v1/admin/settings/outlook", json={"sync_mode": "off"})
    r = client.post("/api/v1/admin/outlook/sync-test", json={"mailbox": "owner@example.org"}).json()
    assert r["ok"] is False and "выключена" in r["message"]
    assert client.post("/api/v1/admin/outlook/sync-test", json={"mailbox": "not-an-address"}).status_code == 422


# ------------------------------------------------------------------------------------------------ манифест и пакет
def _manifest(sync):
    return ol.manifest_xml("https://meet.example.org", "0.23.0", sync_on_send=sync)


def test_manifest_is_wellformed_and_id_is_stable():
    plain, with_sync = _manifest(False), _manifest(True)
    for x in (plain, with_sync):
        ET.fromstring(x)
    id_ = lambda x: re.search(r"<Id>([^<]+)</Id>", x).group(1)          # noqa: E731
    assert id_(plain) == id_(with_sync) == id_(ol.manifest_xml("https://meet.example.org", "9.9.9"))        # не зависит от версии и настроек
    assert id_(plain) != id_(ol.manifest_xml("https://other.example.org", "0.23.0"))
    assert "LaunchEvent" not in plain and 'MinVersion="1.3"' in plain and "residRuntime" not in plain
    assert 'Type="OnAppointmentSend"' in with_sync and 'MinVersion="1.12"' in with_sync and 'SendMode="SoftBlock"' in with_sync and "https://meet.example.org/outlook/commands.js" in with_sync


def test_package_contents_are_server_specific_and_secret_free():
    cfg = OutlookSettings(sync_on_send=True, graph_client_secret="S3cret!", ews_password="P@ss", graph_tenant_id="tid-123")
    data = build_package("https://meet.example.org", "0.23.0", cfg)
    assert build_package("https://meet.example.org", "0.23.0", cfg) == data                       # повторное скачивание — тот же архив
    z = zipfile.ZipFile(io.BytesIO(data))
    assert sorted(z.namelist()) == ["Install-OutlookAddin.ps1", "Peregovorka-Outlook.xml", "README.txt", "Test-OutlookAddin.ps1"]
    allf = "".join(z.read(n).decode("utf-8-sig") for n in z.namelist())
    assert "S3cret" not in allf and "P@ss" not in allf and "tid-123" not in allf
    xml = z.read("Peregovorka-Outlook.xml").decode()
    assert "https://meet.example.org/outlook/commands.html" in xml and 'OnAppointmentSend' in xml
    assert z.read("Install-OutlookAddin.ps1").startswith(b"\xef\xbb\xbf") and z.read("Test-OutlookAddin.ps1").startswith(b"\xef\xbb\xbf")
    readme = z.read("README.txt").decode("utf-8-sig")
    assert "meet.example.org" in readme and "Remove-App" in readme and "Integrated apps" in readme


def test_package_download_audited_and_needs_https(client):
    _enable(client)
    login(client, "root")
    r = client.get("/api/v1/admin/outlook/package")
    assert r.status_code == 409 and "HTTPS" in r.json()["detail"]                              # тестовый адрес http://testserver
    client.put("/api/v1/admin/settings/outlook", json={"public_url": "https://meet.example.org"})
    r = client.get("/api/v1/admin/outlook/package")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "outlook.package" in client.get("/api/v1/admin/audit").text
    assert zipfile.ZipFile(io.BytesIO(r.content)).testzip() is None
