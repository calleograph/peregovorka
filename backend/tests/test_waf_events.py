"""Журнал событий безопасности, заблокированные адреса и лёгкий WAF: режимы «наблюдение» и «защита», исключения, отсутствие ложных блокировок обычных запросов,
блокировка только анонимных запросов, ручная блокировка с ограничениями, выгрузка без секретов. Без сети."""
from __future__ import annotations

import json

import pytest

from .test_ip_access import admin, instance, server  # noqa: F401
from .test_login_protection import at, setup_group

SEC = "/api/v1/admin/security"


@pytest.fixture
def c(tmp_path, directory, server):
    with instance(tmp_path, directory, server) as cl:
        yield cl


def flush(c):
    c.portal.call(lambda: c.app_obj.state.secevents.flush())


def refresh_policies(c):
    s = c.app_obj.state
    c.portal.call(lambda: s.policies.refresh(s.settings_svc, s.session_maker, s.redis))


def set_waf(c, **vals):
    admin(c)
    r = c.put(f"{SEC}/waf", json=vals)
    assert r.status_code == 200, r.text
    refresh_policies(c)
    c.cookies.clear()
    c.headers.pop("X-CSRF-Token", None)


def anon(c, path, ip="203.0.113.60", **kw):
    c.cookies.clear()
    return c.get(path, headers={"X-PG-Peer": ip, "User-Agent": "Mozilla/5.0 Chrome/130", **kw.pop("headers", {})}, **kw)


def events(c, **q):
    flush(c)
    if "X-CSRF-Token" not in c.headers:
        admin(c)
    r = c.get(f"{SEC}/events", params={"from": "2000-01-01T00:00:00+00:00", **q})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ WAF
def test_observe_mode_records_but_never_blocks(c):
    assert c.get("/api/v1/captcha/challenge?scope=login", headers=at("203.0.113.60")).status_code == 200
    r = anon(c, "/api/v1/captcha/challenge?scope=login&x=1%27+or+%271%27%3D%271")
    assert r.status_code == 200, "режим по умолчанию — наблюдение: запрос обслужен"
    r = anon(c, "/api/.env")
    assert r.status_code in (404, 405, 200), "наблюдение не блокирует даже проверенные правила"
    ev = events(c, kind="waf_hit")
    rules = {e["rule"] for e in ev["items"]}
    assert {"waf:sqli_query", "waf:scanner_path"} <= rules and {e["result"] for e in ev["items"]} == {"observed"}


def test_protect_blocks_verified_rules_for_anonymous_but_keeps_heuristics_in_observation(c):
    set_waf(c, mode="protect")
    assert anon(c, "/api/.env").status_code == 403, "проверенное правило блокирует"
    assert anon(c, "/api/v1/files/..%2f..%2fetc%2fpasswd").status_code == 403
    body = anon(c, "/api/.git/config")
    assert body.json()["detail"] == {"code": "request_blocked", "message": "Запрос отклонён."}, "ответ без подробностей"
    assert c.request("TRACE", "/api/v1/health/live", headers=at("203.0.113.60")).status_code in (403, 405)
    heur = anon(c, "/api/v1/captcha/challenge?scope=login&q=1+union+select+password+from+users")
    assert heur.status_code == 200, "эвристика в «Защите» пока только наблюдается"
    set_waf(c, mode="protect", rules={"sqli_query": "block"})
    assert anon(c, "/api/v1/captcha/challenge?scope=login&q=1+union+select+password+from+users").status_code == 403, "после явного перевода в «Блок» — блокирует"
    ev = events(c, kind="waf_hit")
    assert {"blocked", "observed"} <= {e["result"] for e in ev["items"]}


def test_signed_in_users_are_never_blocked_by_waf_and_normal_requests_pass(c):
    set_waf(c, mode="protect", rules={"sqli_query": "block", "xss_query": "block", "cmd_query": "block", "bot_ua": "block"})
    admin(c)
    for q in ("q=Иван+Петров", "q=o%27brien", "search=select+room+from+list", "from=2026-10-01T00%3A00%3A00%2B00%3A00&limit=50", "format=docx"):
        assert c.get(f"/api/v1/rooms?{q}").status_code == 200, q
    odd = c.get("/api/v1/rooms?q=1+union+select+x", headers={"User-Agent": "sqlmap/1.7"})
    assert odd.status_code == 200, "с сессией ложное срабатывание обычную работу не останавливает (только запись события)"
    assert anon(c, "/api/v1/captcha/challenge?scope=login", headers={"User-Agent": "sqlmap/1.7"}).status_code == 403


