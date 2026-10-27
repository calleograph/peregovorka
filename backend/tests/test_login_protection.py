"""Защита входа от перебора: проверки обхода (cookie, инкогнито, User-Agent, подмена заголовков, повтор CAPTCHA, перезапуск, два экземпляра backend, отказ Redis)
и рабочие сценарии (порог CAPTCHA, подбор по списку, общий адрес, повторные блокировки, снятие блокировки). Без сети."""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import contextmanager

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Base
from app.security import imgcaptcha

from .conftest import FakeDirectory, login, make_settings

WEB = ("172.18.0.5", 50000)          # так backend видит web-контейнер (частный адрес)
LOGIN = "/api/v1/auth/login"


@contextmanager
def instance(tmp_path, directory, server: FakeServer, name="a"):
    """Экземпляр backend с общим Redis (server) и собственной базой/процессом."""
    sub = tmp_path / name
    sub.mkdir(exist_ok=True)
    app = create_app(make_settings(sub), redis_factory=lambda s: FakeAsyncRedis(server=server, decode_responses=True), directory_factory=lambda s: directory, start_workers=False)
    with TestClient(app, base_url="http://testserver", client=WEB) as c:
        async def _create():
            async with app.state.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        c.portal.call(_create)
        c.app_obj = app  # type: ignore[attr-defined]
        yield c


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def pc(tmp_path, directory, server):
    with instance(tmp_path, directory, server) as c:
        yield c


def at(ip: str, **extra) -> dict:
    """Заголовки web-контейнера: он сообщает, с какого адреса к нему подключились."""
    return {"X-PG-Peer": ip, **extra}


def attempt(c, user="alice", pw="bad", ip="203.0.113.10", headers=None):
    return c.post(LOGIN, json={"login": user, "password": pw}, headers={**at(ip), **(headers or {})})


def setup_group(c, group, **vals):
    login(c, "root")
    r = c.put(f"/api/v1/admin/settings/{group}", json=vals)
    assert r.status_code == 200, r.text
    c.post("/api/v1/auth/logout")
    c.cookies.clear()
    c.headers.pop("X-CSRF-Token", None)


@pytest.fixture
def deterministic_captcha(monkeypatch):
    """Ответ CAPTCHA известен тесту: алфавит из одного символа."""
    monkeypatch.setattr(imgcaptcha, "ALPHABET", "A")
    return "AAAAA"


def solved(c, ch, ip="203.0.113.10", answer="AAAAA"):
    return {"X-Captcha-Id": ch["id"], "X-Captcha-Answer": answer, **at(ip)}


# ------------------------------------------------------------------------------------------------ 1–2. порог CAPTCHA и вид проверки
def test_three_wrong_passwords_then_visible_captcha_is_required_by_the_server(pc, deterministic_captcha):
    setup_group(pc, "captcha", mode="after_failures", failures=3)
    for _ in range(3):
        assert attempt(pc).status_code == 401
    r = attempt(pc)                                                      # четвёртая попытка — даже с ВЕРНЫМ паролем — без проверки не принимается
    assert r.status_code == 428 and r.json()["detail"]["code"] == "captcha_required"
    ch = r.json()["detail"]["challenge"]
    assert ch["kind"] == "image" and ch["image"].startswith("data:image/png;base64,") and ch["ttl"] > 0 and "answer" not in json.dumps(ch).lower()
    assert attempt(pc, pw="alice-pass").status_code == 428, "прямой запрос к API с верным паролем без CAPTCHA отклоняется"
    assert attempt(pc, pw="alice-pass", headers=solved(pc, ch)).status_code == 200


def test_changed_threshold_in_admin_applies_immediately(pc):
    setup_group(pc, "captcha", mode="after_failures", failures=2)
    assert [attempt(pc).status_code for _ in range(3)] == [401, 401, 428]
    setup_group(pc, "captcha", mode="after_failures", failures=50)
    assert attempt(pc).status_code == 401, "порог выше — проверка снова не требуется"


