"""Доверенные подсети: ограничение по IP выключено по умолчанию; включение с проверкой списка и адреса администратора, пробным сроком, подтверждением и автооткатом;
отказ нейтральный (403 без сведений о настройках); внутренний трафик контейнеров, гости и публичный API — по настройкам; аварийное выключение; запись в аудит. Без сети."""
from __future__ import annotations

import time

import pytest
from fakeredis import FakeServer
from fastapi.testclient import TestClient

from app.security.allownets import MESSAGE, parse_cidr

from .conftest import login
from .test_login_protection import WEB, at, instance, server  # noqa: F401  (фикстура server и помощники общие)

API = "/api/v1/admin/security/ip-access"


@pytest.fixture
def c(tmp_path, directory, server):
    with instance(tmp_path, directory, server) as cl:
        yield cl


def admin(c: TestClient, ip="198.51.100.7"):
    """Вход администратора с адреса ip; дальше все запросы клиента идут «с этого адреса»."""
    c.cookies.clear()
    r = c.post("/api/v1/auth/login", json={"login": "root", "password": "root-pass"}, headers=at(ip))
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    c.headers["X-PG-Peer"] = ip


def holder(c):
    return c.app_obj.state.allownets


def refresh(c):
    c.portal.call(lambda: holder(c).refresh(c.app_obj.state.settings_svc, c.app_obj.state.session_maker))


def test_cidr_parsing_and_validation():
    assert parse_cidr("192.0.2.10") == "192.0.2.10/32" and parse_cidr("198.51.100.77/24") == "198.51.100.0/24" and parse_cidr("2001:db8::1") == "2001:db8::1/128"
    for bad in ("", "abc", "300.1.1.1", "10.0.0.0/33", "0.0.0.0/0", "::/0"):
        with pytest.raises(ValueError):
            parse_cidr(bad)


def test_off_by_default_everything_is_reachable(c):
    assert c.get("/api/v1/captcha/challenge", headers=at("203.0.113.9")).status_code == 200
    admin(c)
    st = c.get(API).json()
    assert st["enabled"] is False and st["active"] is False and st["entries"] == [] and st["my_ip"] == "198.51.100.7" and st["denied_24h"] == 0
    assert st["covered"] and st["not_covered"], "интерфейс показывает, что охвачено и что требует внешнего firewall"


def test_cannot_enable_with_empty_list_or_without_own_address_and_trial_requires_confirmation(c):
    admin(c)
    assert c.put(f"{API}/settings", json={"enabled": True}).status_code == 422, "пустой список закрыл бы доступ всем"
    assert c.post(f"{API}/entries", json={"cidr": "192.0.2.0/24", "comment": "Офис"}).status_code == 201
    r = c.put(f"{API}/settings", json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "admin_not_allowed" and r.json()["detail"]["my_ip"] == "198.51.100.7", "адрес администратора не в списке — предупреждение"
    assert c.get(API).json()["enabled"] is False
    assert c.post(f"{API}/entries", json={"cidr": "198.51.100.0/24", "comment": "VPN админов"}).status_code == 201
    on = c.put(f"{API}/settings", json={"enabled": True})
    assert on.status_code == 200 and on.json()["active"] and on.json()["pending"] and 0 < on.json()["seconds_left"] <= 300
    assert c.post(f"{API}/confirm").json()["pending"] is False


def test_denied_network_gets_neutral_403_and_allowed_one_passes(c):
    admin(c)
    c.post(f"{API}/entries", json={"cidr": "198.51.100.0/24"})
    c.post(f"{API}/entries", json={"cidr": "2001:db8:aa::/48", "comment": "IPv6 офис"})
    c.put(f"{API}/settings", json={"enabled": True})
    c.post(f"{API}/confirm")
    stranger = c.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "203.0.113.50"})
    assert stranger.status_code == 403 and stranger.json()["detail"] == {"code": "access_restricted", "message": MESSAGE}
    body = stranger.text
    assert "198.51.100" not in body and "2001:db8" not in body and "ip_access" not in body, "список разрешённых сетей не раскрывается"
    assert stranger.headers["cache-control"] == "no-store"
    assert c.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "198.51.100.200"}).status_code == 200
    assert c.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "2001:db8:aa::5"}).status_code == 200, "IPv6-подсеть работает"
    assert c.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "2001:db8:bb::5"}).status_code == 403
    # попытка подделать адрес заголовком от недоверенного клиента не помогает
    assert c.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "203.0.113.50", "X-Forwarded-For": "198.51.100.9", "X-Real-IP": "198.51.100.9"}).status_code == 403
    # проверки готовности мониторинга и внутренний трафик (без web) проходят
    assert c.get("/api/v1/health/live", headers={"X-PG-Peer": "203.0.113.50"}).status_code == 200
    internal = c.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": ""})
    assert internal.status_code in (200, 429), "запрос без заголовка web с частного адреса — внутренний трафик, не отказ"
    assert c.get(API).json()["denied_24h"] >= 3


def test_html_navigations_get_a_neutral_page_not_json(c):
    admin(c)
    c.post(f"{API}/entries", json={"cidr": "198.51.100.0/24"})
    c.put(f"{API}/settings", json={"enabled": True})
    r = c.get("/not-an-api-path", headers={"X-PG-Peer": "203.0.113.50"})
    assert r.status_code == 403 and r.headers["content-type"].startswith("text/html") and MESSAGE in r.text and "198.51" not in r.text


