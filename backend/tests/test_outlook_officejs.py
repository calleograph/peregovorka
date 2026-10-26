"""Источник Office.js для надстройки Outlook (0.40.1): настройка, адреса страниц в манифесте, проверка локальных файлов и признаки в диагностике."""
import json
import xml.etree.ElementTree as ET

import httpx
import pytest

from app.services import outlook as ol

from .conftest import login, make_settings, running_app

NS = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1", "b": "http://schemas.microsoft.com/office/officeappbasictypes/1.0"}
BASE = "https://meet.example.org"


def _urls(xml: str) -> list[str]:
    root = ET.fromstring(xml)
    return [u.get("DefaultValue") for u in root.iter("{%s}Url" % NS["b"])] + [e.get("DefaultValue") for e in root.iter("{%s}SourceLocation" % NS["o"])]


def test_manifest_pages_are_clean_by_default_and_carry_the_source_when_changed():
    default = _urls(ol.manifest_xml(BASE, "0.40.1"))
    assert f"{BASE}/outlook/taskpane.html" in default and f"{BASE}/outlook/commands.html" in default
    assert not any("officejs" in u for u in default)                       # ранее скачанные манифесты работают без изменений: по умолчанию — с сервера
    assert ol.manifest_xml(BASE, "0.40.1") == ol.manifest_xml(BASE, "0.40.1", officejs="local")
    cdn = ol.manifest_xml(BASE, "0.40.1", sync_on_send=True, officejs="cdn")
    pages = [u for u in _urls(cdn) if u.endswith(".html?officejs=cdn")]
    assert sorted(set(pages)) == [f"{BASE}/outlook/commands.html?officejs=cdn", f"{BASE}/outlook/taskpane.html?officejs=cdn"]
    ids = lambda x: ET.fromstring(x).findtext("{%s}Id" % NS["o"])
    assert ids(cdn) == ids(ol.manifest_xml(BASE, "9.9.9"))                  # идентификатор от источника не зависит: обновление заменяет надстройку
    assert not any("icon" in u and "officejs" in u for u in _urls(cdn))      # значки и скрипты запрос не получают


def test_setting_accepts_only_known_sources_and_reaches_the_downloaded_manifest(client):
    login(client, "root")
    assert client.put("/api/v1/admin/settings/outlook", json={"officejs_source": "bogus"}).status_code == 422
    assert client.get("/api/v1/admin/settings/outlook").json()["officejs_source"] == "local"
    client.put("/api/v1/admin/settings/outlook", json={"enabled": True, "public_url": BASE})
    assert "officejs" not in client.get("/api/v1/outlook/manifest.xml").text
    assert client.put("/api/v1/admin/settings/outlook", json={"officejs_source": "cdn_only"}).status_code == 200
    assert "taskpane.html?officejs=cdn_only" in client.get("/api/v1/outlook/manifest.xml").text


def test_diag_beacon_reports_source_and_microsoftajax(client):
    from app.models import EventLog
    from sqlalchemy import select

    client.get("/api/v1/outlook/diag", params={"e": "init_timeout", "pg": "taskpane", "o": "object", "oc": "0", "sys": "undefined", "os": "local:cdn_fallback", "ms": "10001"})
    app = client.app_obj

    async def rows():
        await app.state.journal.flush()
        async with app.state.session_maker() as db:
            return (await db.execute(select(EventLog).where(EventLog.category == "outlook"))).scalars().all()
    m = client.portal.call(rows)[0].message
    assert "источник local:cdn_fallback" in m and "Sys (MicrosoftAjax) undefined" in m


def _files(office: bool = True, source: bool = True, page: bool = False, needs_ajax: bool = False):
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.startswith("/outlook/assets/"):
            lib = (b"" if needs_ajax else b"osfLoadMsAjax = false;\n") + b"/* Office JavaScript API library */ var OSF=OSF||{};" * 20
            body = {"/outlook/assets/office-js/office.js": lib if office else None,
                    "/outlook/assets/SOURCE.json": json.dumps({"source": "https://cdn.example.org/1/", "built_at": "2026-10-26T14:19:30Z", "files": 121}).encode() if source else None}.get(p)
            if page:
                return httpx.Response(200, content=b"<!doctype html><html>SPA</html>")        # прокси вернул страницу приложения вместо файла
            return httpx.Response(200, content=body) if body else httpx.Response(404)
        return httpx.Response(200, json={"enabled": True})
    return handler


def _check(tmp_path, directory, handler, mode="local"):
    with running_app(make_settings(tmp_path, app_public_url=BASE), directory, transports={"outlook": httpx.MockTransport(handler)}) as c:
        login(c, "root")
        c.put("/api/v1/admin/settings/outlook", json={"enabled": True, "officejs_source": mode})
        return {x["key"]: x for x in c.get("/api/v1/admin/outlook/status").json()["checks"]}


def test_status_reports_local_copy_present(tmp_path, directory):
    by = _check(tmp_path, directory, _files())
    assert by["officejs"]["ok"] is True and "cdn.example.org" in by["officejs"]["message"] and "2026-10-26" in by["officejs"]["message"]
    by = _check(tmp_path, directory, _files(), mode="cdn")
    assert by["officejs"]["ok"] is True and "не используются" in by["officejs"]["message"]


@pytest.mark.parametrize("kw", [{"office": False}, {"source": False}, {"page": True}])
def test_status_warns_but_does_not_fail_when_local_copy_is_missing_or_is_a_page(tmp_path, directory, kw):
    by = _check(tmp_path, directory, _files(**kw))
    assert by["officejs"]["ok"] is None and "CDN" in by["officejs"]["message"] and "--force-build" in by["officejs"]["message"]     # панель работает через запасной путь: не «ошибка», а предупреждение


def test_status_fails_when_local_copy_is_a_build_that_needs_microsoftajax(tmp_path, directory):
    """Такая сборка Office.js под CSP страниц надстройки не запустится (MicrosoftAjax разбирает JSON через eval): это ошибка, а не предупреждение."""
    by = _check(tmp_path, directory, _files(needs_ajax=True))
    assert by["officejs"]["ok"] is False and "MicrosoftAjax" in by["officejs"]["message"]