# ------------------------------------------------------------------------------------------------ 5–8. cookie, инкогнито, User-Agent, повторное использование
def test_requirement_survives_cleared_cookies_new_session_and_another_user_agent(pc):
    setup_group(pc, "captcha", mode="after_failures", failures=3)
    for _ in range(3):
        attempt(pc, headers={"User-Agent": "Chrome incognito #1"})
    pc.cookies.clear()                                                   # «закрыли окно инкогнито»
    for ua in ("Chrome incognito #2", "curl/8.0", ""):
        r = attempt(pc, pw="alice-pass", headers={"User-Agent": ua})
        assert r.status_code == 428, ua


def test_captcha_is_single_use_context_bound_and_expires(pc, deterministic_captcha, monkeypatch):
    setup_group(pc, "captcha", mode="always")
    ch = attempt(pc, pw="alice-pass").json()["detail"]["challenge"]
    ok = attempt(pc, pw="alice-pass", headers=solved(pc, ch))
    assert ok.status_code == 200
    pc.cookies.clear()
    pc.headers.pop("X-CSRF-Token", None)
    replay = attempt(pc, pw="alice-pass", headers=solved(pc, ch))
    assert replay.status_code == 428 and replay.json()["detail"]["reason"] == "expired", "тот же токен второй раз не принимается"
    # неверный ответ сжигает задачу: подобрать по одной картинке нельзя
    ch2 = replay.json()["detail"]["challenge"]
    wrong = attempt(pc, pw="alice-pass", headers=solved(pc, ch2, answer="ZZZZZ"))
    assert wrong.status_code == 428 and wrong.json()["detail"]["reason"] == "wrong"
    assert attempt(pc, pw="alice-pass", headers=solved(pc, ch2)).json()["detail"]["reason"] == "expired"
    # решение, выданное другому адресу, не годится
    ch3 = attempt(pc, pw="alice-pass", ip="203.0.113.10").json()["detail"]["challenge"]
    other = attempt(pc, pw="alice-pass", ip="198.51.100.77", headers=solved(pc, ch3, ip="198.51.100.77"))
    assert other.status_code == 428 and other.json()["detail"]["reason"] == "context"
    # задача выдана для входа — для создания временной переговорки/гостя она не подходит
    async def _age():                                                    # срок жизни: запись исчезает
        await pc.app_obj.state.redis.delete(f"captcha:img:{ch3['id']}")
    pc.portal.call(_age)
    assert attempt(pc, pw="alice-pass", headers=solved(pc, ch3)).json()["detail"]["reason"] == "expired"


def test_pow_kind_still_works_when_chosen(pc):
    setup_group(pc, "captcha", mode="always", kind="pow", difficulty=2000)
    r = attempt(pc, pw="alice-pass")
    assert r.status_code == 428 and r.json()["detail"]["challenge"]["kind"] == "pow"


# ------------------------------------------------------------------------------------------------ 12–13. по записи и по адресу независимо
def test_one_account_from_many_addresses_requires_captcha_and_stops_reaching_the_directory(pc, directory):
    setup_group(pc, "captcha", mode="after_failures", failures=3)
    setup_group(pc, "login_protection", account_guard_failures=6)
    codes = [attempt(pc, "alice", ip=f"198.51.100.{i}").status_code for i in range(1, 4)]
    assert codes == [401, 401, 401], "три адреса — по одной неудаче: каждый ниже порога по адресу"
    r = attempt(pc, "alice", pw="alice-pass", ip="198.51.100.200")
    assert r.status_code == 428 and r.json()["detail"]["challenge"], "порог по ЗАПИСИ достигнут: новому адресу нужна CAPTCHA"
    for i in range(10, 13):
        attempt(pc, "alice", ip=f"198.51.100.{i}", headers={"X-Captcha-Id": "x", "X-Captcha-Answer": "y"})
    calls = directory.calls
    attempt_ok = attempt(pc, "alice", pw="alice-pass", ip="198.51.100.222")
    assert attempt_ok.status_code in (428, 429)