def test_exclusions_skip_inspection_by_path_and_by_address(c):
    set_waf(c, mode="protect", exclude_paths="/api/.env\n", exclude_ips="198.51.100.0/24")
    assert anon(c, "/api/.env").status_code != 403
    assert anon(c, "/api/.git/x", ip="198.51.100.9").status_code != 403
    assert anon(c, "/api/.git/x", ip="203.0.113.9").status_code == 403


def test_probe_rule_blocks_an_address_that_enumerates_paths_but_not_a_shared_one(c):
    set_waf(c, mode="protect", rules={"api_probe": "block"}, probe_threshold=10, probe_block_minutes=5)
    for i in range(12):
        anon(c, f"/api/v1/nope-{i}", ip="203.0.113.77")
    refresh_policies(c)
    assert anon(c, "/api/v1/captcha/challenge", ip="203.0.113.77").status_code == 429, "анонимные запросы с этого адреса отклоняются"
    assert anon(c, "/api/v1/captcha/challenge", ip="203.0.113.78").status_code == 200
    # вошедший пользователь (cookie сессии) с того же адреса продолжает работать: блокировка касается анонимных запросов
    admin(c, "198.51.100.7")
    c.headers["X-PG-Peer"] = "203.0.113.77"
    assert c.get("/api/v1/rooms").status_code == 200


def test_waf_off_and_validation(c):
    set_waf(c, mode="off")
    assert anon(c, "/api/.env").status_code != 403
    admin(c)
    assert c.put(f"{SEC}/waf", json={"mode": "block-all"}).status_code == 422
    assert c.put(f"{SEC}/waf", json={"rules": {"no_such_rule": "block"}}).status_code == 422
    assert c.put(f"{SEC}/waf", json={"exclude_paths": "no-slash"}).status_code == 422
    st = c.get(f"{SEC}/waf").json()
    assert {r["id"] for r in st["rules"]} >= {"path_traversal", "sqli_query", "api_probe"} and all("title" in r and "blurb" in r for r in st["rules"])


