from __future__ import annotations

import base64
import hashlib
import json
import time

from app.services import captcha

from .conftest import login


def solve(ch: dict, *, tamper: str = "") -> str:
    """Решение так же, как в браузере: перебор числа до совпадения SHA-256(salt + n)."""
    n = next(i for i in range(ch["maxnumber"] + 1) if hashlib.sha256(f"{ch['salt']}{i}".encode()).hexdigest() == ch["challenge"])
    p = {"algorithm": ch["algorithm"], "challenge": ch["challenge"], "number": n, "salt": ch["salt"], "signature": ch["signature"]}
    if tamper == "number":
        p["number"] = n + 1
    if tamper == "signature":
        p["signature"] = "0" * 64
    return base64.b64encode(json.dumps(p).encode()).decode()


def _mode(client, mode="always", **extra):
    login(client, "root")
    r = client.put("/api/v1/admin/settings/captcha", json={"mode": mode, "difficulty": 2000, **extra})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


def _login(client, headers=None, pw="alice-pass"):
    return client.post("/api/v1/auth/login", json={"login": "alice", "password": pw}, headers=headers or {})


def test_challenge_roundtrip_unit():
    captcha.set_captcha_key(base64.b64encode(b"k" * 32).decode())
    ch = captcha.make_challenge(500, 60)
    err, sig, left = captcha.check_payload(solve(ch))
    assert err is None and sig == ch["signature"] and 0 < left <= 60
    assert captcha.check_payload(solve(ch, tamper="number"))[0] == "bad_solution"
    assert captcha.check_payload(solve(ch, tamper="signature"))[0] == "bad_signature"
    assert captcha.check_payload("не base64")[0] == "malformed"
    old = captcha.make_challenge(500, 60, now=time.time() - 120)
    assert captcha.check_payload(solve(old))[0] == "expired"


def test_default_mode_after_failures_not_required_at_first(client):
    r = _login(client)
    assert r.status_code == 200                                         # по умолчанию проверка включается только после серии неудач


def test_always_requires_solution_and_rejects_replay(client):
    _mode(client, "always")
    r = _login(client)
    assert r.status_code == 428 and r.json()["detail"]["code"] == "captcha_required"
    ch = r.json()["detail"]["challenge"]
    assert ch["maxnumber"] == 2000
    payload = solve(ch)
    assert _login(client, {"X-Captcha": solve(ch, tamper="number")}).status_code == 428
    ok = _login(client, {"X-Captcha": payload})
    assert ok.status_code == 200
    client.cookies.clear()
    again = _login(client, {"X-Captcha": payload})                       # то же решение второй раз
    assert again.status_code == 428 and again.json()["detail"]["reason"] == "replay"
    # задача из публичной ручки тоже годится
    ch2 = client.get("/api/v1/captcha/challenge").json()
    assert _login(client, {"X-Captcha": solve(ch2)}).status_code == 200


def test_after_failures_threshold_then_cleared_by_success(client):
    _mode(client, "after_failures", failures=3)
    for _ in range(3):
        assert _login(client, pw="wrong").status_code == 401
    r = _login(client, pw="wrong")
    assert r.status_code == 428                                          # порог достигнут: теперь нужна проверка
    ok = _login(client, {"X-Captcha": solve(r.json()["detail"]["challenge"])})
    assert ok.status_code == 200
    client.cookies.clear()
    assert _login(client).status_code == 200                             # успешный вход сбросил счётчик


def test_rejected_solution_is_audited(client):
    _mode(client, "always")
    ch = _login(client).json()["detail"]["challenge"]
    assert _login(client, {"X-Captcha": solve(ch, tamper="signature")}).status_code == 428
    ok = client.post("/api/v1/auth/login", json={"login": "root", "password": "root-pass"}, headers={"X-Captcha": solve(client.get("/api/v1/captcha/challenge").json())})
    assert ok.status_code == 200
    client.headers["X-CSRF-Token"] = ok.json()["csrf_token"]
    rows = client.get("/api/v1/admin/audit", params={"action": "captcha"}).json()
    assert rows and rows[0]["action"] == "captcha.rejected" and rows[0]["result"] == "denied" and rows[0]["details"]["reason"] == "bad_signature"


def test_temporary_room_needs_captcha_in_always_mode(client):
    _mode(client, "always")
    ch = client.get("/api/v1/captcha/challenge").json()
    r = client.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"}, headers={"X-Captcha": solve(ch)})
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    no = client.post("/api/v1/rooms/temporary", json={"name": "Быстрая"})
    assert no.status_code == 428
    yes = client.post("/api/v1/rooms/temporary", json={"name": "Быстрая"}, headers={"X-Captcha": solve(no.json()["detail"]["challenge"])})
    assert yes.status_code == 201, yes.text


def test_off_mode_never_asks(client):
    _mode(client, "off")
    codes = {_login(client, pw="wrong").status_code for _ in range(25)}
    assert 428 not in codes and 401 in codes                              # 429 — штатное ограничение попыток, но не CAPTCHA


def test_challenge_endpoint_is_rate_limited(client):
    codes = [client.get("/api/v1/captcha/challenge").status_code for _ in range(63)]
    assert codes[0] == 200 and codes[-1] == 429


def test_guest_join_needs_captcha_in_always_mode(client):
    from .conftest import make_room

    room = make_room(client, guest_access_enabled=True)
    _mode(client, "always")
    url = f"/api/v1/guest/room/{room['guest_token']}/join"
    no = client.post(url, json={"display_name": "Иван Иванов"})
    assert no.status_code == 428 and no.json()["detail"]["code"] == "captcha_required"
    yes = client.post(url, json={"display_name": "Иван Иванов"}, headers={"X-Captcha": solve(no.json()["detail"]["challenge"])})
    assert yes.status_code != 428
