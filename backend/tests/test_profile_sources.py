"""Источники профиля: слияние по приоритетам, Bitrix24 (подставной портал), фон, отказоустойчивость, ручная аватарка. Без сети."""
from __future__ import annotations

import asyncio
import io
import json
import time

import httpx
import pytest
from sqlalchemy import select

from app.models import User
from app.profiles.bitrix import Bitrix24Provider, webhook_ok
from app.profiles.merge import DEFAULT_PRIORITY, avatar_source, effective, parse_priority, priorities_from
from app.services.settings import Bitrix24Settings

from .conftest import login, make_settings, put_settings, running_app

PORTAL = "https://portal.example.com"
HOOK = "https://portal.example.com/rest/7/s3cr3tTOKEN/"


def png_bytes(color=(10, 120, 200), size=(300, 300)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def portal(rows=None, *, photo: bytes | None = None, calls=None, fail=None, delay=0.0, state: dict | None = None):
    """Подставной портал: user.current / user.get / department.get / фото."""
    rows = rows if rows is not None else [{"ID": "42", "ACTIVE": "Y", "NAME": "Алиса", "LAST_NAME": "Крылова", "EMAIL": "alice@corp.test", "WORK_POSITION": "Системный архитектор",
                                          "UF_DEPARTMENT": [5], "WORK_PHONE": "+7 495 000-00-01", "PERSONAL_PHOTO": "/upload/main/alice.png"}]

    async def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req.url.path)
        if delay:
            await asyncio.sleep(delay)
        if fail is not None:
            raise fail
        p = req.url.path
        if p.endswith("/user.current.json"):
            return httpx.Response(200, json={"result": {"ID": "7", "NAME": "Сервис", "LAST_NAME": "Интеграция"}})
        if p.endswith("/user.get.json"):
            return httpx.Response(200, json={"result": rows, "total": len(rows)})
        if p.endswith("/department.get.json"):
            return httpx.Response(200, json={"result": [{"ID": "5", "NAME": "Служба информационных технологий"}]})
        if p.startswith("/upload/"):
            return httpx.Response(200, content=(state or {}).get("photo") or photo or png_bytes(), headers={"content-type": "image/png"})
        return httpx.Response(404, json={"error": "ERROR_METHOD_NOT_FOUND"})
    return httpx.MockTransport(handler)


def cfg(**over) -> Bitrix24Settings:
    return Bitrix24Settings(enabled=True, portal_url=PORTAL, webhook_url=HOOK, **over)


# ------------------------------------------------------------------------------------------------ слияние
def test_priorities_pick_first_non_empty_source_and_keep_missing_fields_out():
    src = {"ad": {"display_name": "Алиса Крылова", "email": "a@x.test", "title": "Инженер"}, "bitrix": {"title": "Архитектор", "department": "ИТ", "phone": "+7 1"}}
    eff = effective(src, DEFAULT_PRIORITY)
    assert eff == {"display_name": "Алиса Крылова", "email": "a@x.test", "title": "Архитектор", "department": "ИТ", "phone": "+7 1"}
    flipped = effective(src, {**DEFAULT_PRIORITY, "title": ["ad", "bitrix", "local"]})
    assert flipped["title"] == "Инженер"
    assert "department" not in effective({"ad": {}}, DEFAULT_PRIORITY), "нет данных — поле не возвращается, прежнее значение карточки остаётся"


def test_priority_text_is_parsed_defensively():
    assert parse_priority("bitrix; AD ,ad, чужой", ["local"]) == ["bitrix", "ad"]
    assert parse_priority("", ["ad", "local"]) == ["ad", "local"]
    assert priorities_from(None)["avatar"] == ["local", "bitrix", "ad"]
    with pytest.raises(ValueError):
        Bitrix24Settings(priority_title="bitrix, wrong")


def test_manual_avatar_is_not_replaced_unless_the_admin_puts_the_portal_first():
    assert avatar_source(has_avatar=True, current="manual", priority=["local", "bitrix", "ad"], bitrix_available=True) == "keep"
    assert avatar_source(has_avatar=True, current=None, priority=["local", "bitrix"], bitrix_available=True) == "keep", "прежнее фото без пометки — тоже ручное"
    assert avatar_source(has_avatar=True, current="manual", priority=["bitrix", "local"], bitrix_available=True) == "bitrix"
    assert avatar_source(has_avatar=False, current=None, priority=["local", "bitrix"], bitrix_available=True) == "bitrix"
    assert avatar_source(has_avatar=True, current="bitrix", priority=["local", "bitrix"], bitrix_available=True) == "bitrix", "фото с портала обновляется"
    assert avatar_source(has_avatar=False, current=None, priority=["bitrix"], bitrix_available=False) == "keep"