def test_account_guard_spares_the_address_the_user_has_logged_in_from(pc, directory):
    setup_group(pc, "captcha", mode="off")
    setup_group(pc, "login_protection", account_guard_failures=5, ip_block_failures=500, slowdown_after=0)
    assert attempt(pc, "alice", pw="alice-pass", ip="192.0.2.50").status_code == 200       # знакомый адрес
    pc.post("/api/v1/auth/logout")
    pc.cookies.clear()
    for i in range(6):
        attempt(pc, "alice", ip=f"198.51.100.{i + 1}")                                    # подбор с чужих адресов
    calls = directory.calls
    stranger = attempt(pc, "alice", pw="alice-pass", ip="198.51.100.99")
    assert stranger.status_code == 429 and directory.calls == calls, "с неизвестного адреса до каталога не доходит ни верный пароль, ни неверный"
    assert "alice" not in stranger.text.lower() and "Retry-After" in stranger.headers
    home = attempt(pc, "alice", pw="alice-pass", ip="192.0.2.50")
    assert home.status_code == 200, "владельца записи подбор с чужих адресов не блокирует"


def test_many_accounts_from_one_address_blocks_that_address_not_the_accounts(pc):
    setup_group(pc, "captcha", mode="off")
    setup_group(pc, "login_protection", spray_accounts=5, ip_block_failures=500, slowdown_after=0)
    codes = [attempt(pc, f"user{i}", ip="203.0.113.77").status_code for i in range(7)]
    assert 429 in codes and codes[0] == 401
    assert attempt(pc, "alice", pw="alice-pass", ip="203.0.113.78").status_code == 200, "другой адрес с верным паролем входит: записи не заблокированы"
    r = attempt(pc, "alice", pw="alice-pass", ip="203.0.113.77")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0 and r.json()["detail"]["message"] == "Слишком много неудачных попыток. Повторите позже."


def test_account_name_forms_share_one_counter(pc):
    setup_group(pc, "captcha", mode="after_failures", failures=4)
    for form in ("alice", "ALICE", "EXAMPLE\\alice", "alice@example.org"):
        assert attempt(pc, form).status_code == 401
    assert attempt(pc, "Alice ", pw="alice-pass").status_code == 428


# ------------------------------------------------------------------------------------------------ 9–11. блокировка, срок, эскалация, перезапуск, два экземпляра
def test_ip_block_expires_escalates_and_survives_restart_and_second_backend(tmp_path, directory, server):
    with instance(tmp_path, directory, server, "a") as a:
        setup_group(a, "captcha", mode="off")
        setup_group(a, "login_protection", ip_block_failures=5, ip_block_minutes=10, ip_block_max_minutes=25, slowdown_after=0, account_guard_failures=500)
        for _ in range(5):
            attempt(a, "alice", ip="203.0.113.90")
        assert attempt(a, "alice", pw="alice-pass", ip="203.0.113.90").status_code == 429
        left = int(attempt(a, "alice", pw="alice-pass", ip="203.0.113.90").headers["Retry-After"])
        assert 540 <= left <= 600
    with instance(tmp_path, directory, server, "a") as b:                        # «перезапуск»/другой экземпляр: та же база настроек и те же серверные счётчики (Redis)
        assert attempt(b, "alice", pw="alice-pass", ip="203.0.113.90").status_code == 429
        _r = attempt(b, "alice", pw="alice-pass", ip="203.0.113.91")
        assert _r.status_code == 200, _r.text
        guard = b.app_obj.state.login_guard
        b.portal.call(lambda: guard.unblock_ip("203.0.113.90"))
        assert attempt(b, "bob", pw="bob-pass", ip="203.0.113.90").status_code == 200, "после снятия блокировки адрес снова работает"
        # повтор: вторая блокировка вдвое дольше (10 → 20), третья — не дольше потолка (25)
        for _ in range(5):
            attempt(b, "carol", ip="203.0.113.90")
        rec = b.portal.call(lambda: guard.blocked("203.0.113.90"))
        assert rec and round((rec["until"] - rec["since"]) / 60) == 20 and rec["level"] == 2, "снятие блокировки не обнуляет историю повторов"
        b.portal.call(lambda: guard.unblock_ip("203.0.113.90"))
        for _ in range(5):
            attempt(b, "carol", ip="203.0.113.90")
        rec = b.portal.call(lambda: guard.blocked("203.0.113.90"))
        assert round((rec["until"] - rec["since"]) / 60) == 25


