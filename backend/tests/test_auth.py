from __future__ import annotations

import pytest

from app.auth.directory import DirectoryError, classify_bind_failure, parse_login
from app.logging_setup import scrub

from .conftest import login


def test_login_success_sets_secure_session_cookie(client, directory):
    r = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["sam_account_name"] == "alice" and body["user"]["is_admin"] is False
    assert body["csrf_token"]
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    # пароль никогда не возвращается и не попадает в ответ
    assert "alice-pass" not in r.text


def test_admin_flag_comes_from_ad_group(client):
    login(client, "root")
    assert client.get("/api/v1/auth/me").json()["user"]["is_admin"] is True


def test_wrong_password_is_generic_and_unknown_user_looks_the_same(client):
    a = client.post("/api/v1/auth/login", json={"login": "alice", "password": "nope"})
    b = client.post("/api/v1/auth/login", json={"login": "ghost", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_throttle_stops_calls_to_ad_before_lockout_threshold(client, directory):
    async def captcha_off():                          # CAPTCHA по умолчанию включается на тех же 20 неудачах; здесь проверяется именно блокировка входа
        async with client.app_obj.state.session_maker() as db:
            await client.app_obj.state.settings_svc.update(db, "captcha", {"mode": "off"}, actor="test")
    client.portal.call(captcha_off)
    for _ in range(20):  # LOGIN_MAX_FAILURES_PER_USER по умолчанию = 20
        assert client.post("/api/v1/auth/login", json={"login": "alice", "password": "bad"}).status_code == 401
    calls_before = directory.calls
    r = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"})  # даже верный пароль
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert 0 < int(r.headers["Retry-After"]) <= 300, "блокировка входа — 5 минут"
    assert directory.calls == calls_before, "при блокировке в AD обращаться нельзя"


def test_success_resets_failure_counter(client):
    for _ in range(2):
        client.post("/api/v1/auth/login", json={"login": "bob", "password": "bad"})
    assert client.post("/api/v1/auth/login", json={"login": "bob", "password": "bob-pass"}).status_code == 200
    for _ in range(2):
        assert client.post("/api/v1/auth/login", json={"login": "bob", "password": "bad"}).status_code == 401


def test_directory_outage_is_503_not_credentials_error(client, directory):
    directory.fail_with = "server_unavailable"
    r = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"})
    assert r.status_code == 503


def test_locked_account_is_reported_but_does_not_count_as_guessing(client, directory):
    directory.fail_with = "account_locked"
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"login": "alice", "password": "x"}).status_code == 403


def test_requires_session_and_csrf(client):
    assert client.get("/api/v1/rooms").status_code == 401
    login(client, "alice")
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/auth/logout").status_code == 403  # нет CSRF-токена
    client.headers["X-CSRF-Token"] = "wrong"
    assert client.post("/api/v1/auth/logout").status_code == 403


def test_logout_destroys_session(client):
    login(client, "alice")
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_foreign_origin_rejected_on_login(client):
    r = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_origin_from_allowed_origins_is_accepted_on_login_and_others_are_not(client):
    client.app.state.settings.allowed_origins = "https://Meet.Example.org/, https://192.0.2.10"
    ok = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"}, headers={"Origin": "https://meet.example.org"})
    assert ok.status_code == 200
    assert client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"},
                       headers={"Origin": "https://192.0.2.10"}).status_code == 200
    bad = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"}, headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403 and bad.json()["detail"].startswith("Недопустимый Origin. Откройте приложение по адресу ")


def test_origin_list_is_normalized_and_never_matches_null_or_other_hosts():
    from app.config import Settings, origin_allowed

    s = Settings(app_public_url="https://Meet.Example.org/", allowed_origins="https://192.0.2.10:8443/")
    assert origin_allowed("https://meet.example.org", s)
    assert origin_allowed("https://192.0.2.10:8443", s)
    assert not origin_allowed("https://192.0.2.10", s)
    assert not origin_allowed("null", s)
    assert not origin_allowed("https://meet.example.org.evil.example", s)


def test_default_ports_ipv6_and_trailing_slash_match_what_browsers_send():
    from app.config import Settings, origin_allowed

    s = Settings(app_public_url="https://meet.example.org:443/", allowed_origins="http://intranet.example:80, https://[2001:db8::1]:8443")
    assert origin_allowed("https://meet.example.org", s) and origin_allowed("https://meet.example.org/", s)
    assert origin_allowed("http://intranet.example", s)
    assert origin_allowed("https://[2001:db8::1]:8443", s)
    assert not origin_allowed("https://meet.example.org:444", s)
    assert not origin_allowed("http://meet.example.org", s)          # схема тоже часть адреса


