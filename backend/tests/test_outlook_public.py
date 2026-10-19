"""Публичный центр установки Outlook: всё, что нужно клиенту и администратору Exchange, доступно БЕЗ входа, а защищённое остаётся защищённым."""
from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET

import httpx

from .conftest import login
from .test_tls_check import _pki, _serve  # noqa: F401  (цепочка для проверки выдачи сертификатов)

PUB = {"app_public_url": "https://meet.example.org"}


def _app(tmp_path, directory, **extra):
    from .conftest import make_settings, running_app
    return running_app(make_settings(tmp_path, **{**PUB, **extra}), directory, transports={"outlook": httpx.MockTransport(lambda r: httpx.Response(200, json={}))})


def _enable(c, **kw):
    login(c, "root")
    assert c.put("/api/v1/admin/settings/outlook", json={"enabled": True, **kw}).status_code == 200
    c.cookies.clear()                       # дальше — чистая клиентская сессия без входа в Peregovorka


def test_center_manifest_package_work_without_login_and_contain_no_secrets(tmp_path, directory):
    with _app(tmp_path, directory) as c:
        _enable(c, sync_mode="graph", graph_tenant_id="tid-1", graph_client_id="cid-1", graph_client_secret="S3cret!Q", ews_password="P@ssEws1")
        m = c.get("/api/v1/outlook/manifest.xml", follow_redirects=False)
        assert m.status_code == 200 and m.headers["content-type"].startswith("application/xml") and not m.is_redirect
        root = ET.fromstring(m.text)                                          # корректный XML
        assert "https://meet.example.org" in m.text and "http://" not in m.text.replace("http://schemas", "").replace("http://www.w3.org", "")
        a = c.get("/api/v1/outlook/manifest.xml").text
        assert a == m.text                                                    # постоянство: повторная выдача идентична
        p = c.get("/api/v1/outlook/package", follow_redirects=False)
        assert p.status_code == 200 and p.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(p.content))
        blob = b"".join(z.read(n) for n in z.namelist()).decode("utf-8", "ignore")
        assert "Install-OutlookAddin.ps1" in z.namelist() and "Peregovorka-Outlook.xml" in z.namelist()
        for secret in ("S3cret!Q", "P@ssEws1", "tid-1", "cid-1"):
            assert secret not in blob and secret not in c.get("/api/v1/outlook/center").text
        sc = c.get("/api/v1/outlook/scripts/Install-OutlookAddin.ps1")
        assert sc.status_code == 200 and sc.content.startswith(b"\xef\xbb\xbf") and b"New-App" in sc.content and c.get("/api/v1/outlook/scripts/evil.ps1").status_code == 404
        for secret in ("S3cret!Q", "P@ssEws1"):
            assert secret.encode() not in sc.content
        info = c.get("/api/v1/outlook/center").json()
        assert info["enabled"] and info["manifest_url"] == "https://meet.example.org/api/v1/outlook/manifest.xml" and info["compat"]["environments"]
        assert "internal" not in c.get("/api/v1/outlook/center").text.lower().replace("внутренн", "")
        # защищённое по-прежнему защищено
        assert c.get("/api/v1/admin/outlook/status").status_code in (401, 403)
        assert c.get("/api/v1/admin/outlook/package").status_code in (401, 403)
        assert c.post("/api/v1/outlook/token").status_code in (401, 403)


def test_center_disabled_exposes_nothing(tmp_path, directory):
    with _app(tmp_path, directory) as c:
        assert c.get("/api/v1/outlook/center").json()["enabled"] is False
        assert c.get("/api/v1/outlook/manifest.xml").status_code == 404 and c.get("/api/v1/outlook/package").status_code == 404
        assert c.get("/api/v1/outlook/tls/chain.pem").status_code == 404


def test_public_tls_downloads_serve_only_certificates(tmp_path, directory, monkeypatch):
    from app.services import outlook_diag as od, tls_check
    pki = _pki(tmp_path)
    pem = (pki["pem"](pki["leaf"]) + pki["pem"](pki["inter"]) + pki["pem"](pki["root_cert"])).decode()

    async def fake(app, base, **kw):
        return {"state": "ok", "ok": True, "host": "meet.example.org", "port": 443, "chain": [{"role": "leaf"}], "_chain_pem": pem, "message": "ok"}
    monkeypatch.setattr(od, "tls_snapshot", fake)
    with _app(tmp_path, directory) as c:
        _enable(c)
        full = c.get("/api/v1/outlook/tls/fullchain.pem")
        assert full.status_code == 200 and full.text.count("BEGIN CERTIFICATE") == 2 and "PRIVATE KEY" not in full.text         # корень в fullchain не входит
        assert c.get("/api/v1/outlook/tls/chain.pem").text.count("BEGIN CERTIFICATE") == 3
        der = c.get("/api/v1/outlook/tls/cert-1.cer")
        assert der.status_code == 200 and der.headers["content-type"] == "application/pkix-cert" and der.content[:1] == b"\x30"
        assert c.get("/api/v1/outlook/tls/cert-9.pem").status_code == 404 and c.get("/api/v1/outlook/tls/server.key").status_code == 404
        assert c.get("/api/v1/outlook/tls/..%2f..%2fetc%2fpasswd").status_code in (404, 422)


def test_public_api_docs_open_without_login_and_do_not_list_admin_methods(tmp_path, directory):
    with _app(tmp_path, directory) as c:
        page = c.get("/api/docs")
        assert page.status_code == 200 and "text/html" in page.headers["content-type"] and "/api/v1/outlook/manifest.xml" in page.text
        spec = c.get("/api/docs/openapi.json").json()
        assert spec["openapi"].startswith("3.1") and "/api/v1/outlook/package" in spec["paths"]
        assert not any("/admin/" in p for p in spec["paths"]) and "/api/v1/outlook/token" not in spec["paths"]
        # описание не даёт доступа: методы по-прежнему требуют ключ
        assert c.get("/api/public/v1/meetings").status_code in (401, 403, 503)
