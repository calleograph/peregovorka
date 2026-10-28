"""Администрирование → CAPTCHA → «Проверить CAPTCHA»: тестовая проверка той же задачей, но отдельным назначением; защиту входа не затрагивает."""
from __future__ import annotations

import pytest

from app.security import imgcaptcha

from .conftest import login
from .test_login_protection import at, attempt, deterministic_captcha, instance, pc, server, setup_group, solved  # noqa: F401

CH = "/api/v1/admin/security/captcha-test/challenge"
VF = "/api/v1/admin/security/captcha-test/verify"


def admin(c):
    login(c, "root")
    return c


def test_only_admin_with_csrf_can_use_the_test(pc):
    assert pc.post(CH).status_code in (401, 403)                           # без входа
    login(pc, "alice")
    assert pc.post(CH).status_code == 403                                   # обычный пользователь
    pc.post("/api/v1/auth/logout"); pc.cookies.clear(); pc.headers.pop("X-CSRF-Token", None)
    admin(pc)
    csrf = pc.headers.pop("X-CSRF-Token")
    assert pc.post(CH).status_code == 403                                   # без CSRF-токена
    pc.headers["X-CSRF-Token"] = csrf
    assert pc.post(CH).status_code == 200


def test_challenge_shows_active_config_and_never_the_answer(pc, deterministic_captcha):
    setup_group(pc, "captcha", mode="after_failures", failures=7, length=5, ttl_seconds=120)
    admin(pc)
    r = pc.post(CH)
    body = r.json()
    ch, diag = body["challenge"], body["diag"]
    assert ch["kind"] == "image" and ch["image"].startswith("data:image/png;base64,")
    assert diag["failures"] == 7 and diag["ttl_seconds"] == 120 and diag["redis"] is True and diag["mode"] == "after_failures"
    rest = {k: v for k, v in ch.items() if k != "image"}                          # картинка — base64: «AAAAA» там случайно возможно
    assert deterministic_captcha not in str(rest) + str(diag) and "answer" not in str(rest).lower() + str(diag).lower()


def test_pass_wrong_and_single_use(pc, deterministic_captcha):
    admin(pc)
    ch = pc.post(CH).json()["challenge"]
    ok = pc.post(VF, json={"id": ch["id"], "answer": "aaaaa"}).json()           # регистр не важен
    assert ok["ok"] is True and ok["reason"] == "passed" and "пройдена" in ok["message"]
    again = pc.post(VF, json={"id": ch["id"], "answer": "AAAAA"}).json()
    assert again["ok"] is False and again["reason"] == "expired"
    ch2 = pc.post(CH).json()["challenge"]
    wrong = pc.post(VF, json={"id": ch2["id"], "answer": "ZZZZZ"}).json()
    assert wrong["reason"] == "wrong" and "Неверный" in wrong["message"]
    assert pc.post(VF, json={"id": ch2["id"], "answer": "AAAAA"}).json()["reason"] == "expired", "неверный ответ сжигает картинку"
    assert pc.post(VF, json={"id": "", "answer": ""}).json()["reason"] == "missing"


def test_test_task_does_not_open_login_and_failed_tests_do_not_count_as_failed_logins(pc, deterministic_captcha):
    admin(pc)
    tch = pc.post(CH).json()["challenge"]
    assert pc.put("/api/v1/admin/settings/captcha", json={"mode": "always"}).status_code == 200
    r = attempt(pc, pw="alice-pass", headers=solved(pc, tch))                  # задача «для теста» не принимается при входе
    assert r.status_code == 428 and r.json()["detail"]["reason"] == "context"
    assert pc.put("/api/v1/admin/settings/captcha", json={"mode": "after_failures", "failures": 3}).status_code == 200
    for _ in range(12):                                                         # много неверных тестов подряд
        c = pc.post(CH).json()["challenge"]
        pc.post(VF, json={"id": c["id"], "answer": "ZZZZZ"})
    pc.post("/api/v1/auth/logout"); pc.cookies.clear(); pc.headers.pop("X-CSRF-Token", None)
    assert attempt(pc, pw="alice-pass").status_code == 200, "неудачные тесты CAPTCHA не повлияли на счётчики входа"


def test_after_threshold_login_is_closed_without_captcha_even_after_cleared_cookies_and_direct_post(pc, deterministic_captcha):
    """Настоящий вход: порог → сервер требует CAPTCHA, пока её не решили; закрытие окна/очистка cookie/прямой POST/другой User-Agent это не снимают."""
    setup_group(pc, "captcha", mode="after_failures", failures=3)
    for _ in range(3):
        assert attempt(pc).status_code == 401
    for ua in ("Chrome incognito", "curl/8.0", ""):
        pc.cookies.clear()
        r = attempt(pc, pw="alice-pass", headers={"User-Agent": ua})
        assert r.status_code == 428 and r.json()["detail"]["code"] == "captcha_required"
    ch = r.json()["detail"]["challenge"]
    assert attempt(pc, pw="alice-pass", headers=solved(pc, ch)).status_code == 200


def test_test_rate_limit(pc, deterministic_captcha):
    admin(pc)
    codes = [pc.post(CH).status_code for _ in range(32)]
    assert codes[:30] == [200] * 30 and codes[30:] == [429, 429]


def test_verify_writes_audit_and_service_error_is_explicit(pc, deterministic_captcha, monkeypatch):
    admin(pc)
    ch = pc.post(CH).json()["challenge"]
    assert pc.post(VF, json={"id": ch["id"], "answer": "AAAAA"}).json()["ok"] is True

    async def _rows():
        from sqlalchemy import select
        from app.models import AuditLog
        async with pc.app_obj.state.session_maker() as db:
            return (await db.execute(select(AuditLog).where(AuditLog.action == "captcha.test"))).scalars().all()
    rows = pc.portal.call(_rows)
    assert len(rows) == 1 and "AAAAA" not in str(rows[0].details) and rows[0].details["reason"] == "passed"

    async def boom(*a, **k):
        raise OSError("redis down")
    monkeypatch.setattr(imgcaptcha, "verify", boom)
    r = pc.post(VF, json={"id": "x", "answer": "y"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "captcha_unavailable"
    monkeypatch.setattr(imgcaptcha, "render", lambda *a, **k: (_ for _ in ()).throw(OSError("нет шрифта")))
    r = pc.post(CH)
    assert r.status_code == 503 and "OSError" in r.json()["detail"]["message"] and r.json()["detail"]["diag"]["kind"] == "image"


def test_pow_kind_is_tested_too(pc):
    import base64, hashlib, json
    setup_group(pc, "captcha", kind="pow", difficulty=1000)
    admin(pc)
    ch = pc.post(CH).json()["challenge"]
    assert ch["kind"] == "pow"
    n = next(i for i in range(ch["maxnumber"] + 1) if hashlib.sha256(f"{ch['salt']}{i}".encode()).hexdigest() == ch["challenge"])
    sol = base64.b64encode(json.dumps({"algorithm": ch["algorithm"], "challenge": ch["challenge"], "number": n, "salt": ch["salt"], "signature": ch["signature"]}).encode()).decode()
    assert pc.post(VF, json={"solution": sol}).json()["ok"] is True
    assert pc.post(VF, json={"solution": sol}).json()["reason"] == "replay"