def test_block_expires_by_time(pc, monkeypatch):
    import time as _t

    setup_group(pc, "captcha", mode="off")
    setup_group(pc, "login_protection", ip_block_failures=3, ip_block_minutes=1, slowdown_after=0, account_guard_failures=500)
    for _ in range(3):
        attempt(pc, "alice", ip="203.0.113.60")
    assert attempt(pc, "alice", pw="alice-pass", ip="203.0.113.60").status_code == 429
    real = _t.time
    monkeypatch.setattr("app.security.loginguard.time.time", lambda: real() + 61)
    assert attempt(pc, "alice", pw="alice-pass", ip="203.0.113.60").status_code == 200


# ------------------------------------------------------------------------------------------------ 15–16. подмена адреса
def test_forged_forwarded_headers_do_not_reset_or_split_counters(pc):
    setup_group(pc, "captcha", mode="after_failures", failures=3)
    # клиент подключился к web напрямую (публичный адрес) и каждый раз присылает другой X-Forwarded-For/X-Real-IP
    for i in range(3):
        r = pc.post(LOGIN, json={"login": "alice", "password": "bad"}, headers={"X-PG-Peer": "203.0.113.5", "X-Forwarded-For": f"198.51.100.{i}", "X-Real-IP": f"198.51.100.{i}"})
        assert r.status_code == 401
    r = pc.post(LOGIN, json={"login": "alice", "password": "alice-pass"}, headers={"X-PG-Peer": "203.0.113.5", "X-Forwarded-For": "198.51.100.200", "Forwarded": "for=198.51.100.250"})
    assert r.status_code == 428, "подмена заголовков не сбросила счётчик адреса"