def test_websocket_from_denied_network_is_closed(c):
    admin(c)
    c.post(f"{API}/entries", json={"cidr": "198.51.100.0/24"})
    c.put(f"{API}/settings", json={"enabled": True})
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/v1/ws", headers={"X-PG-Peer": "203.0.113.50"}) as ws:
            ws.receive_text()
    assert e.value.code == 4403


def test_guest_and_public_api_exceptions_are_explicit(c):
    admin(c)
    c.post(f"{API}/entries", json={"cidr": "198.51.100.0/24"})
    c.put(f"{API}/settings", json={"enabled": True})
    c.post(f"{API}/confirm")
    far = {"X-PG-Peer": "203.0.113.50"}
    assert c.get("/api/v1/guest/room/x", headers=far).status_code == 403
    assert c.get("/api/public/v1/health", headers=far).status_code == 403
    c.put(f"{API}/settings", json={"exempt_guests": True, "exempt_public_api": True})
    assert c.get("/api/v1/guest/room/x", headers=far).status_code != 403
    assert c.get("/api/public/v1/health", headers=far).status_code != 403


def test_changes_that_cut_the_admin_off_need_acknowledgement_and_rearm_the_trial(c):
    admin(c)
    a = c.post(f"{API}/entries", json={"cidr": "198.51.100.0/24"}).json()["entries"][0]
    b = c.post(f"{API}/entries", json={"cidr": "192.0.2.0/24"}).json()["entries"][1]
    c.put(f"{API}/settings", json={"enabled": True})
    c.post(f"{API}/confirm")
    assert c.request("DELETE", f"{API}/entries/{b['id']}", json={}).status_code == 200, "лишняя запись удаляется свободно"
    last = c.request("DELETE", f"{API}/entries/{a['id']}", json={})
    assert last.status_code == 422, "нельзя удалить последнюю разрешённую сеть при действующем ограничении"
    c.post(f"{API}/entries", json={"cidr": "192.0.2.0/24"})
    other = c.get(API).json()["entries"][-1]
    bad = c.post(f"{API}/entries", json={"cidr": "не-адрес"})
    assert bad.status_code == 422 and "не IP-адрес" in bad.json()["detail"]
    assert c.post(f"{API}/entries", json={"cidr": "192.0.2.0/24"}).status_code == 409
    cut = c.patch(f"{API}/entries/{a['id']}", json={"enabled": False})
    assert cut.status_code == 409 and cut.json()["detail"]["code"] == "admin_not_allowed"
    ok = c.patch(f"{API}/entries/{a['id']}", json={"enabled": False, "acknowledge_lockout": True})
    assert ok.status_code == 200 and ok.json()["pending"] is True, "изменение без подтверждения откатится само"
    assert other["cidr"] == "192.0.2.0/24"
    assert c.get(API).status_code == 403, "после изменения администратор действительно отрезан — поэтому и нужен автооткат"


def test_unconfirmed_restriction_reverts_itself_and_is_audited(c, monkeypatch):
    admin(c)
    c.post(f"{API}/entries", json={"cidr": "198.51.100.0/24"})
    c.put(f"{API}/settings", json={"enabled": True})
    far = {"X-PG-Peer": "203.0.113.50"}
    assert c.get("/api/v1/captcha/challenge", headers=far).status_code == 403
    real = time.time
    monkeypatch.setattr("app.security.allownets.time.time", lambda: real() + 400)       # пробный срок (5 мин) вышел, подтверждения не было
    refresh(c)
    assert c.get("/api/v1/captcha/challenge", headers=far).status_code == 200, "ограничение выключилось"
    assert c.get(API).json()["enabled"] is False
    rows = c.get("/api/v1/admin/audit", params={"action": "security.ip_access"}).json()
    assert "security.ip_access.auto_revert" in {r["action"] for r in rows}


def test_forced_off_by_environment_and_console_reset(tmp_path, directory, server, monkeypatch):
    with instance(tmp_path, directory, server) as c1:
        admin(c1)
        c1.post(f"{API}/entries", json={"cidr": "198.51.100.0/24"})
        c1.put(f"{API}/settings", json={"enabled": True})
        c1.post(f"{API}/confirm")
        assert c1.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "203.0.113.50"}).status_code == 403
    monkeypatch.setenv("IP_ACCESS_DISABLE", "1")
    with instance(tmp_path, directory, server) as c2:
        admin(c2, "203.0.113.50")
        assert c2.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "203.0.113.50"}).status_code == 200
        st = c2.get(API).json()
        assert st["forced_off"] and st["active"] is False and any("IP_ACCESS_DISABLE" in w for w in st["warnings"])


def test_console_command_switches_the_restriction_off_without_the_web_interface(c):
    from app.cli import disable_ip_access  # noqa: PLC0415

    admin(c)
    c.post(f"{API}/entries", json={"cidr": "198.51.100.0/24"})
    c.put(f"{API}/settings", json={"enabled": True})
    c.post(f"{API}/confirm")
    assert c.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "203.0.113.50"}).status_code == 403

    async def run():
        async with c.app_obj.state.session_maker() as db:
            return await disable_ip_access(db, c.app_obj.state.settings_svc, actor="console")
    assert c.portal.call(run) is True
    refresh(c)
    assert c.get("/api/v1/captcha/challenge", headers={"X-PG-Peer": "203.0.113.50"}).status_code == 200
    assert c.get(API).json()["entries"], "список сетей сохранён: выключено только ограничение"


def test_only_administrators_manage_the_list(c):
    login(c, "alice")
    for method, url in (("get", API), ("post", f"{API}/entries"), ("put", f"{API}/settings"), ("post", f"{API}/confirm")):
        assert getattr(c, method)(url).status_code in (403, 422), (method, url)
