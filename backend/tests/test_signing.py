"""Подпись клиента Windows и корпоративные сборки: права администратора и аудит, подключение и отключение агента, сертификаты (свой, организации,
без назначения «Подпись кода», истёкший), сборки (только подтверждённые пакеты с действительной подписью нужным сертификатом), две организации
с разными сертификатами, корпоративный канал обновлений (подпись издателя проверяется), отсутствие секретов в ответах и базе."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.api import signing as sig_api

from .conftest import login

API = "/api/v1"


def make_cert(cn: str, *, eku=True, days=(-1, 365)) -> tuple[bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.ORGANIZATION_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now + dt.timedelta(days=days[0])).not_valid_after(now + dt.timedelta(days=days[1])))
    if eku:
        b = b.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
    der = b.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)
    return der, hashlib.sha1(der).hexdigest().upper()  # noqa: S324 — отпечаток Windows


def enroll(c, name="Узел подписи"):
    r = admin(c).post(f"{API}/admin/signing/enroll", json={"name": name})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    code = r.json()["code"]
    a = c.post(f"{API}/signing/agent/enroll", json={"code": code, "host": "SIGN-01", "version": "1.1.0"})
    assert a.status_code == 200, a.text
    assert c.post(f"{API}/signing/agent/enroll", json={"code": code}).status_code == 401, "код одноразовый"
    return a.json()["agent_id"], {"Authorization": f"Bearer {a.json()['token']}"}


def poll(c, h):
    r = c.post(f"{API}/signing/agent/poll", json={"version": "1.1.0", "info": {"os": "Windows Server 2025"}}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["job"]


def as_user(c, user):
    """Вход только при смене пользователя: иначе тест упрётся в лимит одновременных сеансов."""
    if getattr(c, "_as", None) != user:
        login(c, user)
        c._as = user
    return c


def admin(c):
    return as_user(c, "root")


def cert_job(c, h, kind_path, body, der, check=None):
    r = admin(c).post(f"{API}/admin/signing/certs/{kind_path}", json=body)
    assert r.status_code == 200, r.text
    job = poll(c, h)
    assert job and job["id"] == r.json()["id"]
    chk = {"has_private_key": True, "chain_ok": True, "can_sign": True, "key_exportable": False, **(check or {})}
    f = c.post(f"{API}/signing/agent/jobs/{job['id']}/finish", json={"ok": True, "cert": {"cer_b64": base64.b64encode(der).decode(), "check": chk}}, headers=h)
    assert f.status_code == 200, f.text
    return admin(c).get(f"{API}/admin/signing/jobs/{job['id']}").json()


def own_cert(c, h, agent_id, cn="ООО Пример"):
    der, tp = make_cert(cn)
    j = cert_job(c, h, "create", {"agent_id": agent_id, "subject": cn}, der)
    assert j["status"] == "done", j
    return j["cert_id"], tp


def test_only_admins_and_every_action_is_audited(client):
    as_user(client, "alice")
    for m, p in (("get", "/admin/signing"), ("post", "/admin/signing/enroll"), ("post", "/admin/signing/builds"), ("post", "/admin/signing/channel/publish")):
        assert getattr(client, m)(f"{API}{p}", **({"json": {}} if m == "post" else {})).status_code == 403, p
    agent_id, h = enroll(client)
    own_cert(client, h, agent_id)
    acts = {a["action"] for a in admin(client).get(f"{API}/admin/audit").json()}
    assert {"signing.enroll_code", "signing.agent_enrolled", "signing.create_cert", "signing.create_cert_done"} <= acts


def test_agent_token_is_required_never_stored_and_revocable(client):
    agent_id, h = enroll(client)
    assert client.post(f"{API}/signing/agent/poll", json={}).status_code == 401
    assert client.post(f"{API}/signing/agent/poll", json={}, headers={"Authorization": "Bearer pgs_wrong"}).status_code == 401
    assert client.post(f"{API}/signing/agent/poll", json={}, headers={**h, "Origin": "http://testserver"}).status_code == 403, "из браузера — нельзя"
    tok = h["Authorization"][7:]

    async def stored():
        from sqlalchemy import text
        async with client.app_obj.state.engine.begin() as conn:
            return [tuple(r) for r in (await conn.execute(text("select token_hash from signing_agents"))).all()]
    rows = client.portal.call(stored)
    assert rows == [(hashlib.sha256(tok.encode()).hexdigest(),)], "в базе только хеш токена"
    ov = admin(client).get(f"{API}/admin/signing").json()
    assert ov["agents"][0]["online"] and tok not in json.dumps(ov)
    assert admin(client).delete(f"{API}/admin/signing/agents/{agent_id}").status_code == 200
    assert client.post(f"{API}/signing/agent/poll", json={}, headers=h).status_code == 401, "отключённый агент больше не получает задания"


def test_own_certificate_activation_and_public_cer_download(client):
    agent_id, h = enroll(client)
    cert_id, tp = own_cert(client, h, agent_id)
    c = next(x for x in admin(client).get(f"{API}/admin/signing").json()["certs"] if x["id"] == cert_id)
    assert c["kind"] == "self" and c["thumbprint"] == tp and c["state"] == "ok" and c["check"]["code_signing"] and c["check"]["self_signed"]
    assert c["subject"].startswith("O=ООО Пример") or "CN=ООО Пример" in c["subject"]
    r = admin(client).get(f"{API}/admin/signing/certs/{cert_id}/cer")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pkix-cert"
    assert x509.load_der_x509_certificate(r.content) and b"PRIVATE" not in r.content, "только открытая часть"
    assert admin(client).post(f"{API}/admin/signing/certs/{cert_id}/activate").json()["active"]


def test_certificate_without_code_signing_or_expired_is_rejected(client):
    agent_id, h = enroll(client)
    der, tp = make_cert("Без назначения", eku=False)
    j = cert_job(client, h, "register", {"agent_id": agent_id, "thumbprint": tp}, der)
    assert j["status"] == "failed" and "Подпись кода" in j["error"]
    der, tp = make_cert("Истёкший", days=(-400, -1))
    j = cert_job(client, h, "register", {"agent_id": agent_id, "thumbprint": tp}, der)
    assert j["status"] == "failed" and "Срок действия" in j["error"]
    der, _ = make_cert("Подмена")
    j = cert_job(client, h, "register", {"agent_id": agent_id, "thumbprint": "A" * 40}, der)
    assert j["status"] == "failed" and "не совпадает" in j["error"], "агент вернул не тот сертификат"
    certs = admin(client).get(f"{API}/admin/signing").json()["certs"]
    for c in certs:
        if c["subject"].endswith("Истёкший") or "Истёкший" in c["subject"]:
            assert c["state"] == "expired"
            r = admin(client).post(f"{API}/admin/signing/builds", json={"cert_id": c["id"], "version": "1.0.1", "server_url": "https://meet.example.org", "org_name": "X", "types": ["msi"]})
            assert r.status_code == 409, "истёкшим сертификатом сборку не поставить"
    assert admin(client).post(f"{API}/admin/signing/certs/register", json={"agent_id": agent_id, "thumbprint": "xyz"}).status_code == 422


def build(client, h, cert_id, org, server, files_ok=True, thumb=None, upload=True):
    r = admin(client).post(f"{API}/admin/signing/builds", json={"cert_id": cert_id, "version": "1.0.1", "server_url": server, "org_name": org, "types": ["exe", "msi"],
                                                                   "update_policy": "channel", "autostart": "tray", "allow_add": False})
    assert r.status_code == 200, r.text
    job = poll(client, h)
    p = job["params"]
    assert p["server_url"] == server and p["org_name"] == org and p["update_url"].endswith("/api/v1/desktop/channel/update.json") and "url" not in json.dumps(p).replace("server_url", "").replace("update_url", "")
    files = []
    for name in (f"Peregovorka-Setup-1.0.1-{server[8:]}.exe", f"Peregovorka-1.0.1-{server[8:]}-x64.msi"):
        data = name.encode() * 100
        if upload:
            u = client.put(f"{API}/signing/agent/jobs/{job['id']}/files/{name}", content=data, headers=h)
            assert u.status_code == 200, u.text
        files.append({"name": name, "sha256": hashlib.sha256(data if files_ok else b"other").hexdigest(),
                      "signature": {"status": "Valid", "thumbprint": thumb or p["thumbprint"], "subject": "CN=x"}})
    client.post(f"{API}/signing/agent/jobs/{job['id']}/finish", json={"ok": True, "files": files}, headers=h)
    return admin(client).get(f"{API}/admin/signing/jobs/{job['id']}").json()


def test_build_accepts_only_uploaded_files_signed_by_the_chosen_certificate(client):
    agent_id, h = enroll(client)
    cert_id, tp = own_cert(client, h, agent_id)
    j = build(client, h, cert_id, "Головной офис", "https://meet.example.org", files_ok=False)
    assert j["status"] == "failed" and "SHA-256" in j["error"]
    j = build(client, h, cert_id, "Головной офис", "https://meet.example.org", thumb="B" * 40)
    assert j["status"] == "failed" and "не подтверждена" in j["error"], "подписано не тем сертификатом"
    j = build(client, h, cert_id, "Головной офис", "https://meet.example.org", upload=False)
    assert j["status"] == "failed" and "не загружен" in j["error"]
    j = build(client, h, cert_id, "Головной офис", "https://meet.example.org")
    assert j["status"] == "done" and len(j["files"]) == 2 and all(f["signature"]["thumbprint"] == tp for f in j["files"])
    d = admin(client).get(f"{API}/admin/signing/jobs/{j['id']}/files/{j['files'][0]['name']}")
    assert d.status_code == 200 and hashlib.sha256(d.content).hexdigest() == j["files"][0]["sha256"]
    assert admin(client).get(f"{API}/admin/signing/jobs/{j['id']}/files/..%2F..%2Fdev.db").status_code == 404
    as_user(client, "alice")
    assert client.get(f"{API}/admin/signing/jobs/{j['id']}/files/{j['files'][0]['name']}").status_code == 403


def test_agent_cannot_upload_to_finished_or_foreign_jobs_or_bad_names(client):
    a1, h1 = enroll(client, "Узел А")
    a2, h2 = enroll(client, "Узел Б")
    c1, _ = own_cert(client, h1, a1, "Организация А")
    r = admin(client).post(f"{API}/admin/signing/builds", json={"cert_id": c1, "version": "1.0.1", "server_url": "https://a.example.org", "org_name": "А", "types": ["exe"]})
    job = poll(client, h1)
    assert client.put(f"{API}/signing/agent/jobs/{job['id']}/files/x.exe", content=b"1", headers=h2).status_code == 404, "чужое задание"
    for bad in ("..%2Fx.exe", "x.ps1", ".hidden.exe"):
        assert client.put(f"{API}/signing/agent/jobs/{job['id']}/files/{bad}", content=b"1", headers=h1).status_code in (404, 422), bad
    assert r.status_code == 200


def test_two_organisations_with_different_certificates(client):
    a1, h1 = enroll(client, "Узел А")
    a2, h2 = enroll(client, "Узел Б")
    c1, tp1 = own_cert(client, h1, a1, "Организация А")
    c2, tp2 = own_cert(client, h2, a2, "Организация Б")
    assert tp1 != tp2
    j1 = build(client, h1, c1, "Организация А", "https://a.example.org")
    j2 = build(client, h2, c2, "Организация Б", "https://b.example.org")
    assert j1["status"] == j2["status"] == "done"
    assert {f["signature"]["thumbprint"] for f in j1["files"]} == {tp1} and {f["signature"]["thumbprint"] for f in j2["files"]} == {tp2}
    assert j1["agent_id"] == a1 and j2["agent_id"] == a2, "сборка уходит на узел, где лежит ключ выбранного сертификата"
    assert poll(client, h1) is None and poll(client, h2) is None


@pytest.fixture
def feed(monkeypatch):
    key = ed25519.Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    exe = b"MZ" + b"installer" * 1000
    man = json.dumps({"product": "peregovorka-desktop", "version": "1.2.0", "released": "2026-10-24", "notes": "Исправления",
                      "files": [{"kind": "nsis", "name": "Peregovorka-Setup-1.2.0.exe", "url": "https://example.org/dl/Peregovorka-Setup-1.2.0.exe", "sha256": hashlib.sha256(exe).hexdigest(), "size": len(exe)}]}).encode()
    state = {"sig": base64.b64encode(key.sign(man)).decode(), "exe": exe}

    def handler(req: httpx.Request):
        if req.url.path.endswith("update.json"):
            return httpx.Response(200, content=man)
        if req.url.path.endswith(".sig"):
            return httpx.Response(200, text=state["sig"])
        return httpx.Response(200, content=state["exe"])
    real = httpx.AsyncClient
    monkeypatch.setattr(sig_api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return pub, state


def test_corporate_channel_publishes_only_publisher_signed_versions(client, feed):
    pub, state = feed
    assert admin(client).post(f"{API}/admin/signing/channel/check").status_code == 409, "без открытого ключа издателя проверять нечем"
    assert admin(client).put(f"{API}/admin/settings/desktop_channel", json={"pubkeys": "не ключ"}).status_code == 422
    assert admin(client).put(f"{API}/admin/settings/desktop_channel", json={"pubkeys": pub}).status_code == 200
    r = admin(client).post(f"{API}/admin/signing/channel/check")
    assert r.status_code == 200 and r.json()["version"] == "1.2.0" and r.json()["verified"]
    assert client.get(f"{API}/desktop/channel/update.json").status_code == 404, "пока не опубликовано"
    good_sig = state["sig"]
    state["sig"] = base64.b64encode(b"\0" * 64).decode()
    assert admin(client).post(f"{API}/admin/signing/channel/publish").status_code == 422, "чужая подпись"
    state["sig"], exe = good_sig, state["exe"]
    state["exe"] = b"MZ tampered"
    assert admin(client).post(f"{API}/admin/signing/channel/publish").status_code == 422, "установщик не совпал с манифестом"
    state["exe"] = exe
    r = admin(client).post(f"{API}/admin/signing/channel/publish")
    assert r.status_code == 200 and r.json()["version"] == "1.2.0", r.text
    m = client.get(f"{API}/desktop/channel/update.json")
    s = client.get(f"{API}/desktop/channel/update.json.sig").text
    ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(base64.b64decode(s), m.content)
    assert client.get(f"{API}/desktop/channel/Peregovorka-Setup-1.2.0.exe").content == exe
    assert client.get(f"{API}/desktop/channel/channel.json").status_code == 404 and client.get(f"{API}/desktop/channel/..%2Fdev.db").status_code == 404
    assert "signing.channel_publish" in {a["action"] for a in admin(client).get(f"{API}/admin/audit").json()}
