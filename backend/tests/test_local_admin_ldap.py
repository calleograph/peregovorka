"""Локальный администратор, подключения LDAPS из веб-интерфейса, CA-сертификаты, группы администраторов из настроек. Данные — только нейтральные."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.auth.directory import DirectoryError, DirectoryIdentity
from app.services import local_admin as la
from app.services.ldap_profiles import ProfileDirectory

from .conftest import OTHER_GROUP, login, make_room, put_settings

STRONG = "Zx7-Hq3m-Pw9t-Lk2v"


def make_local_admin(client, password: str = "Qm4-Rt8w-Yn2k-Bd6x", name: str = "admin", must_change: bool = True):
    async def go():
        async with client.app_obj.state.session_maker() as db:
            await la.create_local_admin(db, name, password, must_change=must_change)
            await db.commit()

    client.portal.call(go)


def cert_pem(cn: str = "Example Root CA", ca: bool = True, days: int = 3650, issuer_cn: str | None = None, with_key: bool = False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Example Org")])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)]) if issuer_cn else subject
    now = datetime.now(timezone.utc)
    b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days)))
    if ca:
        b = b.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    cert = b.sign(key, hashes.SHA256())
    pem = cert.public_bytes(serialization.Encoding.PEM)
    der = cert.public_bytes(serialization.Encoding.DER)
    pkey = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return (pem + pkey) if with_key else pem, der


# ---------------------------------------------------------------------------------------- локальный администратор
def test_local_admin_logs_in_must_change_password_then_works_without_ldap(client, directory):
    make_local_admin(client)
    directory.fail_with = "server_unavailable"           # каталог недоступен — локальный вход от него не зависит
    r = client.post("/api/v1/auth/login", json={"login": "admin", "password": "Qm4-Rt8w-Yn2k-Bd6x"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["local"] is True and body["must_change_password"] is True and body["user"]["is_admin"] is True
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    assert client.get("/api/v1/rooms").status_code == 403           # пока пароль не сменён, остальное закрыто
    assert client.get("/api/v1/auth/me").status_code == 200
    # слабый, совпадающий и неверный текущий
    bad = client.post("/api/v1/auth/change-password", json={"current_password": "Qm4-Rt8w-Yn2k-Bd6x", "new_password": "short"})
    assert bad.status_code == 422
    assert client.post("/api/v1/auth/change-password", json={"current_password": "wrong", "new_password": STRONG}).status_code == 403
    assert client.post("/api/v1/auth/change-password", json={"current_password": "Qm4-Rt8w-Yn2k-Bd6x", "new_password": STRONG}).status_code == 204
    assert client.get("/api/v1/rooms").status_code == 200
    assert client.get("/api/v1/admin/local-admin").json()["must_change_password"] is False
    # старый пароль больше не подходит, новый — да
    client.cookies.clear()
    assert client.post("/api/v1/auth/login", json={"login": "admin", "password": "Qm4-Rt8w-Yn2k-Bd6x"}).status_code in (401, 503)
    assert client.post("/api/v1/auth/login", json={"login": "admin", "password": STRONG}).status_code == 200


def test_domain_login_still_works_and_local_name_does_not_block_it(client, directory):
    make_local_admin(client, must_change=False)
    assert login(client, "alice")                                      # доменный пользователь
    assert client.get("/api/v1/auth/me").json()["local"] is False


def test_local_admin_password_never_in_database_in_clear_and_not_in_api(client):
    make_local_admin(client)

    async def row():
        from sqlalchemy import select
        from app.models import User
        async with client.app_obj.state.session_maker() as db:
            return (await db.execute(select(User).where(User.auth_source == "local"))).scalar_one()

    u = client.portal.call(row)
    assert u.password_hash.startswith("$argon2") and "Qm4" not in u.password_hash
    login(client, "root")
    assert "password" not in client.get("/api/v1/admin/local-admin").text.lower().replace("must_change_password", "").replace("password_changed_at", "")


def test_generated_password_is_random_and_strong():
    a, b = la.generate_password(), la.generate_password()
    assert a != b and len(a.replace("-", "")) == 24 and not set(a) & set("0O1lI")
    with pytest.raises(la.LocalAdminError):
        la.validate_new_password("aaaaaaaaaaaaaaaa", "admin")           # один вид символов
    with pytest.raises(la.LocalAdminError):
        la.validate_new_password("admin-Passw0rd-xx", "admin")          # содержит имя
    la.validate_new_password(STRONG, "admin")


def test_reset_closes_sessions_and_forces_change(client):
    make_local_admin(client, must_change=False)
    assert client.post("/api/v1/auth/login", json={"login": "admin", "password": "Qm4-Rt8w-Yn2k-Bd6x"}).status_code == 200

    async def reset():
        async with client.app_obj.state.session_maker() as db:
            u = await la.find_local(db)
            await la.set_password(db, u, "Nn5-Vc8h-Dd3s-Ff7j", must_change=True)
            await db.commit()
        return await client.app_obj.state.sessions.destroy_user(str(u.id))

    assert client.portal.call(reset) == 1
    assert client.get("/api/v1/auth/me").status_code == 401           # сессия завершена
    r = client.post("/api/v1/auth/login", json={"login": "admin", "password": "Nn5-Vc8h-Dd3s-Ff7j"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True


# --------------------------------------------------------------------------------------------- группы из настроек
def test_admin_groups_from_web_settings_grant_admin_login(client, directory):
    directory.add("carol", "carol-pass", groups=(OTHER_GROUP,))
    assert login(client, "carol") and client.get("/api/v1/auth/me").json()["user"]["is_admin"] is False
    put_settings(client, "access", admin_groups=[OTHER_GROUP.upper()])
    client.cookies.clear()
    assert login(client, "carol") and client.get("/api/v1/auth/me").json()["user"]["is_admin"] is True
    login(client, "root")
    assert client.put("/api/v1/admin/settings/access", json={"admin_groups": ["не DN"]}).status_code == 422


def test_user_groups_restrict_domain_login_but_not_local_admin(client, directory):
    make_local_admin(client, must_change=False)
    put_settings(client, "access", user_groups=[OTHER_GROUP])
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"})
    assert r.status_code == 403
    assert client.post("/api/v1/auth/login", json={"login": "admin", "password": "Qm4-Rt8w-Yn2k-Bd6x"}).status_code == 200


# -------------------------------------------------------------------------------------------------------- CA
def test_ca_inspect_add_list_delete_and_bundle_file(client, settings):
    login(client, "root")
    pem, der = cert_pem("Example Root CA")
    info = client.post("/api/v1/admin/ca/inspect", json={"pem": pem.decode()}).json()["items"][0]
    assert "Example Root CA" in info["subject"] and len(info["sha256"]) == 64 and info["is_ca"] and info["self_signed"] and info["type"].startswith("корневой")
    assert info["serial"] and info["not_after"] > info["not_before"]
    assert client.get("/api/v1/admin/ca").json()["items"] == []        # осмотр ничего не сохраняет
    r = client.post("/api/v1/admin/ca", json={"pem": pem.decode(), "label": "Корневой CA"})
    assert r.status_code == 201 and len(r.json()["added"]) == 1
    items = client.get("/api/v1/admin/ca").json()["items"]
    assert len(items) == 1 and items[0]["label"] == "Корневой CA"
    bundle = (__import__("pathlib").Path(settings.data_dir) / "ca" / "bundle.pem")
    assert bundle.read_text().count("BEGIN CERTIFICATE") == 1
    again = client.post("/api/v1/admin/ca", json={"data_base64": base64.b64encode(der).decode()}).json()          # тот же сертификат в DER
    assert again["added"] == [] and len(again["already_present"]) == 1
    assert client.delete(f"/api/v1/admin/ca/{items[0]['id']}").status_code == 204
    assert not bundle.exists()
    actions = [a["action"] for a in client.get("/api/v1/admin/audit").json()]
    assert "ca.add" in actions and "ca.delete" in actions


def test_ca_rejects_private_keys_garbage_and_non_ca_without_confirmation(client):
    login(client, "root")
    pem_with_key, _ = cert_pem("Example Root CA", with_key=True)
    r = client.post("/api/v1/admin/ca", json={"pem": pem_with_key.decode()})
    assert r.status_code == 422 and "закрытый ключ" in r.json()["detail"].lower()
    assert client.get("/api/v1/admin/ca").json()["items"] == []
    assert client.post("/api/v1/admin/ca", json={"pem": "это не сертификат"}).status_code == 422
    assert client.post("/api/v1/admin/ca", json={"pem": ""}).status_code == 422
    leaf, _ = cert_pem("dc1.example.local", ca=False, issuer_cn="Example Root CA")
    r = client.post("/api/v1/admin/ca", json={"pem": leaf.decode()})
    assert r.status_code == 422 and "CA" in r.json()["detail"]
    assert client.post("/api/v1/admin/ca", json={"pem": leaf.decode(), "confirm_non_ca": True}).status_code == 201
    login(client, "alice")
    assert client.get("/api/v1/admin/ca").status_code == 403


# -------------------------------------------------------------------------------------------- LDAP-подключения
def ldap_body(**over):
    body = {"name": "Каталог 1", "host": "dc1.example.local", "port": 636, "protocol": "ldaps", "base_dn": "DC=example,DC=local",
            "upn_suffix": "example.local", "netbios_domain": "EXAMPLE", "timeout_s": 5, "bind_dn": "CN=svc-read,OU=Service,DC=example,DC=local",
            "secret": "Svc-Pass-123!", "use_for_users": True, "use_for_admins": True}
    body.update(over)
    return body


def test_ldap_profile_crud_secret_never_returned(client):
    login(client, "root")
    r = client.post("/api/v1/admin/ldap-profiles", json=ldap_body())
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["secret_set"] is True and "Svc-Pass" not in r.text and p["uri"] == "ldaps://dc1.example.local:636"
    assert "Svc-Pass" not in client.get("/api/v1/admin/ldap-profiles").text
    # без пароля — не меняется; с новым — меняется; пустой — запрещён
    assert client.patch(f"/api/v1/admin/ldap-profiles/{p['id']}", json={"name": "Каталог 1б", "port": 3269}).json()["port"] == 3269
    assert client.patch(f"/api/v1/admin/ldap-profiles/{p['id']}", json={"secret": ""}).status_code == 422
    assert client.patch(f"/api/v1/admin/ldap-profiles/{p['id']}", json={"secret": "New-Pass-456!"}).json()["secret_set"] is True
    # адрес со схемой и портом нормализуется
    q = client.post("/api/v1/admin/ldap-profiles", json=ldap_body(name="Каталог 2", host="ldaps://dc2.example.local:3269")).json()
    assert q["host"] == "dc2.example.local" and q["port"] == 3269
    # проверки значений
    for bad in ({"host": "bad host!"}, {"protocol": "ldap"}, {"port": 70000}, {"base_dn": "без знака равно"}, {"timeout_s": 0}, {"email_attribute": "m ail"}):
        assert client.post("/api/v1/admin/ldap-profiles", json=ldap_body(name="x" + str(len(str(bad))), **bad)).status_code == 422, bad
    assert client.post("/api/v1/admin/ldap-profiles", json=ldap_body(name="Каталог 1б")).status_code == 422   # то же название
    assert client.delete(f"/api/v1/admin/ldap-profiles/{q['id']}").status_code == 204
    audit = client.get("/api/v1/admin/audit").text
    assert "ldap.create" in audit and "Svc-Pass" not in audit and "New-Pass" not in audit
    login(client, "alice")
    assert client.get("/api/v1/admin/ldap-profiles").status_code == 403


def test_ldap_test_reports_stages_without_ca_and_for_unreachable_host(client):
    login(client, "root")
    p = client.post("/api/v1/admin/ldap-profiles", json=ldap_body(host="127.0.0.1", port=1)).json()
    res = client.post(f"/api/v1/admin/ldap-profiles/{p['id']}/test").json()
    assert res["ok"] is False and res["stages"][0]["stage"] == "config" and "CA" in res["stages"][0]["message"]
    pem, _ = cert_pem()
    client.post("/api/v1/admin/ca", json={"pem": pem.decode()})
    res = client.post(f"/api/v1/admin/ldap-profiles/{p['id']}/test").json()
    stages = {s["stage"]: s for s in res["stages"]}
    assert res["ok"] is False and stages["dns"]["ok"] is True and stages["tcp"]["ok"] is False and "TCP" in stages["tcp"]["message"]
    assert "Svc-Pass" not in str(res)
    nodns = client.post("/api/v1/admin/ldap-profiles", json=ldap_body(name="n", host="no-such-host.invalid")).json()
    res = client.post(f"/api/v1/admin/ldap-profiles/{nodns['id']}/test").json()
    assert [s["stage"] for s in res["stages"]][-1] == "dns" and res["stages"][-1]["ok"] is False


def test_setup_status_wizard_and_complete(client):
    make_local_admin(client, must_change=False)
    client.headers["X-CSRF-Token"] = client.post("/api/v1/auth/login", json={"login": "admin", "password": "Qm4-Rt8w-Yn2k-Bd6x"}).json()["csrf_token"]
    st = client.get("/api/v1/admin/setup/status").json()
    assert st["show"] is True and st["completed"] is False and {s["id"]: s["done"] for s in st["steps"]}["ldap"] is False
    client.post("/api/v1/admin/ldap-profiles", json=ldap_body())
    assert {s["id"]: s["done"] for s in client.get("/api/v1/admin/setup/status").json()["steps"]}["ldap"] is True
    assert client.post("/api/v1/admin/setup/complete", json={"skipped": ["mail"]}).json()["completed"] is True
    st = client.get("/api/v1/admin/setup/status").json()
    assert st["show"] is False and st["skipped"] == ["mail"]


# --------------------------------------------------------------------------- составной каталог (несколько подключений)
class _FakeLdap:
    users: dict[tuple[str, str], tuple[str, DirectoryIdentity]] = {}
    down: set[str] = set()

    def __init__(self, cfg):
        self.cfg = cfg

    def authenticate(self, login: str, password: str):
        if self.cfg.name in self.down:
            raise DirectoryError("server_unavailable")
        rec = self.users.get((self.cfg.name, login.split("\\")[-1].split("@")[0].lower()))
        if rec is None:
            raise DirectoryError("user_not_found")
        if rec[0] != password:
            raise DirectoryError("invalid_credentials")
        i = rec[1]
        return DirectoryIdentity(i.ad_guid, i.dn, i.sam_account_name, i.upn, i.display_name, i.email, i.groups, self.cfg.name, self.cfg.use_for_users, self.cfg.use_for_admins)

    def check_service_account(self):
        return None

    def search(self, kind, query, limit=20):
        return []


def _ident(sam: str) -> DirectoryIdentity:
    return DirectoryIdentity(f"guid-{sam}", f"cn={sam}", sam, None, sam.title(), f"{sam}@example.local", frozenset())


def test_profile_directory_order_domain_hint_and_unavailable_fallthrough(client, monkeypatch, settings):
    monkeypatch.setattr("app.services.ldap_profiles.LdapDirectory", _FakeLdap)
    _FakeLdap.users = {("Каталог A", "user1"): ("pw-a", _ident("user1")), ("Каталог B", "user2"): ("pw-b", _ident("user2")), ("Каталог B", "user1"): ("pw-b1", _ident("user1"))}
    _FakeLdap.down = set()
    login(client, "root")
    pem, _ = cert_pem()
    client.post("/api/v1/admin/ca", json={"pem": pem.decode()})
    client.post("/api/v1/admin/ldap-profiles", json=ldap_body(name="Каталог A", host="dc-a.example.local", netbios_domain="AAA", upn_suffix="a.example.local"))
    client.post("/api/v1/admin/ldap-profiles", json=ldap_body(name="Каталог B", host="dc-b.example.local", netbios_domain="BBB", upn_suffix="b.example.local", use_for_admins=False))

    async def build():
        d = ProfileDirectory(settings, client.app_obj.state.ldap)
        async with client.app_obj.state.session_maker() as db:
            await d.reload(db)
        return d

    d = client.portal.call(build)
    assert d.configured and d.authenticate("user1", "pw-a").source == "Каталог A"
    assert d.authenticate("user2", "pw-b").source == "Каталог B"                 # не найден в A — найден в B
    assert d.authenticate("BBB\\user1", "pw-b1").source == "Каталог B"            # подсказка домена выбирает подключение
    assert d.authenticate("user1@b.example.local", "pw-b1").for_admins is False
    with pytest.raises(DirectoryError) as e:
        d.authenticate("user1", "wrong")                                           # неверный пароль: решение принято, B не опрашивается
    assert e.value.code == "invalid_credentials"
    _FakeLdap.down = {"Каталог A"}
    assert d.authenticate("user2", "pw-b").source == "Каталог B"                  # A недоступен — работает B
    _FakeLdap.down = {"Каталог A", "Каталог B"}
    with pytest.raises(DirectoryError) as e:
        d.authenticate("user2", "pw-b")
    assert e.value.code == "server_unavailable"                                    # недоступность — не «пользователь не найден»


def test_no_profiles_means_not_configured_not_a_crash(client, settings):
    async def build():
        d = ProfileDirectory(settings, client.app_obj.state.ldap)
        async with client.app_obj.state.session_maker() as db:
            await d.reload(db)
        return d

    d = client.portal.call(build)
    assert not d.configured
    with pytest.raises(DirectoryError) as e:
        d.authenticate("user1", "x")
    assert e.value.code == "not_configured"