# ------------------------------------------------------------------------------------------------ клиент Bitrix24
def test_webhook_must_belong_to_the_portal_and_not_be_internal():
    assert webhook_ok(PORTAL, HOOK) is None
    assert "принадлежать" in webhook_ok(PORTAL, "https://evil.example.net/rest/7/x/")
    assert "/rest/" in webhook_ok(PORTAL, "https://portal.example.com/other/")
    assert webhook_ok("http://127.0.0.1", "http://127.0.0.1/rest/1/x/") == "Недопустимый адрес портала"
    assert webhook_ok("http://169.254.169.254", "http://169.254.169.254/rest/1/x/") == "Недопустимый адрес портала"


def test_enabled_settings_require_addresses_and_https():
    with pytest.raises(ValueError):
        Bitrix24Settings(enabled=True)
    with pytest.raises(ValueError):
        Bitrix24Settings(enabled=True, portal_url="http://portal.example.com", webhook_url="http://portal.example.com/rest/1/x/")
    Bitrix24Settings(enabled=True, portal_url="http://portal.example.com", webhook_url="http://portal.example.com/rest/1/x/", allow_http=True)


def test_provider_reads_position_department_phone_and_photo_from_the_same_host_only():
    calls: list[str] = []
    p = Bitrix24Provider(cfg(), HOOK, transport=portal(calls=calls))
    prof = asyncio.run(p.fetch(email="alice@corp.test", external_id=None))
    assert prof and prof.fields["title"] == "Системный архитектор" and prof.fields["department"] == "Служба информационных технологий"
    assert prof.fields["phone"] == "+7 495 000-00-01" and prof.external_id == "42" and prof.photo
    # фото с чужого узла не скачивается
    rows = [{"ID": "1", "ACTIVE": "Y", "EMAIL": "a@x.test", "PERSONAL_PHOTO": "https://evil.example.net/p.png"}]
    prof2 = asyncio.run(Bitrix24Provider(cfg(), HOOK, transport=portal(rows)).fetch(email="a@x.test", external_id=None))
    assert prof2 and prof2.photo is None


def test_ambiguous_or_missing_match_is_ignored_not_guessed():
    two = [{"ID": "1", "ACTIVE": "Y", "EMAIL": "a@x.test"}, {"ID": "2", "ACTIVE": "Y", "EMAIL": "a@x.test"}]
    assert asyncio.run(Bitrix24Provider(cfg(), HOOK, transport=portal(two)).fetch(email="a@x.test", external_id=None)) is None
    inactive = [{"ID": "1", "ACTIVE": "N", "EMAIL": "a@x.test"}]
    assert asyncio.run(Bitrix24Provider(cfg(), HOOK, transport=portal(inactive)).fetch(email="a@x.test", external_id=None)) is None
    assert asyncio.run(Bitrix24Provider(cfg(), HOOK, transport=portal([])).fetch(email="a@x.test", external_id=None)) is None


def test_errors_never_contain_the_webhook_secret():
    from app.profiles.base import ProviderError
    for exc in (httpx.ConnectError("boom " + HOOK), httpx.ReadTimeout("slow " + HOOK)):
        with pytest.raises(ProviderError) as e:
            asyncio.run(Bitrix24Provider(cfg(), HOOK, transport=portal(fail=exc)).check())
        assert "s3cr3tTOKEN" not in str(e.value) and "portal.example.com" not in str(e.value)


# ------------------------------------------------------------------------------------------------ сквозные: вход, фон, сбои
def enable(c, **over):
    put_settings(c, "bitrix24", enabled=True, portal_url=PORTAL, webhook_url=HOOK, **over)


def user_row(c, sam="alice") -> User:
    async def get():
        async with c.app_obj.state.session_maker() as db:
            return (await db.execute(select(User).where(User.sam_account_name == sam))).scalars().one()
    return c.portal.call(get)


def drain(c):
    c.portal.call(c.app_obj.state.enrichment.drain)


def test_disabled_by_default_login_never_touches_the_portal(tmp_path, directory):
    calls: list[str] = []
    with running_app(make_settings(tmp_path), directory, transports={"bitrix24": portal(calls=calls)}) as c:
        login(c, "alice")
        drain(c)
        assert calls == [] and c.get("/api/v1/profile").json()["title"] is None


