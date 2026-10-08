"""Путь ОБНОВЛЕНИЯ, а не чистой установки: база и .env версии 0.2.x → миграции → 0.3.x+, вход по домену через прежнюю настройку LDAP_* остаётся рабочим.

Каждая новая версия проверяется как обновление предыдущей релизной схемы с уже существующими данными: именно здесь находились самые неприятные
эксплуатационные ошибки (права на новые каталоги, прежний LDAP, перенос настроек). Данные вымышленные: example.local, user1, admin.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fakeredis import FakeAsyncRedis

from app.auth.directory import DirectoryError, DirectoryIdentity, LdapDirectory
from app.main import create_app
from app.services.ldap_profiles import LdapService

from .conftest import login, make_settings
from .test_local_admin_ldap import cert_pem, make_local_admin
from .test_repairs_and_legacy import ADMIN_PW, OK_STAGES, run_app

BACKEND = Path(__file__).resolve().parents[1]
REV_0_2_0 = "0007"            # последняя миграция релиза 0.2.0 (в 0.3.0 добавлена 0008)


def _cfg(db: Path) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db}")
    return cfg


def _identity(sam: str) -> DirectoryIdentity:
    return DirectoryIdentity(f"guid-{sam}", f"cn={sam},ou=users,dc=example,dc=local", sam, f"{sam}@example.local", sam.title(), f"{sam}@example.local",
                             frozenset({"cn=peregovorka-users,ou=groups,dc=example,dc=local"}))


def test_upgrade_from_0_2_x_keeps_data_and_legacy_ldap_login_works(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    cfg = _cfg(db)
    command.upgrade(cfg, REV_0_2_0)                                           # схема, какой она была у 0.2.x
    eng = sa.create_engine(f"sqlite:///{db}")
    with eng.begin() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        assert "ldap_profiles" not in tables and "ca_certificates" not in tables  # новых таблиц 0.3 ещё нет
        meta = sa.MetaData()
        meta.reflect(conn)
        rooms = meta.tables["rooms"]
        required = {c.name: c for c in rooms.columns if not c.nullable and c.default is None and c.server_default is None}
        row = {"id": str(uuid.uuid4()), "slug": "old-room", "name": "Комната из 0.2", "description": "создана до обновления",
               "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}
        for name, col in required.items():
            row.setdefault(name, 0 if isinstance(col.type, (sa.Integer, sa.Boolean)) else datetime.now(timezone.utc) if isinstance(col.type, sa.DateTime) else "x")
        conn.execute(rooms.insert().values(**{k: v for k, v in row.items() if k in rooms.c}))
    eng.dispose()

    command.upgrade(cfg, "head")                                              # «обновление»: миграции 0.3.x поверх существующих данных
    eng = sa.create_engine(f"sqlite:///{db}")
    with eng.connect() as conn:
        assert {"ldap_profiles", "ca_certificates", "mail_profiles", "mail_messages", "storage_sync_runs"} <= set(sa.inspect(conn).get_table_names())
        assert conn.execute(sa.text("select name from rooms where slug='old-room'")).scalar_one() == "Комната из 0.2"
    eng.dispose()

    # прежний .env: LDAP_* заданы, управляемых подключений в базе нет; каталог CA ещё недоступен процессу (владелец — прежний пользователь)
    pem, _ = cert_pem("Example Root CA")
    cafile = tmp_path / "ad-ca.pem"
    cafile.write_bytes(pem)
    (tmp_path / "ca").write_text("каталог создан от другого владельца: запись невозможна")
    s = make_settings(tmp_path, ldap_uris="ldaps://dc1.example.local:636", ldap_base_dn="DC=example,DC=local", ldap_bind_dn="CN=svc-reader,DC=example,DC=local",
                      ldap_bind_password="Svc-Pass-9x-test", ldap_ca_file=str(cafile), ldap_admin_group_dn="CN=Peregovorka-Admins,OU=Groups,DC=example,DC=local")

    def authenticate(self, login_, password):
        if login_.split("@")[0].lower() == "user1" and password == "user1-pass":
            return _identity("user1")
        raise DirectoryError("invalid_credentials")
    monkeypatch.setattr(LdapDirectory, "authenticate", authenticate)
    monkeypatch.setattr(LdapDirectory, "check_service_account", lambda self: None)
    monkeypatch.setattr(LdapService, "diagnose", lambda self, r: OK_STAGES)

    app = create_app(s, redis_factory=lambda x: FakeAsyncRedis(decode_responses=True), directory_factory=None, start_workers=False)
    c = run_app(app)
    try:
        # 1) вход по домену после обновления работает через прежнюю настройку, несмотря на сбой записи набора CA
        assert app.state.directory.configured
        assert "ca" in app.state.boot_errors
        assert c.post("/api/v1/auth/login", json={"login": "user1", "password": "user1-pass"}).status_code == 200
        assert c.post("/api/v1/auth/login", json={"login": "user1", "password": "wrong"}).status_code == 401
        # 2) диагностика не называет это «каталог не настроен»: каталог работает, а проблема с правами названа
        make_local_admin(c, must_change=False)
        login(c, "admin", ADMIN_PW)
        st = c.get("/api/v1/admin/ldap-legacy").json()
        assert st["active"] and st["needs_import"] and not st["migrated"]
        # 3) пока каталог CA недоступен на запись, перенос честно отказывает и называет причину; прежний вход при этом не страдает
        r = c.post("/api/v1/admin/ldap-legacy/import")
        assert r.status_code == 409 and "FileExistsError" in r.json()["detail"]["message"]
        assert c.post("/api/v1/auth/login", json={"login": "user1", "password": "user1-pass"}).status_code == 200
        # «Исправить автоматически» привело каталог в порядок → перенос выполняется, вход продолжает работать уже через подключение из базы
        (tmp_path / "ca").unlink()
        (tmp_path / "ca").mkdir()
        login(c, "admin", ADMIN_PW)
        assert c.post("/api/v1/admin/ldap-legacy/import").status_code == 200
        st = c.get("/api/v1/admin/ldap-legacy").json()
        assert st["migrated"] and not st["active"]
        rooms = [r["name"] for r in c.get("/api/v1/admin/rooms").json()]          # 4) данные прежней версии на месте
        assert "Комната из 0.2" in rooms
        c.cookies.clear()
        assert c.post("/api/v1/auth/login", json={"login": "user1", "password": "user1-pass"}).status_code == 200
    finally:
        c.__exit__(None, None, None)