@pytest.mark.parametrize("origin", ["null", "", "https://", "://", "https://meet.example.org/evil", "https://meet.example.org?x=1",
                                    "https://user@meet.example.org", "https://meet.example.org:notaport"])
def test_malformed_origin_is_never_allowed(origin):
    from app.config import Settings, origin_allowed

    assert not origin_allowed(origin, Settings(app_public_url="https://meet.example.org"))


@pytest.mark.parametrize("public_url", ["meet.example.org", "", "ftp://meet.example.org", "https://"])
def test_a_broken_public_url_does_not_turn_null_origin_into_an_allowed_one(public_url):
    from app.config import Settings, origin_allowed

    s = Settings(app_public_url=public_url)
    assert not origin_allowed("null", s) and not origin_allowed("://", s) and not origin_allowed("", s)


@pytest.mark.parametrize("value", ["meet.example.org", "ftp://meet.example.org", "https://meet.example.org/path", "https://meet.example.org?x=1"])
def test_allowed_origins_must_be_plain_scheme_and_host(value):
    from app.config import Settings

    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        Settings(allowed_origins=value)


def test_allowed_origins_accepts_a_list_of_plain_urls():
    from app.config import Settings

    assert Settings(allowed_origins="https://a.example, http://10.0.0.5:8080").allowed_origin_set >= {"https://a.example", "http://10.0.0.5:8080"}


def test_access_group_restricts_login(tmp_path, directory):
    from fakeredis import FakeAsyncRedis
    from fastapi.testclient import TestClient

    from app.main import create_app
    from .conftest import STAFF_GROUP, make_settings

    s = make_settings(tmp_path, ldap_access_group_dn=STAFF_GROUP)
    app = create_app(s, redis_factory=lambda _: FakeAsyncRedis(decode_responses=True),
                     directory_factory=lambda _: directory, start_workers=False)
    with TestClient(app, base_url="http://testserver") as c:
        from app.models import Base

        async def _create():
            async with app.state.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        c.portal.call(_create)
        assert c.post("/api/v1/auth/login", json={"login": "carol", "password": "carol-pass"}).status_code == 403
        assert c.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"}).status_code == 200


# ---- чистые функции каталога
@pytest.mark.parametrize("raw,expected", [("alice", ("alice", False)), ("CORP\\alice", ("alice", False)),
                                          ("alice@corp.test", ("alice@corp.test", True))])
def test_parse_login(raw, expected):
    assert parse_login(raw) == expected


@pytest.mark.parametrize("raw", ["", "a b", "a)(uid=*", "*", "a\\", "a@b@c", "x" * 300])
def test_parse_login_rejects_ldap_injection_and_garbage(raw):
    with pytest.raises(DirectoryError):
        parse_login(raw)


@pytest.mark.parametrize("message,code", [
    ("AcceptSecurityContext error, data 52e, v4563", "invalid_credentials"),
    ("AcceptSecurityContext error, data 775, v4563", "account_locked"),
    ("AcceptSecurityContext error, data 533, v4563", "account_disabled"),
    ("AcceptSecurityContext error, data 532, v4563", "password_expired"),
    ("AcceptSecurityContext error, data 773, v4563", "password_must_change"),
    ("AcceptSecurityContext error, data 52f, v4563", "account_restriction"),
])
def test_classify_bind_failure(message, code):
    assert classify_bind_failure({"result": 49, "description": "invalidCredentials", "message": message}) == code


def test_log_scrubbing_hides_secrets():
    out = scrub({"password": "p", "nested": {"api_key": "k", "ok": 1}, "msg": "token=abc123 password: hunter2"})
    assert out["password"] == "***" and out["nested"]["api_key"] == "***" and out["nested"]["ok"] == 1
    assert "abc123" not in out["msg"] and "hunter2" not in out["msg"]


@pytest.mark.parametrize("uri", ["ldaps://AD1.corp.test:636", "ldaps://AD1.corp.test:636/", "ldaps://AD1.corp.test"])
def test_ldap_uri_forms_with_and_without_trailing_slash_are_equivalent(tmp_path, uri):
    from app.auth.directory import LdapDirectory
    from .conftest import make_settings

    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    d = LdapDirectory(make_settings(tmp_path, ldap_uris=uri, ldap_ca_file=str(ca)))
    servers = d._server_pool().servers
    assert [(s.host.lower(), s.port) for s in servers] == [("ad1.corp.test", 636)]

