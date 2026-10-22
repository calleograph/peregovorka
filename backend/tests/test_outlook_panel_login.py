"""Боковая панель Outlook: вход логином и паролем без сеанса браузера, второй фактор — через окно, продление входа, отзыв при отключении учётной записи."""
from __future__ import annotations

from app.services import outlook as ol

from .conftest import login
from .test_outlook import O, _anon, _body, _enable

API = "/api/v1"


def _panel_login(client, user="alice", password=None, **headers):
    _anon(client)
    return client.post(f"{O}/login", json={"login": user, "password": password or f"{user}-pass"}, headers=headers)


def test_panel_login_issues_addin_token_without_browser_session(client):
    _enable(client)
    bad = _panel_login(client, password="wrong")
    assert bad.status_code == 401 and bad.json()["detail"]["code"] == "invalid_credentials"
    r = _panel_login(client)
    assert r.status_code == 200 and r.json()["token"].startswith("pgo_") and r.json()["expires_in"] == 168 * 3600, "по умолчанию вход держится 7 суток"
    assert "set-cookie" not in r.headers and r.headers["cache-control"] == "no-store", "сеанс браузера не открывается"
    h = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get(f"{O}/me", headers=h).json()["name"]
    assert client.post(f"{O}/meetings", json=_body(), headers=h).status_code == 200
    login(client, "alice")
    assert len(client.get(f"{API}/auth/sessions").json()) == 1, "вход в панели не занял место в лимите сеансов"
    assert _panel_login(client, Origin="https://evil.example.net").status_code == 403


def test_panel_login_respects_integration_switch_groups_and_second_factor(client):
    _anon(client)
    assert _panel_login(client).status_code == 404, "интеграция выключена"
    _enable(client, allowed_groups="cn=nobody,dc=example,dc=org")
    assert _panel_login(client).status_code == 403, "не в разрешённой группе"
    _enable(client, allowed_groups="")
    login(client, "root")
    assert client.put(f"{API}/admin/settings/auth_security", json={"totp_policy": "all"}).status_code == 200
    r = _panel_login(client)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "browser_required" and r.json()["detail"]["reason"] == "totp_setup", "2FA проходит в окне входа, не в панели"


def test_token_refresh_rotates_and_has_absolute_limit(client, monkeypatch):
    _enable(client)
    old = {"Authorization": "Bearer " + _panel_login(client).json()["token"]}
    _anon(client)
    r = client.post(f"{O}/token/refresh", headers=old)
    assert r.status_code == 200
    new = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get(f"{O}/me", headers=old).status_code == 401, "прежний токен отозван сразу"
    assert client.get(f"{O}/me", headers=new).status_code == 200
    monkeypatch.setattr(ol, "MAX_TOKEN_LIFETIME_S", 1800)                       # предел от входа паролем исчерпан
    assert client.post(f"{O}/token/refresh", headers=new).status_code == 401
    assert client.post(f"{O}/token/refresh").status_code == 401


def test_deactivated_user_loses_addin_access_immediately(client):
    _enable(client)
    h = {"Authorization": "Bearer " + _panel_login(client, "bob").json()["token"]}
    login(client, "root")
    uid = next(u["id"] for u in client.get(f"{API}/admin/users").json() if u["sam_account_name"] == "bob")
    assert client.patch(f"{API}/admin/users/{uid}", json={"is_active": False}).status_code == 200
    _anon(client)
    assert client.get(f"{O}/me", headers=h).status_code == 401
    assert client.post(f"{O}/meetings", json=_body(), headers=h).status_code == 401