def test_login_fills_title_department_phone_and_avatar_from_the_portal_in_background(tmp_path, directory):
    calls: list[str] = []
    with running_app(make_settings(tmp_path), directory, transports={"bitrix24": portal(calls=calls)}) as c:
        enable(c)
        login(c, "alice")
        drain(c)
        p = c.get("/api/v1/profile").json()
        assert p["title"] == "Системный архитектор" and p["department"] == "Служба информационных технологий" and p["phone"] == "+7 495 000-00-01"
        assert p["avatar_url"] and p["email"] == "alice@corp.test", "e-mail остаётся из каталога"
        u = user_row(c)
        assert u.avatar_source == "bitrix" and u.external_ids == {"bitrix": "42"} and "bitrix" in u.profile_sources and "ad" in u.profile_sources
        n = len(calls)
        login(c, "alice")                      # второй вход в пределах срока: портал не опрашивается
        drain(c)
        assert len(calls) == n
        card = c.get(p["avatar_url"])
        assert card.status_code == 200 and card.headers["content-type"] == "image/webp"


def test_portal_failure_does_not_break_login_and_keeps_previous_values(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory, transports={"bitrix24": portal(fail=httpx.ConnectError("down"))}) as c:
        enable(c)
        t0 = time.monotonic()
        r = c.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"})
        assert r.status_code == 200 and time.monotonic() - t0 < 3
        drain(c)
        assert c.get("/api/v1/profile").json()["title"] is None
        assert user_row(c).profile_sources["bitrix"].get("failed_at"), "сбой запомнен: повторы после паузы, а не при каждом входе"


def test_slow_portal_does_not_delay_login(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory, transports={"bitrix24": portal(delay=2.0)}) as c:
        enable(c, timeout=5)
        t0 = time.monotonic()
        assert c.post("/api/v1/auth/login", json={"login": "alice", "password": "alice-pass"}).status_code == 200
        assert time.monotonic() - t0 < 1.5, "вход не ждёт портал"
        drain(c)


def test_manual_avatar_survives_the_portal_photo(tmp_path, directory):
    state: dict = {}
    with running_app(make_settings(tmp_path), directory, transports={"bitrix24": portal(state=state)}) as c:
        enable(c)
        login(c, "alice")
        drain(c)
        state["photo"] = png_bytes((5, 200, 5))            # на портале появилось другое фото
        r = c.put("/api/v1/profile/avatar", content=png_bytes((200, 30, 30)), headers={"content-type": "application/octet-stream"})
        assert r.status_code == 200
        before = user_row(c)
        assert before.avatar_source == "manual"
        async def again():
            await c.app_obj.state.enrichment.enrich(before.id, force=True)
        c.portal.call(again)
        after = user_row(c)
        assert after.avatar_source == "manual" and after.avatar_updated_at == before.avatar_updated_at, "ручное фото не заменено"
        assert after.title == "Системный архитектор", "а должность по-прежнему обновляется"
        assert after.profile_sources["bitrix"]["photo_sha"] != before.profile_sources["bitrix"]["photo_sha"], "новое фото портал отдал, но не применили"


def test_admin_checks_and_sync_work_and_hide_the_secret(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory, transports={"bitrix24": portal()}) as c:
        enable(c)
        got = c.get("/api/v1/admin/settings/bitrix24").json()
        assert "s3cr3tTOKEN" not in json.dumps(got)
        assert c.post("/api/v1/admin/settings/bitrix24/test").json()["ok"] is True
        r = c.post("/api/v1/admin/bitrix24/check").json()
        assert r["ok"] and "Соединение установлено" in r["message"] and "s3cr3tTOKEN" not in json.dumps(r)
        look = c.post("/api/v1/admin/bitrix24/lookup", json={"email": "alice@corp.test"}).json()
        assert look["ok"] and look["fields"]["title"] == "Системный архитектор" and look["has_photo"] is True
        assert c.post("/api/v1/admin/bitrix24/lookup", json={"email": "не e-mail"}).status_code == 422
        login(c, "alice")                                   # появился пользователь с e-mail
        login(c, "root")
        s = c.post("/api/v1/admin/bitrix24/sync").json()
        assert s["status"] == "done" and s["processed"] >= 1
        login(c, "alice")
        assert c.post("/api/v1/admin/bitrix24/sync").status_code == 403, "только администратор"
