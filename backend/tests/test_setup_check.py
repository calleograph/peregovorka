"""Чек-лист «Первоначальная настройка и проверка сервера»: настройка и проверки состояния, повторный запуск, сохранение результата, аудит, расширяемость."""
from __future__ import annotations

import asyncio

from app.services import setup_check as sc

from .conftest import login

ADM = "/api/v1/admin"
HEALTH_IDS = {"public_url", "livekit", "livekit_public", "rtc_ports", "ldap_health", "asr", "llm", "disk", "master_key"}


def test_status_before_any_check_has_steps_and_an_empty_result(client):
    login(client, "root")
    st = client.get(f"{ADM}/setup/status").json()
    assert {s["id"] for s in st["steps"]} >= {"ldap", "ca", "access", "login_acl", "storage", "mail", "check"}
    assert st["checks"] == [] and st["last_check_at"] is None
    assert st["summary"]["total"] == len(st["steps"]), "до первой проверки в итоге только шаги настройки"


def test_running_the_check_returns_every_health_item_and_remembers_the_result(client):
    login(client, "root")
    r = client.post(f"{ADM}/setup/check")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {c["id"] for c in body["checks"]}
    assert HEALTH_IDS <= ids, ids
    assert all(c["status"] in ("ok", "warn", "fail", "todo") and c["title"] and c["detail"] and c["page"] for c in body["checks"])
    by = {c["id"]: c for c in body["checks"]}
    assert by["public_url"]["status"] == "warn" and "HTTP" in by["public_url"]["detail"]        # тестовый адрес — http://testserver
    assert by["livekit"]["status"] == "fail" and by["rtc_ports"]["status"] in ("warn", "fail"), "LiveKit в тестах недоступен — это видно, а не скрыто"
    assert by["master_key"]["status"] == "ok" and by["disk"]["status"] in ("ok", "warn", "fail")
    assert body["last_check_at"]
    again = client.get(f"{ADM}/setup/status").json()                                          # результат сохранён: открытие страницы не гоняет проверки заново
    assert again["last_check_at"] == body["last_check_at"] and [c["id"] for c in again["checks"]] == [c["id"] for c in body["checks"]]
    s = again["summary"]
    assert s["total"] == s["ok"] + s["warn"] + s["fail"] + s["todo"] and s["attention"] == s["warn"] + s["fail"] + s["todo"] and s["attention"] > 0


def test_check_is_recorded_in_the_audit_without_details_of_the_servers(client):
    login(client, "root")
    client.post(f"{ADM}/setup/check")
    rows = [a for a in client.get(f"{ADM}/audit").json() if a["action"] == "setup.check"]
    assert len(rows) == 1 and set(rows[0]["details"]) == {"ok", "warn", "fail", "todo", "total"}


def test_only_administrators_can_open_or_run_the_check(client):
    login(client, "alice")
    assert client.get(f"{ADM}/setup/status").status_code == 403
    assert client.post(f"{ADM}/setup/check").status_code == 403


def test_a_new_check_appears_without_changing_the_interface_and_a_broken_one_does_not_stop_the_others(client, monkeypatch):
    async def check_backup(app, db, ctx):
        return [sc.item("backup", "Резервное копирование", sc.WARN, "Последняя копия устарела", "config_backup")]

    async def check_broken(app, db, ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(sc, "HEALTH_CHECKS", [*sc.HEALTH_CHECKS, check_backup, check_broken])
    login(client, "root")
    by = {c["id"]: c for c in client.post(f"{ADM}/setup/check").json()["checks"]}
    assert by["backup"]["status"] == "warn" and by["backup"]["page"] == "config_backup"
    assert by["broken"]["status"] == "fail" and "RuntimeError" in by["broken"]["detail"] and HEALTH_IDS <= set(by)


def test_summary_counts_configuration_and_health_together():
    s = sc.summarize([{"done": True}, {"done": False}], [sc.item("a", "a", sc.OK, "x", "p"), sc.item("b", "b", sc.FAIL, "x", "p"), sc.item("c", "c", sc.WARN, "x", "p")])
    assert s == {"ok": 2, "warn": 1, "fail": 1, "todo": 1, "total": 5, "attention": 3}


def test_public_url_with_an_ip_address_is_a_warning_because_mail_links_will_carry_it():
    from types import SimpleNamespace
    from app.services.setup_check import check_public_url
    app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(app_public_url="https://192.0.2.10")))
    (r,) = asyncio.run(check_public_url(app, None, {}))
    assert r["status"] == "warn" and "DNS-имя" in r["detail"]


def test_mail_links_use_a_domain_from_allowed_origins_when_the_public_url_is_an_ip():
    from app.config import Settings
    kw = {"allowed_origins": "https://192.0.2.10, https://meet.example.org"}
    assert Settings(app_public_url="https://192.0.2.10", **kw).link_base == "https://meet.example.org"
    assert Settings(app_public_url="https://meet.example.org", **kw).link_base == "https://meet.example.org"
    assert Settings(app_public_url="https://192.0.2.10").link_base == "https://192.0.2.10"


def test_mail_policy_link_base_accepts_a_domain_and_refuses_a_path():
    import pytest
    from pydantic import ValidationError
    from app.services.settings import MailPolicySettings
    assert MailPolicySettings(link_base=" https://meet.example.org/ ").link_base == "https://meet.example.org"
    assert MailPolicySettings().link_base == ""
    for bad in ("meet.example.org", "https://meet.example.org/x", "https://a b"):
        with pytest.raises(ValidationError):
            MailPolicySettings(link_base=bad)
