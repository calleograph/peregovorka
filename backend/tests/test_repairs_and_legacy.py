"""Эксплуатационный путь обновления: помощник с правами, «Исправить автоматически», раздельные итоги, перенос прежней настройки LDAP из .env,
устойчивость запуска к ошибке каталога CA. Вымышленные данные: example.local, user1, admin."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fakeredis import FakeAsyncRedis

from app.main import create_app
from app.models import Base, CaCertificate, LdapProfile
from app.services import updates as upd
from app.services.ldap_profiles import LdapService
from app.util_errors import describe_error

from .conftest import login, make_settings
from .test_local_admin_ldap import cert_pem, make_local_admin
from .test_updates import chan, heartbeat

U = "/api/v1/admin/updates"
REPO = Path(__file__).resolve().parents[2]
ADMIN_PW = "Qm4-Rt8w-Yn2k-Bd6x"


def repairs_json(d: Path, *ids: str):
    items = [{"id": i, "title": f"Проблема {i}", "meaning": "Что это значит", "fix": "Что будет сделано"} for i in ids]
    (d / "repairs.json").write_text(json.dumps({"checked_at": time.time(), "uid": 0, "profile": "standalone", "items": items}), encoding="utf-8")


# ------------------------------------------------------------------------------------------------ помощник и белый список
def test_repair_ids_mirror_the_shell_whitelist():
    text = (REPO / "scripts/lib/repairlib.sh").read_text(encoding="utf-8")
    shell = re.search(r"^REPAIR_IDS=\(([^)]*)\)", text, re.M).group(1).split()
    assert tuple(shell) == upd.REPAIR_IDS


def test_channel_refuses_unknown_actions_and_repair_ids(tmp_path):
    ch = upd.Channel(str(tmp_path))
    for bad in ("rm -rf /", "", "../update", "data_dirs; touch x", "DATA_DIRS"):
        with pytest.raises(ValueError):
            ch.request("repair", by="admin", repair=bad)
    with pytest.raises(ValueError):
        ch.request("shell", by="admin")
    ch.request("repair", by="a\nb=c", repair="data_dirs")
    body = (tmp_path / "request.txt").read_text()
    assert "action=repair\n" in body and "repair=data_dirs\n" in body and body.count("\n") == len(body.splitlines())
    assert "by=a_b_c" in body                                   # перевод строки в имени не создаёт новых полей запроса


def test_helper_without_privileges_is_reported_and_fix_is_refused(client):
    d = chan(client)
    login(client, "root")
    o = client.get(U).json()
    assert o["helper"]["problem"] == "not_installed" and o["helper"]["privileged"] is False
    heartbeat(d, uid=1000)
    o = client.get(U).json()
    assert o["helper"]["problem"] == "no_privileges"
    repairs_json(d, "data_dirs")
    r = client.get(f"{U}/repairs").json()
    manual = [i for i in r["items"] if i["id"] == "updater_helper"]
    assert manual and manual[0]["fixable"] is False and "updater.sh install" in manual[0]["command"]
    assert all(i["fixable"] is False for i in r["items"] if i["kind"] == "helper")
    resp = client.post(f"{U}/repairs/data_dirs/fix")
    assert resp.status_code == 409 and "прав" in resp.json()["detail"]
    assert not (d / "request.txt").exists()


def test_fix_writes_only_a_whitelisted_structured_request(client):
    d = chan(client)
    heartbeat(d, uid=0)
    repairs_json(d, "data_dirs", "nginx_site")
    login(client, "root")
    r = client.get(f"{U}/repairs").json()
    assert [i["id"] for i in r["items"]] == ["data_dirs", "nginx_site"] and all(i["fixable"] for i in r["items"])
    assert client.post(f"{U}/repairs/nginx_site/fix").status_code == 200
    req = dict(line.split("=", 1) for line in (d / "request.txt").read_text().splitlines())
    assert req["action"] == "repair" and req["repair"] == "nginx_site" and set(req) == {"id", "action", "force_build", "pull", "repair", "by", "at"}
    (d / "request.txt").unlink()
    # произвольное и «не обнаруженное сейчас» не принимается
    assert client.post(f"{U}/repairs/rm -rf/fix").status_code in (404, 405)
    assert client.post(f"{U}/repairs/..%2Fupdate/fix").status_code in (404, 405)
    assert client.post(f"{U}/repairs/shell/fix").status_code == 404
    assert client.post(f"{U}/repairs/sysctl/fix").status_code == 409            # в последней проверке такой проблемы нет
    assert not (d / "request.txt").exists()


def test_fix_is_refused_while_busy_and_requires_admin(client):
    d = chan(client)
    heartbeat(d, uid=0, state="repairing", action="repair", repair_id="data_dirs")
    repairs_json(d, "data_dirs")
    login(client, "root")
    assert client.post(f"{U}/repairs/data_dirs/fix").status_code == 409
    assert client.post(f"{U}/run", json={"confirm": True}).status_code == 409           # и обновление в это время не стартует
    assert client.get(f"{U}/repairs").json()["busy"] is True
    login(client, "alice")
    assert client.get(f"{U}/repairs").status_code == 403 and client.post(f"{U}/repairs/data_dirs/fix").status_code == 403


# ------------------------------------------------------------------------------------------------ раздельные итоги
def test_outcome_separates_update_deployment_health_and_integrations(client):
    d = chan(client)
    heartbeat(d, uid=0, finished_at=time.time(), exit_code=3, result="ok", action="update", update_status="ok", deploy_status="ok",
              health_status="ok", integration_status="fail", integration_issues="LDAP/LDAPS")
    login(client, "root")
    o = client.get(U).json()["outcome"]
    assert o == {"update": "ok", "deployment": "ok", "health": "ok", "integrations": "fail", "integration_issues": "LDAP/LDAPS", "needs_attention": True}
    assert client.get(f"{U}/log").json()["outcome"]["integrations"] == "fail"


def test_outcome_for_old_status_files_and_failures(client):
    d = chan(client)
    heartbeat(d, finished_at=time.time(), exit_code=0, result="ok")                     # status.json прежней версии, без новых полей
    login(client, "root")
    o = client.get(U).json()["outcome"]
    assert o["update"] == "ok" and o["integrations"] == "ok" and o["needs_attention"] is False
    heartbeat(d, finished_at=time.time(), exit_code=1, result="failed", update_status="failed")
    o = client.get(U).json()["outcome"]
    assert o["update"] == "failed" and o["needs_attention"] is True
    heartbeat(d, finished_at=time.time(), exit_code=0, result="ok", action="repair", repair_id="data_dirs")
    assert client.get(U).json()["outcome"] is None                                      # итог исправления — не итог обновления


# ------------------------------------------------------------------------------------------------ прежняя настройка LDAP
def legacy_app(tmp_path, monkeypatch, **over):
    pem, _der = cert_pem("Example Root CA")
    cafile = tmp_path / "ad-ca.pem"
    cafile.write_bytes(pem)
    params = dict(ldap_uris="ldaps://dc1.example.local:636", ldap_base_dn="DC=example,DC=local", ldap_bind_dn="CN=svc-reader,DC=example,DC=local",
                  ldap_bind_password="Svc-Pass-9x-test", ldap_ca_file=str(cafile), ldap_admin_group_dn="CN=Peregovorka-Admins,OU=Groups,DC=example,DC=local",
                  ldap_access_group_dn="CN=Peregovorka-Users,OU=Groups,DC=example,DC=local")
    params.update(over)
    s = make_settings(tmp_path, **params)
    return make_app(s), s


def make_app(s):
    """Схема БД создаётся ДО старта приложения — как после миграций на реальном сервере, поэтому загрузка каталога при запуске идёт по-настоящему."""
    import sqlalchemy  # noqa: PLC0415

    eng = sqlalchemy.create_engine(s.database_url.replace("sqlite+aiosqlite", "sqlite"))
    Base.metadata.create_all(eng)
    eng.dispose()
    return create_app(s, redis_factory=lambda x: FakeAsyncRedis(decode_responses=True), directory_factory=None, start_workers=False)


def run_app(app):
    from fastapi.testclient import TestClient  # noqa: PLC0415

    c = TestClient(app, base_url="http://testserver")
    c.__enter__()
    c.app_obj = app
    return c


OK_STAGES = {"ok": True, "stages": [{"stage": "config", "ok": True, "message": "ok", "ms": 0}, {"stage": "bind", "ok": True, "message": "ok", "ms": 1}]}
BAD_STAGES = {"ok": False, "stages": [{"stage": "tcp", "ok": False, "message": "TCP: нет соединения с dc1.example.local:636", "ms": 3}]}


def test_legacy_ldap_is_visible_and_import_moves_everything_securely(tmp_path, monkeypatch):
    monkeypatch.setattr(LdapService, "diagnose", lambda self, r: OK_STAGES)
    app, s = legacy_app(tmp_path, monkeypatch)
    c = run_app(app)
    try:
        make_local_admin(c, must_change=False)
        login(c, "admin", ADMIN_PW)
        resp = c.get("/api/v1/admin/ldap-legacy")
        assert resp.status_code == 200, resp.text
        st = resp.json()
        assert st["present"] and st["needs_import"] and st["active"] and not st["migrated"] and st["password_set"] and st["ca_file_readable"]
        assert "Svc-Pass-9x-test" not in json.dumps(st)                                   # пароль в ответах не появляется никогда
        lst = c.get("/api/v1/admin/ldap-profiles").json()
        assert lst["legacy"]["needs_import"] and lst["items"] == []
        rep = c.get(f"{U}/repairs").json()
        assert any(i["id"] == "ldap_legacy" and i["kind"] == "backend" and i["fixable"] for i in rep["items"])

        r = c.post("/api/v1/admin/ldap-legacy/import")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["profiles"] == ["Основной каталог (из .env)"] and out["ca_added"] == 1 and out["groups_added"] == 2
        assert "Svc-Pass-9x-test" not in r.text
        st = c.get("/api/v1/admin/ldap-legacy").json()
        assert st["migrated"] and not st["needs_import"] and not st["active"]
        prof = c.get("/api/v1/admin/ldap-profiles").json()["items"][0]
        assert prof["host"] == "dc1.example.local" and prof["port"] == 636 and prof["secret_set"] and "secret" not in prof
        assert [i["label"] for i in c.get("/api/v1/admin/ca").json()["items"]][0].startswith("Импорт из прежней")
        acc = c.get("/api/v1/admin/settings/access").json()
        assert "CN=Peregovorka-Admins,OU=Groups,DC=example,DC=local" in json.dumps(acc)
        # пароль лежит в базе только зашифрованным
        async def row():
            async with app.state.session_maker() as db:
                from sqlalchemy import select  # noqa: PLC0415
                return (await db.execute(select(LdapProfile))).scalars().one()
        enc = c.portal.call(row).secret_enc
        assert enc and "Svc-Pass-9x-test" not in enc
        actions = [a["action"] for a in c.get("/api/v1/admin/audit").json()]
        assert "ldap.legacy_import" in actions
        assert c.post("/api/v1/admin/ldap-legacy/import").status_code == 409              # повторно переносить нечего
    finally:
        c.__exit__(None, None, None)


def test_legacy_import_is_rolled_back_when_connection_test_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(LdapService, "diagnose", lambda self, r: BAD_STAGES)
    app, s = legacy_app(tmp_path, monkeypatch)
    c = run_app(app)
    try:
        make_local_admin(c, must_change=False)
        login(c, "admin", ADMIN_PW)
        r = c.post("/api/v1/admin/ldap-legacy/import")
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "не прошла" in detail["message"] and "прежняя настройка продолжает работать" in detail["message"] and detail["stages"][0]["stage"] == "tcp"
        st = c.get("/api/v1/admin/ldap-legacy").json()
        assert st["needs_import"] and not st["migrated"] and st["active"]               # прежний вход по домену остался рабочим
        assert c.get("/api/v1/admin/ldap-profiles").json()["items"] == []
        assert c.get("/api/v1/admin/ca").json()["items"][0]["source"] == "file"          # в базу ничего не попало: только файл из .env
        assert "Svc-Pass-9x-test" not in r.text
    finally:
        c.__exit__(None, None, None)


def test_auto_import_runs_once_in_background_and_failure_keeps_legacy(tmp_path, monkeypatch):
    app, s = legacy_app(tmp_path, monkeypatch)
    c = run_app(app)
    try:
        monkeypatch.setattr(LdapService, "diagnose", lambda self, r: BAD_STAGES)
        mig = app.state.legacy_ldap
        assert c.portal.call(lambda: mig.auto_import(app.state.session_maker, app.state.redis, app.state.directory)) is False
        assert "не прошла" in mig.last_error and app.state.directory.configured          # вход по старой настройке не сломан
        monkeypatch.setattr(LdapService, "diagnose", lambda self, r: OK_STAGES)
        assert c.portal.call(lambda: mig.auto_import(app.state.session_maker, app.state.redis, app.state.directory)) is True
        assert mig.last_error == "" and app.state.directory.configured
        assert c.portal.call(lambda: mig.auto_import(app.state.session_maker, app.state.redis, app.state.directory)) is False   # уже перенесено
    finally:
        c.__exit__(None, None, None)


def test_legacy_import_needs_complete_config_and_master_key(tmp_path, monkeypatch):
    monkeypatch.setattr(LdapService, "diagnose", lambda self, r: OK_STAGES)
    app, _ = legacy_app(tmp_path, monkeypatch, ldap_bind_password="")
    c = run_app(app)
    try:
        make_local_admin(c, must_change=False)
        login(c, "admin", ADMIN_PW)
        r = c.post("/api/v1/admin/ldap-legacy/import")
        assert r.status_code == 409 and "неполная" in r.json()["detail"]["message"]
    finally:
        c.__exit__(None, None, None)


# ------------------------------------------------------------------------------------------------ устойчивость запуска
def test_boot_survives_unwritable_ca_dir_and_keeps_legacy_directory(tmp_path, monkeypatch, caplog):
    """Реальный сбой 0.3.0: каталог CA недоступен на запись → rebuild() падал → каталог (LDAP) не загружался вовсе («not_configured»)."""
    (tmp_path / "ca").write_text("это файл, а не каталог — запись набора CA невозможна")
    app, s = legacy_app(tmp_path, monkeypatch)
    c = run_app(app)
    try:
        assert app.state.directory.configured, "каталог из .env должен загрузиться независимо от сбоя CA"
        be = app.state.boot_errors
        assert "ca" in be and be["ca"].startswith("FileExistsError") and str(tmp_path / "ca") in be["ca"]
        assert "Svc-Pass" not in json.dumps(be)
        make_local_admin(c, must_change=False)
        login(c, "admin", ADMIN_PW)
        heartbeat(chan(c), uid=0)
        items = c.get(f"{U}/repairs").json()["items"]
        assert any(i["id"] == "data_dirs" and "сертификат" in i["title"].lower() and i["fixable"] for i in items)
    finally:
        c.__exit__(None, None, None)


def test_diagnostics_does_not_call_a_failed_load_not_configured(tmp_path, monkeypatch):
    """В .env LDAP задан, но не загружен (нет CA-файла) — это сбой, а не «каталог не настроен»: smoke-test должен сказать об этом."""
    from app.services.diagnostics import build_report  # noqa: PLC0415

    app, s = legacy_app(tmp_path, monkeypatch, ldap_ca_file="")
    c = run_app(app)
    try:
        assert not app.state.directory.configured
        rep = c.portal.call(lambda: build_report(app))
        assert rep["checks"]["ldap"]["ok"] is False and rep["checks"]["ldap"]["error"] == "legacy_not_loaded"
    finally:
        c.__exit__(None, None, None)
    # а на свежей установке (в .env LDAP нет) — по-прежнему «не настроено», не ошибка
    from .conftest import make_settings as ms  # noqa: PLC0415

    (tmp_path / "x").mkdir()
    app2 = make_app(ms(tmp_path / "x"))
    c2 = run_app(app2)
    try:
        rep = c2.portal.call(lambda: build_report(app2))
        assert rep["checks"]["ldap"] == {"ok": True, "configured": False}
    finally:
        c2.__exit__(None, None, None)


def test_describe_error_is_safe_and_useful():
    assert describe_error(PermissionError(13, "Permission denied", "/data/ca/bundle.pem.tmp")) == "PermissionError [Errno 13] Permission denied: /data/ca/bundle.pem.tmp"
    assert describe_error(ValueError("пароль=secret")) == "ValueError"                  # текст произвольных исключений (возможны секреты) не попадает
