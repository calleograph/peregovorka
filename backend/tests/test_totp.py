from __future__ import annotations

import base64
import time

from app.services import totp

from .conftest import login

# RFC 6238, приложение B: секрет ASCII "12345678901234567890", SHA-1, восьмизначные коды; берём шесть последних цифр
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


def test_rfc6238_vectors():
    for at, full in ((59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"), (1234567890, "89005924"), (2000000000, "69279037")):
        assert totp.code_at(RFC_SECRET, at) == full[-6:]


def test_verify_window_replay_and_garbage():
    t = 1_700_000_000
    code = totp.code_at(RFC_SECRET, t)
    step = totp.verify(RFC_SECRET, code, None, t)
    assert step == t // 30
    assert totp.verify(RFC_SECRET, code, step, t) is None                          # тот же шаг повторно — отказ
    assert totp.verify(RFC_SECRET, code, None, t + 30) == step                    # сосед по времени допустим
    assert totp.verify(RFC_SECRET, code, None, t + 120) is None                    # дальше окна — нет
    for bad in ("", "12345", "abcdef", "1234567", code[:-1] + str((int(code[-1]) + 1) % 10)):
        assert totp.verify(RFC_SECRET, bad, None, t) is None
    assert totp.verify(RFC_SECRET, f"{code[:3]} {code[3:]}", None, t) == step      # пробел в середине (так показывают приложения)


def test_backup_codes_are_hashed_and_normalised():
    codes = totp.new_backup_codes()
    assert len(set(codes)) == 10 and all(len(c) == 10 for c in codes)
    assert totp.hash_backup("u1", codes[0]) == totp.hash_backup("u1", totp.format_backup(codes[0]).upper())
    assert totp.hash_backup("u1", codes[0]) != totp.hash_backup("u2", codes[0])
    assert codes[0] not in totp.hash_backup("u1", codes[0])
    assert totp.looks_like_backup(totp.format_backup(codes[0])) and not totp.looks_like_backup("123456")


def test_otpauth_uri_and_qr():
    uri = totp.otpauth_uri("Peregovorka", "ivanov", "ABCDEFGH")
    assert uri.startswith("otpauth://totp/Peregovorka%3Aivanov?secret=ABCDEFGH&issuer=Peregovorka")
    assert totp.qr_svg(uri).lstrip().startswith("<svg")


# ------------------------------------------------------------------------------------------------ сквозной сценарий
def _policy(client, **patch):
    login(client, "root")
    r = client.put("/api/v1/admin/settings/auth_security", json=patch)
    assert r.status_code == 200, r.text


def _enroll(client, user="alice", password=None):
    csrf = login(client, user, password)
    s = client.post("/api/v1/auth/totp/setup", json={"password": password or f"{user}-pass"})
    assert s.status_code == 200, s.text
    secret = s.json()["secret"].replace(" ", "")
    assert s.json()["qr_svg"].lstrip().startswith("<svg")
    e = client.post("/api/v1/auth/totp/enable", json={"code": totp.code_at(secret)})
    assert e.status_code == 200, e.text
    return csrf, secret, e.json()["backup_codes"]


def _login_step1(client, user="alice"):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    return client.post("/api/v1/auth/login", json={"login": user, "password": f"{user}-pass"})


def test_enrol_then_login_requires_code_and_rejects_replay(client):
    _, secret, codes = _enroll(client)
    assert len(codes) == 10
    r = _login_step1(client)
    assert r.status_code == 200 and r.json()["totp_required"] is True and "set-cookie" not in r.headers
    assert client.get("/api/v1/auth/me").status_code == 401                              # сессии нет
    ch = r.json()["challenge"]
    bad = client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": "000000"})
    assert bad.status_code == 401
    # код, использованный при включении, повторно не принимается (тот же шаг)
    replay = client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": totp.code_at(secret)})
    assert replay.status_code == 401
    # следующий шаг — принимается
    ok = client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": totp.code_at(secret, time.time() + 30)})
    assert ok.status_code == 200 and ok.json()["user"]["sam_account_name"] == "alice"
    assert client.get("/api/v1/auth/me").status_code == 200
    # использованная заявка не годится второй раз
    assert client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": totp.code_at(secret, time.time() - 30)}).status_code == 401


def test_backup_code_works_once(client):
    _, _secret, codes = _enroll(client)
    ch = _login_step1(client).json()["challenge"]
    assert client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": codes[0]}).status_code == 200
    ch2 = _login_step1(client).json()["challenge"]
    assert client.post("/api/v1/auth/login/totp", json={"challenge": ch2, "code": codes[0]}).status_code == 401
    assert client.post("/api/v1/auth/login/totp", json={"challenge": ch2, "code": codes[1]}).status_code == 200
    rows = [a["action"] for a in client.get("/api/v1/admin/audit").json()] if login(client, "root") else []
    assert {"auth.totp_enable", "auth.backup_code_used", "auth.totp_failed"} <= set(rows)


def test_challenge_dies_after_five_wrong_codes(client):
    _, secret, _ = _enroll(client)
    ch = _login_step1(client).json()["challenge"]
    for _ in range(5):
        assert client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": "111111"}).status_code == 401
    r = client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": totp.code_at(secret, time.time() + 30)})
    assert r.status_code in (401, 429)                                                  # верный код после лимита уже не спасает
    assert client.get("/api/v1/auth/me").status_code == 401


def test_policy_all_forces_enrollment_and_blocks_other_api(client):
    _policy(client, totp_policy="all")
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    r = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"})
    assert r.status_code == 200 and r.json()["must_enroll_totp"] is True
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.get("/api/v1/rooms").status_code == 403                               # прочие ручки закрыты до настройки
    assert client.get("/api/v1/auth/me").status_code == 200
    s = client.post("/api/v1/auth/totp/setup", json={"password": "alice-pass"}).json()
    assert client.post("/api/v1/auth/totp/enable", json={"code": totp.code_at(s["secret"].replace(" ", ""))}).status_code == 200
    assert client.get("/api/v1/rooms").status_code == 200                               # после настройки сессия полноценна
    # отключить нельзя, пока политика требует
    st = client.get("/api/v1/auth/totp/status").json()
    assert st["required"] is True and st["enabled"] is True
    assert client.post("/api/v1/auth/totp/disable", json={"password": "alice-pass", "code": "123456"}).status_code == 403


def test_policy_admins_only(client):
    _policy(client, totp_policy="admins")
    login(client, "root")
    assert client.get("/api/v1/auth/totp/status").json()["required"] is True
    assert client.get("/api/v1/admin/audit").status_code == 403                         # администратор без 2FA — только настройка
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    r = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"})
    assert r.json().get("must_enroll_totp") is False                                    # обычному пользователю не нужно


def test_policy_off_hides_feature(client):
    _policy(client, totp_policy="off")
    login(client, "alice")
    assert client.get("/api/v1/auth/totp/status").json()["available"] is False
    assert client.post("/api/v1/auth/totp/setup", json={"password": "alice-pass"}).status_code == 403


def test_setup_needs_password_and_disable_voluntarily(client):
    login(client, "alice")
    assert client.post("/api/v1/auth/totp/setup", json={"password": "wrong"}).status_code == 403
    assert client.post("/api/v1/auth/totp/enable", json={"code": "123456"}).status_code == 409      # без setup
    _, secret, _ = _enroll(client, "alice")
    # выключение — по паролю и коду (следующий шаг, чтобы не упереться в защиту от повтора)
    r = client.post("/api/v1/auth/totp/disable", json={"password": "alice-pass", "code": totp.code_at(secret, time.time() + 30)})
    assert r.status_code == 204
    assert _login_step1(client).json().get("totp_required") is None


def test_admin_reset_restores_access(client):
    _, secret, _ = _enroll(client)
    me = client.get("/api/v1/auth/me").json()["user"]["id"]
    login(client, "root")
    assert client.post(f"/api/v1/admin/users/{me}/totp/reset", json={"password": "wrong"}).status_code == 403
    assert client.post(f"/api/v1/admin/users/{me}/totp/reset", json={"password": "root-pass"}).status_code == 204
    r = _login_step1(client)                                                            # вход снова по одному паролю
    assert r.status_code == 200 and r.json().get("totp_required") is None
    login(client, "alice")
    assert client.post(f"/api/v1/admin/users/{me}/totp/reset", json={"password": "alice-pass"}).status_code == 403   # обычному пользователю нельзя
    login(client, "root")
    assert "auth.totp_reset" in [a["action"] for a in client.get("/api/v1/admin/audit").json()]


def test_regenerate_backup_codes_invalidates_old(client):
    _, secret, old = _enroll(client)
    r = client.post("/api/v1/auth/totp/backup-codes", json={"password": "alice-pass", "code": totp.code_at(secret, time.time() + 30)})
    assert r.status_code == 200 and r.json()["backup_codes"] != old
    ch = _login_step1(client).json()["challenge"]
    assert client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": old[0]}).status_code == 401
    assert client.post("/api/v1/auth/login/totp", json={"challenge": ch, "code": r.json()["backup_codes"][0]}).status_code == 200