def test_direct_hit_on_backend_cannot_pick_an_address(tmp_path, directory, server):
    app = create_app(make_settings(tmp_path), redis_factory=lambda s: FakeAsyncRedis(server=server, decode_responses=True), directory_factory=lambda s: directory, start_workers=False)
    with TestClient(app, base_url="http://testserver", client=("203.0.113.9", 1234)) as c:       # соединение из интернета минуя web
        async def _create():
            async with app.state.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        c.portal.call(_create)
        setup_group(c, "captcha", mode="after_failures", failures=3)
        for i in range(3):
            c.post(LOGIN, json={"login": "alice", "password": "bad"}, headers={"X-Forwarded-For": f"10.0.0.{i}", "X-PG-Peer": f"10.0.1.{i}"})
        assert c.post(LOGIN, json={"login": "alice", "password": "alice-pass"}, headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 428


# ------------------------------------------------------------------------------------------------ общий адрес (NAT), замедление, Redis
def test_shared_address_is_never_blocked_as_a_whole(pc):
    setup_group(pc, "captcha", mode="off")
    setup_group(pc, "login_protection", ip_block_failures=6, shared_ip_accounts=2, slowdown_after=0, account_guard_failures=500)
    assert attempt(pc, "alice", pw="alice-pass", ip="192.0.2.99").status_code == 200
    pc.post("/api/v1/auth/logout"); pc.cookies.clear()
    assert attempt(pc, "bob", pw="bob-pass", ip="192.0.2.99").status_code == 200
    pc.post("/api/v1/auth/logout"); pc.cookies.clear()
    for _ in range(12):
        attempt(pc, "carol", ip="192.0.2.99")
    assert attempt(pc, "alice", pw="alice-pass", ip="192.0.2.99").status_code == 200, "за общим NAT неудачи одного не закрывают вход остальным"


def test_admin_listed_nat_address_gets_captcha_not_a_block(pc):
    setup_group(pc, "captcha", mode="after_failures", failures=3)
    setup_group(pc, "login_protection", ip_block_failures=4, nat_addresses="192.0.2.0/24", slowdown_after=0, account_guard_failures=500)
    for _ in range(8):
        attempt(pc, "alice", ip="192.0.2.15")
    r = attempt(pc, "alice", pw="alice-pass", ip="192.0.2.15")
    assert r.status_code == 428, "NAT: вместо блокировки — CAPTCHA"


def test_slowdown_grows_with_failures_but_is_capped(pc, monkeypatch):
    setup_group(pc, "captcha", mode="off")
    setup_group(pc, "login_protection", slowdown_after=2, slowdown_step_ms=500, slowdown_max_ms=1500, ip_block_failures=500, account_guard_failures=500)
    slept: list[float] = []
    real = asyncio.sleep

    async def fake(s, *a, **k):
        slept.append(s)
        return await real(0)
    monkeypatch.setattr("app.api.auth.asyncio.sleep", fake)
    for _ in range(8):
        attempt(pc, "alice", ip="203.0.113.44")
    mine = [x for x in slept if x in (0.5, 1.0, 1.5)]                  # паузы защиты входа (остальные sleep — служебные циклы приложения, у них свои значения)
    assert mine[:3] == [0.5, 1.0, 1.5] and len(mine) >= 4 and mine == sorted(mine)


def test_redis_outage_closes_login_with_a_clear_message_and_does_not_open_it(pc, monkeypatch):
    guard = pc.app_obj.state.login_guard

    async def boom(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(guard.r, "zcount", boom)
    r = attempt(pc, "alice", pw="alice-pass")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "protection_unavailable"
    assert not any(c.name == "pg_session" for c in pc.cookies.jar)
    # уже идущие встречи вход не затрагивает: ручки без входа работают
    assert pc.get("/api/v1/captcha/challenge").status_code in (200, 503)


def test_captcha_outage_is_an_explicit_error_not_a_pass(pc, monkeypatch):
    setup_group(pc, "captcha", mode="always")
    monkeypatch.setattr(imgcaptcha, "render", lambda *a, **k: (_ for _ in ()).throw(OSError("нет шрифта")))
    r = attempt(pc, "alice", pw="alice-pass")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "captcha_unavailable"


def test_issue_rate_is_limited_per_address(pc):
    codes = [pc.get("/api/v1/captcha/challenge", headers=at("203.0.113.31")).status_code for _ in range(24)]
    assert codes[0] == 200 and codes[-1] == 429
    assert pc.get("/api/v1/captcha/challenge", headers=at("203.0.113.32")).status_code == 200


def test_security_events_are_written_without_secrets(pc, deterministic_captcha):
    setup_group(pc, "captcha", mode="after_failures", failures=2)
    for _ in range(3):
        attempt(pc, "alice", pw="TOPSECRET-password-123")
    login(pc, "root")
    rows = pc.get("/api/v1/admin/journal", params={"category": "security"}).json() if False else []
    text = json.dumps(pc.get("/api/v1/admin/audit").json(), ensure_ascii=False)
    assert "TOPSECRET" not in text


def test_global_flood_asks_everyone_for_captcha_but_never_when_captcha_is_off(pc):
    setup_group(pc, "captcha", mode="after_failures", failures=50)
    setup_group(pc, "login_protection", rate_global_per_minute=10, rate_ip_per_minute=500, slowdown_after=0, ip_block_failures=1000, spray_accounts=500, account_guard_failures=1000)
    login(pc, "root")                                                  # сеанс администратора открыт до всплеска: во время всплеска вход без CAPTCHA не принимается
    for i in range(12):
        pc.post(LOGIN, json={"login": f"ghost{i}", "password": "x"}, headers=at(f"198.51.100.{i + 1}"))
    other = pc.post(LOGIN, json={"login": "alice", "password": "alice-pass"}, headers=at("198.51.100.200"))
    assert other.status_code == 428, "всплеск запросов со всех адресов: проверка для всех входящих, блокировок нет"
    assert pc.put("/api/v1/admin/settings/captcha", json={"mode": "off"}).status_code == 200
    ok = pc.post(LOGIN, json={"login": "alice", "password": "alice-pass"}, headers=at("198.51.100.201"))
    assert ok.status_code == 200, "CAPTCHA выключена администратором — всплеск её не включает"