def test_long_and_streaming_traffic_is_not_touched(c):
    """WAF смотрит только на заголовок запроса: тело любого размера не читается и не буферизуется."""
    set_waf(c, mode="protect")
    admin(c)
    big = b"x" * (3 * 1024 * 1024)
    r = c.post("/api/v1/auth/logout", content=big, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code in (200, 204, 401, 403, 415, 422), r.status_code


# ------------------------------------------------------------------------------------------------ события, блокировки, выгрузка
def test_events_table_filters_export_and_no_secrets(c):
    setup_group(c, "captcha", mode="off")
    for pw in ("TOPSECRET-1", "TOPSECRET-2"):
        c.post("/api/v1/auth/login", json={"login": "alice", "password": pw}, headers=at("203.0.113.5"))
    ev = events(c)
    failed = [e for e in ev["items"] if e["kind"] == "login_failed"]
    assert len(failed) == 2 and failed[0]["ip"] == "203.0.113.5" and failed[0]["account"] == "alice" and failed[0]["component"] == "auth"
    assert events(c, ip="203.0.113.5", kind="login_failed")["total"] == 2 and events(c, account="alice")["total"] >= 2 and events(c, ip="198.51.100.1")["total"] == 0
    assert events(c, kind="login_failed", result="denied")["total"] == 2 and events(c, result="blocked")["total"] == 0
    admin(c)
    csv = c.get(f"{SEC}/events/export", params={"format": "csv", "from": "2000-01-01T00:00:00+00:00"})
    assert csv.status_code == 200 and "Неудачная попытка входа" in csv.text and "TOPSECRET" not in csv.text and csv.text.startswith("﻿")
    js = c.get(f"{SEC}/events/export", params={"format": "json", "from": "2000-01-01T00:00:00+00:00"})
    assert "TOPSECRET" not in js.text and isinstance(json.loads(js.text), list)
    assert "security.events.export" in {a["action"] for a in c.get("/api/v1/admin/audit", params={"action": "security.events"}).json()}


def test_blocked_addresses_unblock_keeps_history_and_manual_block_has_limits(c):
    setup_group(c, "captcha", mode="off")
    setup_group(c, "login_protection", ip_block_failures=4, ip_block_minutes=10, slowdown_after=0, account_guard_failures=500)
    for _ in range(4):
        c.post("/api/v1/auth/login", json={"login": "alice", "password": "bad"}, headers=at("203.0.113.90"))
    admin(c)
    refresh_policies(c)
    blocks = c.get(f"{SEC}/blocks").json()["items"]
    assert [b["ip"] for b in blocks] == ["203.0.113.90"] and blocks[0]["failures_24h"] >= 4 and blocks[0]["rule"] == "ip_failures" and blocks[0]["level"] == 1
    act = c.get(f"{SEC}/activity", params={"ip": "203.0.113.90"}).json()
    assert any(i.get("explain") for i in act["items"]) or any(i["kind"] == "ip_blocked" for i in act["items"])
    assert c.post(f"{SEC}/blocks/unblock", json={"ip": "203.0.113.90"}).status_code == 200
    assert c.post(f"{SEC}/blocks/unblock", json={"ip": "203.0.113.90"}).status_code == 404
    c.headers["X-PG-Peer"] = "198.51.100.7"
    for _ in range(4):
        c.post("/api/v1/auth/login", json={"login": "alice", "password": "bad"}, headers=at("203.0.113.90"))
    again = c.get(f"{SEC}/blocks").json()["items"]
    assert again and again[0]["level"] == 2, "снятие блокировки не обнуляет историю повторов"
    # ручная блокировка
    bad = [({"ip": "203.0.113.55", "minutes": 0, "reason": "x1"}, 422), ({"ip": "203.0.113.55", "minutes": 60, "reason": ""}, 422), ({"ip": "203.0.113.55", "minutes": 10**6, "reason": "много"}, 422),
           ({"ip": "198.51.100.7", "minutes": 5, "reason": "это я"}, 422), ({"ip": "не-адрес", "minutes": 5, "reason": "x"}, 422)]
    for body, code in bad:
        assert c.post(f"{SEC}/blocks", json=body).status_code == code, body
    ok = c.post(f"{SEC}/blocks", json={"ip": "203.0.113.55", "minutes": 30, "reason": "Сообщение службы ИБ"})
    assert ok.status_code == 200
    refresh_policies(c)
    assert anon(c, "/api/v1/captcha/challenge", ip="203.0.113.55").status_code == 429
    admin(c)
    assert any(b["ip"] == "203.0.113.55" and b["manual"] for b in c.get(f"{SEC}/blocks").json()["items"])
    acts = {a["action"] for a in c.get("/api/v1/admin/audit", params={"action": "security.ip"}).json()}
    assert {"security.ip.block", "security.ip.unblock"} <= acts


def test_manual_block_of_shared_address_needs_confirmation(c):
    setup_group(c, "captcha", mode="off")
    setup_group(c, "login_protection", shared_ip_accounts=2)
    for u, p in (("alice", "alice-pass"), ("bob", "bob-pass")):
        assert c.post("/api/v1/auth/login", json={"login": u, "password": p}, headers=at("203.0.113.40")).status_code == 200
        c.post("/api/v1/auth/logout")
        c.cookies.clear()
    admin(c)
    r = c.post(f"{SEC}/blocks", json={"ip": "203.0.113.40", "minutes": 5, "reason": "проверка"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "shared_address"
    assert c.post(f"{SEC}/blocks", json={"ip": "203.0.113.40", "minutes": 5, "reason": "проверка", "confirm_shared": True}).status_code == 200


def test_overview_counts_and_attention(c):
    setup_group(c, "captcha", mode="off")
    c.post("/api/v1/auth/login", json={"login": "alice", "password": "bad"}, headers=at("203.0.113.5"))
    flush(c)
    admin(c)
    o = c.get(f"{SEC}/overview").json()
    assert o["failed_logins_24h"] >= 1 and o["blocked_now"] == 0 and o["protection"]["waf_mode"] == "observe" and o["protection"]["captcha_mode"] == "off"
    assert any("CAPTCHA выключена" in a for a in o["attention"])


def test_security_pages_need_admin(c):
    from .conftest import login

    login(c, "alice")
    for url in (f"{SEC}/events", f"{SEC}/blocks", f"{SEC}/overview", f"{SEC}/waf", f"{SEC}/ip-diagnostics"):
        assert c.get(url).status_code == 403, url
