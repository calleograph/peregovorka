"""Приложение Windows: сведения о сервере для мастера подключения и «доверенное устройство» — вход без пароля после перезапуска, но с теми же правилами, что при входе:
токен одноразовый, отзывается выходом, завершением сеанса, сменой и отключением учётной записи, сменой пароля в каталоге; из браузера его не получить."""
from __future__ import annotations

import dataclasses

from app.auth.directory import DirectoryError

from .test_sessions import API, COOKIE, as_device, me_status, sign_in

DESK = {"X-Peregovorka-Desktop": "1", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36 PeregovorkaDesktop/1.0.0"}


def trusted(c, user="alice"):
    """Вход паролем «в приложении» и выдача доверия: (cookie, токен)."""
    r, cookie = sign_in(c, user, ua=DESK["User-Agent"])
    assert r.status_code == 200, r.text
    t = c.post(f"{API}/desktop/trust", headers={**DESK, "X-CSRF-Token": r.json()["csrf_token"]})
    assert t.status_code == 200, t.text
    assert t.headers["cache-control"] == "no-store"
    return cookie, t.json()["token"]


def resume(c, token):
    c.cookies.clear()
    return c.post(f"{API}/desktop/resume", json={"token": token}, headers=DESK)


def test_info_is_public_and_describes_compatibility(client):
    client.cookies.clear()
    d = client.get(f"{API}/desktop/info").json()
    assert d["product"] == "peregovorka" and d["api"] == 1 and d["min_client"] and d["cookie"] == COOKIE and d["trust_days"] == 30 and "trust" in d["features"]


def test_resume_opens_a_new_session_rotates_the_token_and_replaces_the_old_session(client):
    old_cookie, token = trusted(client)
    r = resume(client, token)
    assert r.status_code == 200, r.text
    new_cookie, nxt = client.cookies.get(COOKIE), r.json()["token"]
    assert new_cookie and new_cookie != old_cookie and nxt and nxt != token
    assert me_status(client, new_cookie) == 200
    assert me_status(client, old_cookie) == 401, "прежняя сессия этого устройства завершена — место в лимите сеансов не растёт"
    assert resume(client, token).status_code == 401, "токен одноразовый"
    assert resume(client, nxt).status_code == 200, "следующий запуск — по новому токену"


def test_device_shows_up_as_the_windows_app_in_active_devices(client):
    cookie, _ = trusted(client)
    as_device(client, cookie)
    items = client.get(f"{API}/auth/sessions").json()["items"]
    assert items[0]["device"].startswith("Peregovorka для Windows 1.0.0 · Windows 10/11")


def test_browser_requests_cannot_get_or_use_a_token(client):
    r, _ = sign_in(client)
    csrf = r.json()["csrf_token"]
    assert client.post(f"{API}/desktop/trust", headers={"X-CSRF-Token": csrf}).status_code == 403, "без признака приложения"
    for extra in ({"Origin": "http://testserver"}, {"Sec-Fetch-Mode": "cors"}):
        assert client.post(f"{API}/desktop/trust", headers={**DESK, **extra, "X-CSRF-Token": csrf}).status_code == 403, f"запрос страницы ({extra}) — даже с XSS токен не выдаётся"
    _, token = trusted(client)
    client.cookies.clear()
    assert client.post(f"{API}/desktop/resume", json={"token": token}, headers={**DESK, "Origin": "http://testserver"}).status_code == 403


def test_logout_and_ending_the_session_revoke_the_device(client):
    cookie, token = trusted(client)
    as_device(client, cookie)
    csrf = client.get(f"{API}/auth/me").json()["csrf_token"]
    assert client.post(f"{API}/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert resume(client, token).status_code == 401, "выход в приложении отзывает доверие устройства"

    cookie, token = trusted(client)
    other, _ = sign_in(client)[1], None
    as_device(client, other)
    me = client.get(f"{API}/auth/me").json()
    pub = next(x["id"] for x in client.get(f"{API}/auth/sessions").json()["items"] if not x["current"] and "Peregovorka" in x["device"])
    assert client.delete(f"{API}/auth/sessions/{pub}", headers={"X-CSRF-Token": me["csrf_token"]}).status_code == 204
    assert resume(client, token).status_code == 401, "завершение сеанса в «Активных устройствах» отзывает и доверие"


def test_end_other_sessions_and_disabling_the_account_revoke_trust(client):
    _, token = trusted(client)
    r, keep = sign_in(client)
    assert client.post(f"{API}/auth/sessions/end-others", headers={"X-CSRF-Token": r.json()["csrf_token"]}).json()["ended"] >= 1
    assert resume(client, token).status_code == 401

    _, token = trusted(client)
    r, adm = sign_in(client, "root")
    csrf = r.json()["csrf_token"]
    uid = next(u["id"] for u in client.get(f"{API}/admin/users").json() if u["sam_account_name"] == "alice")
    assert client.patch(f"{API}/admin/users/{uid}", json={"is_active": False}, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert resume(client, token).status_code == 401, "отключённая учётная запись не возобновляется"


def test_directory_checks_on_resume(client, directory):
    pw, ident = directory.users["alice"]
    directory.users["alice"] = (pw, dataclasses.replace(ident, pwd_set="2026-10-01 10:00:00+00:00"))
    _, token = trusted(client)

    real = directory.lookup
    directory.lookup = lambda login: (_ for _ in ()).throw(DirectoryError("server_unavailable"))
    r = resume(client, token)
    assert r.status_code == 503 and r.headers.get("retry-after"), "каталог недоступен — временная ошибка"
    directory.lookup = real
    r = resume(client, token)
    assert r.status_code == 200, "после временной ошибки тот же токен действует (клиент повторит запуск)"
    token = r.json()["token"]

    directory.users["alice"] = (pw, dataclasses.replace(ident, pwd_set="2026-10-20 08:00:00+00:00"))
    assert resume(client, token).status_code == 401, "пароль сменили в каталоге — сохранённый вход больше не действует"

    directory.users["alice"] = (pw, dataclasses.replace(ident, pwd_set=None))
    _, token = trusted(client)
    directory.users["alice"] = (pw, dataclasses.replace(ident, disabled=True))
    assert resume(client, token).status_code == 401, "учётная запись отключена в каталоге"
    directory.users["alice"] = (pw, ident)


def test_admin_can_turn_trust_off(client):
    r, _ = sign_in(client, "root")
    assert client.put(f"{API}/admin/settings/auth_security", json={"desktop_trust_days": 0}, headers={"X-CSRF-Token": r.json()["csrf_token"]}).status_code == 200
    r, _ = sign_in(client)
    assert client.post(f"{API}/desktop/trust", headers={**DESK, "X-CSRF-Token": r.json()["csrf_token"]}).json()["detail"]["code"] == "trust_disabled"
    client.cookies.clear()
    assert client.get(f"{API}/desktop/info").json()["trust_days"] == 0
    assert client.put(f"{API}/admin/settings/auth_security", json={"desktop_trust_days": 91}).status_code in (401, 403, 422)


def test_garbage_tokens_are_rejected(client):
    for t in ("", "x", "pgd_" + "a" * 200, None, 5):
        assert resume(client, t).status_code == 401
    assert client.post(f"{API}/desktop/forget", json={"token": "pgd_nothing"}, headers=DESK).status_code == 204
