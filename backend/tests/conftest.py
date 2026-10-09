from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass, field

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient

from app.auth.directory import DirectoryError, DirectoryIdentity
from app.config import Settings
from app.main import create_app
from app.models import Base

ADMIN_GROUP = "cn=vm-admins,ou=groups,dc=corp,dc=test"
STAFF_GROUP = "cn=staff,ou=groups,dc=corp,dc=test"
OTHER_GROUP = "cn=other,ou=groups,dc=corp,dc=test"


@dataclass
class FakeDirectory:
    """Подмена AD: LDAP в unit-тестах не используется."""

    users: dict[str, tuple[str, DirectoryIdentity]] = field(default_factory=dict)
    calls: int = 0
    fail_with: str | None = None

    def add(self, login: str, password: str, *, groups=(STAFF_GROUP,), name: str | None = None, guid: str | None = None) -> DirectoryIdentity:
        ident = DirectoryIdentity(
            ad_guid=guid or str(uuid.uuid4()), dn=f"cn={login},ou=users,dc=corp,dc=test", sam_account_name=login,
            upn=f"{login}@corp.test", display_name=name or login.title(), email=f"{login}@corp.test",
            groups=frozenset(g.lower() for g in groups))
        self.users[login.lower()] = (password, ident)
        return ident

    def authenticate(self, login: str, password: str) -> DirectoryIdentity:
        self.calls += 1
        if self.fail_with:
            raise DirectoryError(self.fail_with)
        rec = self.users.get(login.lower())
        if rec is None:
            raise DirectoryError("user_not_found")
        if rec[0] != password:
            raise DirectoryError("invalid_credentials")
        return rec[1]

    def lookup(self, login: str) -> DirectoryIdentity:
        rec = self.users.get(login.split("@")[0].split("\\")[-1].lower())
        if rec is None:
            raise DirectoryError("user_not_found")
        return rec[1]

    def check_service_account(self) -> None:
        return None

    def members(self, group_dn: str, limit: int = 300) -> list[dict]:
        return [{"name": i.display_name, "sam": i.sam_account_name, "email": i.email or ""} for _, i in self.users.values() if group_dn.lower() in i.groups][:limit]

    def search(self, kind: str, query: str, limit: int = 20) -> list[dict]:
        q = query.lower()
        if kind == "group":
            groups = {g for _, i in self.users.values() for g in i.groups}
            return [{"kind": "group", "ref": g, "name": g.split(",")[0][3:], "description": ""} for g in sorted(groups) if q in g][:limit]
        return [{"kind": "user", "ref": i.ad_guid, "name": i.display_name, "sam": i.sam_account_name, "email": i.email or ""}
                for _, i in self.users.values() if q in i.sam_account_name.lower() or q in i.display_name.lower()][:limit]


def make_settings(tmp_path, **over) -> Settings:
    base = dict(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        redis_url="redis://unused",
        app_public_url="http://testserver",
        cookie_secure=False,
        livekit_api_key="devkey",
        livekit_api_secret="s" * 40,
        livekit_public_url="wss://meet.test/livekit",
        livekit_internal_url="ws://127.0.0.1:1",  # заведомо недоступен
        app_master_key=base64.b64encode(os.urandom(32)).decode(),
        internal_api_token="internal-test-token",
        ldap_admin_group_dn=ADMIN_GROUP,
        log_level="WARNING",
        data_dir=str(tmp_path),
        meeting_end_grace_seconds=0,
        segment_consumer_block_ms=20,  # fakeredis блокирует цикл на время block — в тестах короткое
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def directory() -> FakeDirectory:
    d = FakeDirectory()
    d.add("alice", "alice-pass", name="Alice A")
    d.add("bob", "bob-pass", name="Bob B")
    d.add("carol", "carol-pass", groups=(OTHER_GROUP,), name="Carol C")
    d.add("root", "root-pass", groups=(ADMIN_GROUP,), name="Root Admin")
    return d


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings, directory):
    app = create_app(
        settings,
        redis_factory=lambda s: FakeAsyncRedis(decode_responses=True),
        directory_factory=lambda s: directory,
        start_workers=False,
    )
    with TestClient(app, base_url="http://testserver") as c:
        async def _create():
            async with app.state.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        c.portal.call(_create)
        c.app_obj = app  # type: ignore[attr-defined]
        yield c


def login(client: TestClient, user: str, password: str | None = None) -> str:
    """Входит под пользователем (сбрасывая прежнюю сессию); возвращает CSRF-токен и ставит заголовок."""
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    r = client.post("/api/v1/auth/login", json={"login": user, "password": password or f"{user}-pass"})
    assert r.status_code == 200, r.text
    token = r.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = token
    return token


def make_room(client: TestClient, admin_login: str = "root", **over) -> dict:
    login(client, admin_login)
    payload = {"slug": f"room-{uuid.uuid4().hex[:8]}", "name": "Переговорка", "acl": [
        {"subject_type": "group", "subject_ref": STAFF_GROUP}]}
    payload.update(over)
    r = client.post("/api/v1/admin/rooms", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def client_with_workers(settings, directory):
    """Приложение с фоновыми воркерами (приём сегментов и reaper) — для сквозных проверок."""
    app = create_app(
        settings,
        redis_factory=lambda s: FakeAsyncRedis(decode_responses=True),
        directory_factory=lambda s: directory,
        start_workers=True,
    )
    with TestClient(app, base_url="http://testserver") as c:
        async def _create():
            async with app.state.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        c.portal.call(_create)
        c.app_obj = app  # type: ignore[attr-defined]
        yield c


from contextlib import contextmanager


@contextmanager
def running_app(settings, directory, *, transports=None, workers=False):
    """Приложение для сквозных проверок; transports — подставные httpx-транспорты для обезличивателя/LLM."""
    app = create_app(settings, redis_factory=lambda s: FakeAsyncRedis(decode_responses=True),
                     directory_factory=lambda s: directory, start_workers=workers)
    app.state.test_transports = transports or {}
    with TestClient(app, base_url="http://testserver") as c:
        async def _create():
            async with app.state.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        c.portal.call(_create)
        c.app_obj = app  # type: ignore[attr-defined]
        yield c


def put_settings(client, group: str, **values):
    login(client, "root")
    r = client.put(f"/api/v1/admin/settings/{group}", json=values)
    assert r.status_code == 200, r.text
    return r.json()
