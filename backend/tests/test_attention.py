"""Центр внимания «Обзора»: состояния модулей в одном списке, переход к настройке, безопасное исправление, отказ источника — тоже пункт."""
from __future__ import annotations

import asyncio

from app.services import attention, media_checks

from .conftest import login, put_settings


def _get(c, refresh=True):
    r = c.get("/api/v1/admin/attention", params={"refresh": 1} if refresh else None)
    assert r.status_code == 200, r.text
    return r.json()


def test_only_admins_see_it_and_disabled_features_are_not_problems(client):
    login(client, "alice")
    assert client.get("/api/v1/admin/attention").status_code == 403
    login(client, "root")
    data = _get(client)
    assert set(data["counts"]) == {"critical", "warning", "info"} and data["checked_at"] > 0
    ids = {x["id"] for x in data["items"]}
    assert not any(i.startswith(("video.", "live.", "storage.")) for i in ids), "выключенная возможность — не проблема"
    assert [x["level"] for x in data["items"]] == sorted((x["level"] for x in data["items"]), key=attention.LEVELS.index)


def test_broadcast_secret_problem_leads_to_the_setting_with_a_confirmed_fix(client, monkeypatch):
    """Пример владельца: секрет подписи трансляций не записан из-за прав каталога — пункт на «Обзоре» с переходом к проверке и исправлением прав."""
    async def checks(st, db, purpose):
        if purpose != "live":
            return []
        return [media_checks._chk("livekit", "Сервер звонков (LiveKit)", "error", "Не отвечает."),
                media_checks._chk("secret", "Секрет подписи трансляций", "error", "Не записан: каталог трансляций недоступен для записи.",
                                  action={"kind": "repair", "repair": "data_dirs", "label": "Исправить права каталогов"}),
                media_checks._chk("space", "Свободное место", "ok", "Достаточно.")]
    monkeypatch.setattr(media_checks, "checks", checks)
    login(client, "root")
    put_settings(client, "live", enabled=True)
    items = {x["id"]: x for x in _get(client)["items"]}
    sec = items["live.secret"]
    assert sec["level"] == "warning" and sec["component"] == "Трансляции" and sec["tab"] == "live" and sec["anchor"] == "chk-secret"
    assert sec["impact"] and sec["action"] and sec["fix"]["kind"] == "repair" and sec["fix"]["repair"] == "data_dirs" and sec["fix"]["confirm"]
    assert "live.livekit" not in items and "live.space" not in items, "сервер звонков — пунктом «Службы», без повтора; исправное — не пункт"


def test_a_failing_or_hanging_source_is_reported_not_silent(client, monkeypatch):
    async def broken(app, db):
        raise RuntimeError("сбой")

    async def slow(app, db):
        await asyncio.sleep(5)
        return []
    monkeypatch.setattr(attention, "_SOURCES", [("Сломанный", broken), ("Медленный", slow)])
    monkeypatch.setattr(attention, "SOURCE_TIMEOUT_S", 0.2)
    login(client, "root")
    items = _get(client)["items"]
    assert {x["id"] for x in items} == {"source.Сломанный", "source.Медленный"}
    assert all(x["level"] == "info" and "не выполнена" in x["title"] for x in items)


def test_storage_that_cannot_be_written_is_a_warning_with_the_section(client, tmp_path):
    login(client, "root")
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x")
    put_settings(client, "storage", enabled=True, mode="local", local_path=str(blocked / "sub"))
    st = {x["id"]: x for x in _get(client)["items"]}["storage.storage"]
    assert st["level"] == "warning" and st["tab"] == "storage" and "недоступно" in st["title"]
