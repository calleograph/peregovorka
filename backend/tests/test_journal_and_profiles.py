"""Журнал событий (фильтры, подгрузка, удаление, выгрузка, срок хранения, внешнее хранение), API-профили, протоколы без обезличивания."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.models import EventLog, utcnow

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_access_and_protocols import LLM_OK, end_by_alice, meeting_with_two
from .test_admin_features import _drain

J = "/api/v1/admin/journal"


def flush(c) -> None:
    c.portal.call(c.app_obj.state.journal.flush)


def events(c, **params) -> list[dict]:
    login(c, "root")
    flush(c)
    r = c.get(J, params={"limit": 500, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def seed(c, n: int, **kw) -> None:
    j = c.app_obj.state.journal
    for i in range(n):
        j.emit(kw.get("category", "client"), kw.get("event", "seed"), level=kw.get("level", "info"), user=kw.get("user", "u"),
               message=f"{kw.get('message', 'msg')} {i}")
    flush(c)


# ============================================================================ протоколы и обезличивание
def _app(tmp_path, directory):
    calls = {"llm": [], "anon": [], "llm_hosts": []}

    def llm(req: httpx.Request) -> httpx.Response:
        calls["llm"].append(json.loads(req.content))
        calls["llm_hosts"].append((req.url.host, req.headers.get("authorization")))
        return LLM_OK(req)

    from .test_admin_features import anon_ok

    def anon(req: httpx.Request) -> httpx.Response:
        if req.method != "POST":
            return httpx.Response(200, json={"openapi": "3.0.0", "paths": {}})
        calls["anon"].append(req.url.path)
        return anon_ok(req)

    return calls, running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon), "llm": httpx.MockTransport(llm)}), None


def _make_protocol(c, mid, who="bob"):
    login(c, who)
    r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "кратко"})
    assert r.status_code == 202, r.text
    _drain(c)
    return c.get(f"/api/v1/meetings/{mid}/protocols/{r.json()['protocol_id']}").json()


def test_anonymizer_off_does_not_block_protocols(tmp_path, directory):
    calls, gen, _ = _app(tmp_path, directory)
    with gen as c:
        put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")   # обезличивание не настроено вообще
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "bob")
        plan = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").json()["plan"]
        assert plan["llm_ready"] and plan["anonymize"] is False
        got = _make_protocol(c, mid)
        assert got["status"] == "ready", got
        assert calls["anon"] == [], "обезличиватель не вызывался"
        assert "Принято решение по бюджету" in json.dumps(calls["llm"][-1], ensure_ascii=False)
        assert c.get(f"/api/v1/meetings/{mid}/protocols/{got['id']}").json()["status"] == "ready"


def test_room_can_turn_anonymization_off_and_on(tmp_path, directory):
    calls, gen, _ = _app(tmp_path, directory)
    with gen as c:
        put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
        put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")
        room, mid = meeting_with_two(c, anonymize_mode="off")
        assert room["anonymize_mode"] == "off"
        end_by_alice(c, mid)
        got = _make_protocol(c, mid)
        assert got["status"] == "ready" and calls["anon"] == []
        # включено «всегда» в другой комнате: обезличивание вызывается
        _r2, mid2 = meeting_with_two(c, anonymize_mode="on")
        end_by_alice(c, mid2)
        assert _make_protocol(c, mid2)["status"] == "ready" and calls["anon"], "обезличиватель вызывался"
        # и «наследовать» при включённом глобально — тоже вызывается
        calls["anon"].clear()
        _r3, mid3 = meeting_with_two(c)
        end_by_alice(c, mid3)
        assert _make_protocol(c, mid3)["status"] == "ready" and calls["anon"]


def test_room_requiring_anonymization_fails_closed_when_it_is_not_configured(tmp_path, directory):
    calls, gen, _ = _app(tmp_path, directory)
    with gen as c:
        put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")
        _room, mid = meeting_with_two(c, anonymize_mode="on")
        end_by_alice(c, mid)
        login(c, "bob")
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol"})
        assert r.status_code == 409 and "обезличивание" in r.json()["detail"].lower()
        assert calls["llm"] == []


def test_failing_anonymizer_still_blocks_when_it_is_enabled(tmp_path, directory):
    calls = {"llm": 0}

    def llm(req):
        calls["llm"] += 1
        return LLM_OK(req)

    gen = running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(lambda r: httpx.Response(500, text="x")),
                                                                     "llm": httpx.MockTransport(llm)})
    with gen as c:
        put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
        put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        got = _make_protocol(c, mid)
        assert got["status"] == "failed" and calls["llm"] == 0, "включённое обезличивание — fail closed"


# ===================================================================================== API-профили
def test_profiles_default_and_per_room_selection(tmp_path, directory):
    calls, gen, _ = _app(tmp_path, directory)
    with gen as c:
        put_settings(c, "llm", enabled=True, type="openai_compatible", base_url="https://llm-main.test/v1", model="main-model", api_key="main-key")
        login(c, "root")
        a = c.post("/api/v1/admin/api-profiles", json={"kind": "llm", "name": "Резервный", "secret": "SECRET-A", "make_default": True,
                                                       "config": {"type": "openai_compatible", "base_url": "https://llm-a.test/v1", "model": "a-model"}})
        assert a.status_code == 201, a.text
        b = c.post("/api/v1/admin/api-profiles", json={"kind": "llm", "name": "Для руководства", "secret": "SECRET-B",
                                                       "config": {"type": "openai_compatible", "base_url": "https://llm-b.test/v1", "model": "b-model"}}).json()
        listing = c.get("/api/v1/admin/api-profiles", params={"kind": "llm"}).json()
        assert [p["name"] for p in listing][0].startswith("Основной") and {p["name"] for p in listing} >= {"Резервный", "Для руководства"}
        assert "SECRET" not in json.dumps(listing) and all("secret_set" in p for p in listing)
        assert [p["name"] for p in listing if p["is_default"]] == ["Резервный"]

        room1, mid1 = meeting_with_two(c)                                   # общий профиль по умолчанию
        room2, mid2 = meeting_with_two(c, llm_profile_id=b["id"])          # своя привязка
        end_by_alice(c, mid1)
        end_by_alice(c, mid2)
        assert _make_protocol(c, mid1)["status"] == "ready"
        assert calls["llm_hosts"][-1] == ("llm-a.test", "Bearer SECRET-A")
        assert _make_protocol(c, mid2)["status"] == "ready"
        assert calls["llm_hosts"][-1] == ("llm-b.test", "Bearer SECRET-B")

        # вернуть «основной» по умолчанию
        login(c, "root")
        assert c.put("/api/v1/admin/api-profiles/default", json={"kind": "llm", "profile_id": "main"}).status_code == 200
        assert [p["name"] for p in c.get("/api/v1/admin/api-profiles", params={"kind": "llm"}).json() if p["is_default"]][0].startswith("Основной")
        # удаление профиля: комната возвращается к «по умолчанию», ссылка не ломает протоколы
        assert c.delete(f"/api/v1/admin/api-profiles/{b['id']}").status_code == 204
        assert c.get(f"/api/v1/admin/rooms/{room2['id']}").json()["llm_profile_id"] is None
        # основной нельзя ни удалить, ни изменить через профили
        assert c.delete("/api/v1/admin/api-profiles/main").status_code == 409
        assert c.patch("/api/v1/admin/api-profiles/main", json={"name": "x"}).status_code == 409
        actions = [x["action"] for x in c.get("/api/v1/admin/audit").json()]
        assert {"api_profile.create", "api_profile.default", "api_profile.delete"} <= set(actions)


def test_profile_validation_and_secret_update(tmp_path, directory):
    _calls, gen, _ = _app(tmp_path, directory)
    with gen as c:
        login(c, "root")
        bad = c.post("/api/v1/admin/api-profiles", json={"kind": "llm", "name": "x", "config": {"type": "openai_compatible", "model": "m"}})
        assert bad.status_code == 422                                          # нет base_url
        assert c.post("/api/v1/admin/api-profiles", json={"kind": "other", "name": "x"}).status_code == 422
        ok = c.post("/api/v1/admin/api-profiles", json={"kind": "anonymizer", "name": "DocClean 2", "secret": "tok",
                                                        "config": {"profile": "docclean", "base_url": "https://anon2.test"}})
        assert ok.status_code == 201 and ok.json()["secret_set"] is True and "tok" not in ok.text
        pid = ok.json()["id"]
        assert c.post("/api/v1/admin/api-profiles", json={"kind": "anonymizer", "name": "DocClean 2", "config": {"base_url": "https://z.test"}}).status_code == 422
        r = c.patch(f"/api/v1/admin/api-profiles/{pid}", json={"secret": ""})
        assert r.status_code == 200 and r.json()["secret_set"] is False
        r = c.patch(f"/api/v1/admin/api-profiles/{pid}", json={"name": "DocClean резерв", "config": {"timeout": 30}})
        assert r.json()["name"] == "DocClean резерв" and r.json()["config"]["timeout"] == 30
        t = c.post(f"/api/v1/admin/api-profiles/{pid}/test", params={"kind": "anonymizer"})
        assert t.status_code == 200 and "ok" in t.json()
        login(c, "alice")
        assert c.get("/api/v1/admin/api-profiles", params={"kind": "llm"}).status_code == 403


# ============================================================================================= журнал
def test_login_and_room_events_are_journaled_without_password(client):
    login(client, "alice")
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"login": "alice", "password": "SuperSecret!1"}, headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0.0.0 Safari/537.36"})
    evs = events(client)
    names = [e["event"] for e in evs]
    assert {"login_ok", "login_failed", "logout", "app_started"} <= set(names)
    bad = next(e for e in evs if e["event"] == "login_failed")
    assert bad["category"] == "auth" and bad["level"] == "warn" and bad["user"] == "alice" and "Chrome 130" in (bad["client"] or "") and "Windows" in bad["client"]
    assert "SuperSecret" not in json.dumps(evs, ensure_ascii=False)


def test_client_events_reach_journal_with_device_data(client):
    login(client, "bob")
    r = client.post("/api/v1/client/events", json={"event": "mic_busy", "reason": "NotReadableError", "detail": "Could not start audio source",
                                                   "room": "ИТ-1", "data": {"inputs": 2, "exclusive_hint": True, "labels": ["Микрофон (Realtek)"], "nested": {"x": 1}}})
    assert r.status_code == 204
    client.post("/api/v1/client/events", json={"event": "ice_failed", "reason": "pc_connection", "detail": "could not establish pc connection",
                                               "data": {"effective_type": "4g", "rtt": 80, "candidates": ["host", "srflx"]}})
    ev = events(client, **{"filters": json.dumps([{"field": "category", "op": "eq", "value": "device"}])})
    assert len(ev) == 1 and ev[0]["event"] == "mic_busy" and ev[0]["room"] == "ИТ-1" and ev[0]["user"] == "bob"
    assert ev[0]["data"]["inputs"] == 2 and "nested" not in ev[0]["data"]
    net = events(client, **{"filters": json.dumps([{"field": "level", "op": "eq", "value": "error"}])})
    assert [e["event"] for e in net] == ["ice_failed"]


def test_filters_operators_search_and_range(client):
    seed(client, 3, event="alpha", user="ivan", level="info", category="client", message="первый")
    seed(client, 2, event="beta", user="petr", level="error", category="network", message="сбой сети")
    seed(client, 1, event="gamma", user="ivan", level="warn", category="device", message="микрофон занят")

    def q(filters=None, **p):
        params = dict(p)
        if filters is not None:
            params["filters"] = json.dumps(filters)
        return [e for e in events(client, **params) if e["event"] in ("alpha", "beta", "gamma")]

    assert len(q([{"field": "user", "op": "eq", "value": "ivan"}])) == 4
    assert {e["user"] for e in q([{"field": "user", "op": "ne", "value": "ivan"}])} == {"petr"}
    assert len(q([{"field": "message", "op": "contains", "value": "СЕТИ"}])) == 2, "без учёта регистра"
    assert len(q([{"field": "message", "op": "not_contains", "value": "сети"}])) == 4
    assert len(q([{"field": "event", "op": "starts", "value": "ga"}])) == 1
    assert len(q([{"field": "level", "op": "gte", "value": "warn"}])) == 3
    assert len(q([{"field": "category", "op": "eq", "value": ["network", "device"]}])) == 3
    assert len(q([{"field": "user", "op": "eq", "value": "ivan"}, {"field": "level", "op": "eq", "value": "warn"}])) == 1, "условия объединяются через И"
    assert len(q(q="микрофон")) == 1
    assert len(q(range="1h")) == 6
    # недопустимые поля и операции отклоняются
    login(client, "root")
    for bad in ([{"field": "data", "op": "eq", "value": "x"}], [{"field": "user", "op": "regex", "value": "x"}]):
        assert client.get(J, params={"filters": json.dumps(bad)}).status_code == 422
    assert client.get(J, params={"filters": json.dumps([{"field": "user", "op": "gte", "value": "x"}])}).status_code == 422
    assert client.get(J, params={"filters": "not json"}).status_code == 422
    # LIKE-спецсимволы в значении не работают как маски
    seed(client, 1, event="alpha", message="100% готово")
    assert len(q([{"field": "message", "op": "contains", "value": "%"}])) == 1


def test_infinite_scroll_cursor_visits_each_event_once(client):
    seed(client, 25, event="scroll")
    login(client, "root")
    seen, cursor = [], None
    for _ in range(10):
        params = {"limit": 10, "filters": json.dumps([{"field": "event", "op": "eq", "value": "scroll"}])}
        if cursor:
            params["before_id"] = cursor
        page = client.get(J, params=params).json()
        seen += [e["id"] for e in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 25 and len(set(seen)) == 25 and seen == sorted(seen, reverse=True)


def test_delete_selected_and_all_matching_are_audited(client):
    seed(client, 6, event="del")
    seed(client, 2, event="keep")
    ids = [e["id"] for e in events(client, filters=json.dumps([{"field": "event", "op": "eq", "value": "del"}]))]
    r = client.post(f"{J}/delete", json={"ids": ids[:2]})
    assert r.json() == {"deleted": 2}
    r = client.post(f"{J}/delete", json={"all_matching": True, "filters": [{"field": "event", "op": "eq", "value": "del"}]})
    assert r.json() == {"deleted": 4}
    assert len([e for e in events(client) if e["event"] == "keep"]) == 2
    assert client.post(f"{J}/delete", json={}).status_code == 422
    assert client.post(f"{J}/delete", json={"ids": []}).status_code == 422
    assert client.post(f"{J}/delete", json={"ids": ["a"]}).status_code == 422
    assert "journal.delete" in [a["action"] for a in client.get("/api/v1/admin/audit").json()]
    login(client, "alice")
    assert client.post(f"{J}/delete", json={"ids": [1]}).status_code == 403
    assert client.get(J).status_code == 403


def test_export_archive_contains_csv_ndjson_and_audit(client):
    seed(client, 3, event="exp", message="строка для выгрузки")
    login(client, "root")
    for rng in ("24h", "30d", "all"):
        r = client.get(f"{J}/export", params={"range": rng})
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert f"journal-{rng}" in r.headers["content-disposition"]
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert set(z.namelist()) == {"journal.csv", "journal.ndjson", "audit.csv"}
        csv_text = z.read("journal.csv").decode("utf-8")
        assert csv_text.startswith("﻿") and "строка для выгрузки" in csv_text and "Время (UTC)" in csv_text
        lines = [json.loads(x) for x in z.read("journal.ndjson").decode("utf-8").splitlines()]
        assert any(x["event"] == "exp" for x in lines)
    assert client.get(f"{J}/export", params={"range": "5y"}).status_code == 422
    login(client, "alice")
    assert client.get(f"{J}/export").status_code == 403


def test_export_24h_excludes_older_events(client):
    seed(client, 1, event="fresh")
    old = utcnow() - timedelta(days=3)

    async def add():
        async with client.app_obj.state.session_maker() as db:
            db.add(EventLog(at=old, level="info", category="client", event="stale", message="старое"))
            await db.commit()
    client.portal.call(add)
    login(client, "root")
    z = zipfile.ZipFile(io.BytesIO(client.get(f"{J}/export", params={"range": "24h"}).content))
    text = z.read("journal.ndjson").decode("utf-8")
    assert "fresh" in text and "stale" not in text
    z = zipfile.ZipFile(io.BytesIO(client.get(f"{J}/export", params={"range": "30d"}).content))
    assert "stale" in z.read("journal.ndjson").decode("utf-8")


def test_stats_and_facets(client):
    seed(client, 2, event="s1", level="error", category="network", user="ivan")
    login(client, "root")
    st = client.get(f"{J}/stats").json()
    assert st["total"] >= 2 and st["errors_24h"] >= 2 and st["size_bytes"] > 0 and st["retention_days"] == 30 and st["keep_local"] is True
    assert st["by_category_24h"]["network"] >= 2 and st["external"]["enabled"] is False and "audit" in st
    f = client.get(f"{J}/facets").json()
    assert "ivan" in f["users"] and "s1" in f["events"] and "network" in f["categories"] and f["levels"] == ["debug", "info", "warn", "error"]


def test_retention_removes_only_expired_events_and_is_configurable(client):
    async def add():
        async with client.app_obj.state.session_maker() as db:
            db.add_all([EventLog(at=utcnow() - timedelta(days=31), level="info", category="client", event="old31", message="x"),
                        EventLog(at=utcnow() - timedelta(days=29), level="info", category="client", event="old29", message="x")])
            await db.commit()
    client.portal.call(add)
    login(client, "root")
    r = client.post(f"{J}/purge-now")
    assert r.status_code == 200 and r.json()["db"] == 1
    names = {e["event"] for e in events(client)}
    assert "old29" in names and "old31" not in names
    put_settings(client, "journal", retention_days=7)
    client.portal.call(add)
    assert client.post(f"{J}/purge-now").json()["db"] == 3, "две записи 29-дневной давности и одна 31-дневной — все старше 7 дней"
    assert "old29" not in {e["event"] for e in events(client)}, "срок хранения меняется в админке"
    assert client.put("/api/v1/admin/settings/journal", json={"retention_days": 0}).status_code == 422


def test_local_storage_can_be_turned_off_and_external_copy_is_written(tmp_path, client):
    ext = Path(client.app_obj.state.settings.data_dir) / "extlogs"
    put_settings(client, "journal", keep_local=False, enabled=True, mode="local", local_path=str(ext), external_flush_seconds=5)
    client.portal.call(lambda: client.app_obj.state.journal.config(True))
    seed(client, 3, event="ext_only", message="только во внешнее хранилище")
    client.portal.call(client.app_obj.state.journal.flush_external)
    assert [e for e in events(client) if e["event"] == "ext_only"] == [], "в базе сервера события не хранятся"
    files = list(ext.rglob("*.ndjson"))
    assert files and "только во внешнее хранилище" in files[0].read_text(encoding="utf-8")
    assert files[0].parent.parent.name == "journal" and len(files[0].parent.name) == 10
    # внешняя очистка по сроку: каталог старого дня удаляется, свежий остаётся
    old_day = ext / "journal" / "2000-01-01"
    old_day.mkdir(parents=True)
    (old_day / "x.ndjson").write_text("{}", encoding="utf-8")
    stats = client.portal.call(client.app_obj.state.journal.purge_expired)
    assert stats["external_days"] == 1 and not old_day.exists() and files[0].exists()
    # полностью выключено: журнал не ведётся нигде
    put_settings(client, "journal", enabled=False)
    client.portal.call(lambda: client.app_obj.state.journal.config(True))
    seed(client, 2, event="nowhere")
    assert [e for e in events(client) if e["event"] == "nowhere"] == []


def test_min_level_filters_noise(client):
    put_settings(client, "journal", min_level="warn")
    client.portal.call(lambda: client.app_obj.state.journal.config(True))
    seed(client, 1, event="quiet", level="info")
    seed(client, 1, event="loud", level="error")
    names = {e["event"] for e in events(client)}
    assert "loud" in names and "quiet" not in names


def test_journal_never_stores_secrets(client):
    j = client.app_obj.state.journal
    j.emit("admin", "x", message="password=Hunter2 token: abc", data={"api_key": "K-123", "note": "ok", "password": "p"})
    flush(client)
    dump = json.dumps(events(client), ensure_ascii=False)
    assert "Hunter2" not in dump and "K-123" not in dump and '"p"' not in dump and "ok" in dump


def test_room_form_fields_roundtrip(client):
    room = make_room(client, anonymize_mode="off")
    r = client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"anonymize_mode": "on"})
    assert r.status_code == 200 and r.json()["anonymize_mode"] == "on"
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"anonymize_mode": "maybe"}).status_code == 422
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"llm_profile_id": None}).status_code == 200
    # неизвестный профиль у комнаты не ломает работу: используется профиль по умолчанию
    import uuid as _u
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"llm_profile_id": str(_u.uuid4())}).status_code == 200


@pytest.mark.parametrize("ua,expect", [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0", "Edge 131 · Windows 10/11"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0", "Firefox 130 · Linux"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1", "Safari 17 · iOS 17"),
    (None, None),
])
def test_parse_client(ua, expect):
    from app.services.journal import parse_client

    assert parse_client(ua) == expect


# =============================================================================== защита (быстрый аудит)
def test_client_events_are_rate_limited_per_user(client):
    login(client, "bob")
    codes = [client.post("/api/v1/client/events", json={"event": "network_info", "data": {"i": i}}).status_code for i in range(250)]
    assert codes.count(204) == 240 and codes.count(429) == 10
    login(client, "alice")  # другой пользователь не страдает
    assert client.post("/api/v1/client/events", json={"event": "network_info"}).status_code == 204


def test_csv_export_neutralizes_spreadsheet_formulas(client):
    j = client.app_obj.state.journal
    j.emit("client", "x", user="=cmd|' /C calc'!A0", message="+1+1", data=None)
    j.emit("client", "y", user="@SUM(A1)", message="-2+3")
    flush(client)
    login(client, "root")
    z = zipfile.ZipFile(io.BytesIO(client.get(f"{J}/export", params={"range": "24h"}).content))
    text = z.read("journal.csv").decode("utf-8")
    assert "'=cmd" in text and "'+1+1" in text and "'@SUM" in text and "'-2+3" in text
    assert ";=cmd" not in text and ";+1+1" not in text


def test_password_typed_into_login_field_is_not_stored_in_journal(client):
    r = client.post("/api/v1/auth/login", json={"login": "My$ecret Pass!word#1", "password": "x"})
    assert r.status_code == 401
    dump = json.dumps(events(client), ensure_ascii=False)
    assert "ecret Pass" not in dump and "некорректный формат логина" in dump


def test_password_is_not_filtered_by_characters(client, directory):
    """Любые символы в пароле допустимы (звёздочка, слэши, скобки, кавычки): пароль уходит в AD как значение, а не как часть фильтра."""
    for i, pw in enumerate(["p*ss", "back\slash", "a)(b", "q'\"x", "100%_$", "ключ #1 ёЁ", "a" * 200]):
        directory.add(f"pwuser{i}", pw)
        r = client.post("/api/v1/auth/login", json={"login": f"pwuser{i}", "password": pw})
        assert r.status_code == 200, (pw, r.text)
        client.cookies.clear()
    assert client.post("/api/v1/auth/login", json={"login": "pwuser0", "password": "wrong*"}).status_code == 401


def test_api_responses_are_not_cacheable(client):
    login(client, "alice")
    r = client.get("/api/v1/auth/me")
    assert r.headers.get("cache-control") == "no-store" and r.headers.get("x-content-type-options") == "nosniff"


def test_audit_list_supports_filters_and_offset(client):
    make_room(client)
    put_settings(client, "general", timezone="UTC")
    login(client, "root")
    allrows = client.get("/api/v1/admin/audit", params={"limit": 500}).json()
    assert {"room.create", "settings.general.update"} <= {r["action"] for r in allrows}
    assert {r["action"] for r in client.get("/api/v1/admin/audit", params={"action": "room."}).json()} == {"room.create"}
    assert client.get("/api/v1/admin/audit", params={"actor": "нет такого"}).json() == []
    assert len(client.get("/api/v1/admin/audit", params={"limit": 1, "offset": 1}).json()) == 1
    assert client.get("/api/v1/admin/audit", params={"q": "100%"}).json() == [], "спецсимволы LIKE не работают как маска"
