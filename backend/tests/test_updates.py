"""Обновление из веб-интерфейса: обмен с исполнителем (каталог updater), журнал обновления, версии компонентов."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import pytest

from app.services import updates as upd

from .conftest import login, make_settings, running_app

U = "/api/v1/admin/updates"
REPO = Path(__file__).resolve().parents[2]


def chan(client) -> Path:
    d = Path(client.app_obj.state.settings.data_dir) / "updater"
    d.mkdir(exist_ok=True)
    return d


def heartbeat(d: Path, **over):
    st = {"ts": time.time(), "pid": 1, "state": "idle", "request_id": "", "step_no": 0, "step_total": 16, "step_name": "", "started_at": 0,
          "finished_at": 0, "exit_code": None, "result": "", "by": "", "project": "pg-test"}
    st.update(over)
    (d / "status.json").write_text(json.dumps(st), encoding="utf-8")


def remote(d: Path, **over):
    r = {"checked_at": time.time() - 30, "ok": True, "error": "", "branch": "origin/main", "current": "aaaaaaaaaaaa", "remote": "bbbbbbbbbbbb", "behind": 2,
         "ahead": 0, "ff_possible": True, "local_changes": 0, "migrations_changed": 1, "env_example_changed": True,
         "commits": [{"sha": "bbbbbbb", "date": "2026-10-07", "subject": "Новая функция"}, {"sha": "ccccccc", "date": "2026-10-06", "subject": "Исправление"}]}
    r.update(over)
    (d / "remote.json").write_text(json.dumps(r), encoding="utf-8")


def test_everything_requires_admin(client):
    login(client, "alice")
    for m, path in (("get", ""), ("post", "/check"), ("post", "/run"), ("get", "/log"), ("get", "/components")):
        assert getattr(client, m)(U + path).status_code == 403, path


def test_overview_without_updater_explains_what_to_do(client):
    chan(client)
    login(client, "root")
    o = client.get(U).json()
    assert o["updater"]["available"] is False and o["can_update"] is False
    assert any("updater.sh" in r for r in o["reasons"]) and o["commands"]["install"] == "./scripts/updater.sh install"
    assert client.post(f"{U}/run", json={"confirm": True}).status_code == 409
    assert client.post(f"{U}/check").status_code == 409


def test_overview_with_updater_and_remote_changes(client):
    d = chan(client)
    heartbeat(d)
    remote(d)
    login(client, "root")
    o = client.get(U).json()
    assert o["updater"]["available"] is True and o["can_update"] is True and o["reasons"] == []
    assert o["remote"]["behind"] == 2 and o["remote"]["commits"][0]["subject"] == "Новая функция" and o["up_to_date"] is False
    assert o["installed"]["version"] and "commit" in o["installed"]
    remote(d, behind=0, commits=[])
    assert client.get(U).json()["up_to_date"] is True
    remote(d, ok=False, error="нет связи с GitHub", behind=0)
    assert client.get(U).json()["up_to_date"] is False, "ошибка проверки — это не «актуально»"


def test_stale_heartbeat_means_updater_is_down(client):
    d = chan(client)
    heartbeat(d, ts=time.time() - 120)
    login(client, "root")
    o = client.get(U).json()
    assert o["updater"]["available"] is False and o["updater"]["heartbeat_age_s"] >= 119


def test_run_requires_confirmation_and_writes_a_safe_request(client):
    d = chan(client)
    heartbeat(d)
    login(client, "root")
    assert client.post(f"{U}/run", json={}).status_code == 422
    assert client.post(f"{U}/run", json={"confirm": "yes"}).status_code == 422
    r = client.post(f"{U}/run", json={"confirm": True, "force_build": True, "pull": False})
    assert r.status_code == 200
    body = (d / "request.txt").read_text(encoding="utf-8")
    lines = dict(x.split("=", 1) for x in body.strip().splitlines())
    assert re.fullmatch(r"[a-f0-9]{8,32}", lines["id"]) and lines["id"] == r.json()["request_id"]
    assert lines["action"] == "update" and lines["force_build"] == "1" and lines["pull"] == "0" and lines["by"] == "root"
    assert abs(int(lines["at"]) - time.time()) < 10
    assert set(lines) == {"id", "action", "force_build", "pull", "by", "at"}, "в запросе только разрешённые поля"
    assert client.post(f"{U}/run", json={"confirm": True}).status_code == 409, "пока запрос не принят — второй не создаётся"
    j = [a["action"] for a in client.get("/api/v1/admin/audit").json()]
    assert "system.update" in j


def test_run_is_refused_while_an_update_is_in_progress(client):
    d = chan(client)
    heartbeat(d, state="updating", step_no=5, step_name="Сборка")
    login(client, "root")
    o = client.get(U).json()
    assert o["can_update"] is False and any("уже выполняется" in x for x in o["reasons"])
    assert client.post(f"{U}/run", json={"confirm": True}).status_code == 409
    assert not (d / "request.txt").exists()


def test_request_author_is_sanitized(client):
    ch = upd.Channel(str(chan(client)))
    ch.request("update", by="a b;$(id)\nid=evil")
    body = (chan(client) / "request.txt").read_text(encoding="utf-8")
    by_line = [x for x in body.splitlines() if x.startswith("by=")][0]
    assert re.fullmatch(r"by=[A-Za-z0-9._-]+", by_line), by_line
    assert body.count("\nid=") == 0 and len([x for x in body.splitlines() if x.startswith("id=")]) == 1, "нельзя подставить свой id/action через имя"


def test_check_request(client):
    d = chan(client)
    heartbeat(d)
    login(client, "root")
    assert client.post(f"{U}/check").status_code == 200
    assert "action=check" in (d / "request.txt").read_text(encoding="utf-8")


def test_log_is_read_by_offset_and_survives_restart_of_the_log(client):
    d = chan(client)
    heartbeat(d, state="updating", step_no=3, step_total=16, step_name="Сборка образов")
    (d / "update.log").write_bytes("[1/16] Старт\nстрока 2\n".encode("utf-8"))
    login(client, "root")
    a = client.get(f"{U}/log", params={"offset": 0}).json()
    assert a["text"] == "[1/16] Старт\nстрока 2\n" and a["offset"] == a["size"] > 0
    assert a["state"] == "updating" and a["step_no"] == 3 and a["step_name"] == "Сборка образов"
    with (d / "update.log").open("ab") as f:
        f.write("строка 3\n".encode("utf-8"))
    b = client.get(f"{U}/log", params={"offset": a["offset"]}).json()
    assert b["text"] == "строка 3\n" and b["reset"] is False
    (d / "update.log").write_bytes("новое обновление\n".encode("utf-8"))    # журнал начат заново, смещение стало больше размера
    c = client.get(f"{U}/log", params={"offset": b["offset"]}).json()
    assert c["reset"] is True and c["text"] == "новое обновление\n"
    (d / "update.log").unlink()
    assert client.get(f"{U}/log").json()["text"] == ""


def test_log_chunks_do_not_split_lines(client):
    d = chan(client)
    heartbeat(d)
    line = "я" * 99 + "\n"
    (d / "update.log").write_bytes((line * 1500).encode("utf-8"))   # > 64 КБ
    login(client, "root")
    first = client.get(f"{U}/log", params={"offset": 0}).json()
    assert first["text"].endswith("\n") and len(first["text"].encode("utf-8")) <= upd.LOG_CHUNK and first["offset"] < first["size"]
    got, off = first["text"], first["offset"]
    for _ in range(20):
        nxt = client.get(f"{U}/log", params={"offset": off}).json()
        got += nxt["text"]
        if nxt["offset"] == off:
            break
        off = nxt["offset"]
    assert got == line * 1500


# ------------------------------------------------------------------------------------- версии компонентов
def external(handler=None, fail=False):
    def h(req: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("нет сети")
        u = str(req.url)
        if "livekit/livekit/releases" in u:
            return httpx.Response(200, json={"tag_name": "v1.14.0"})
        if "registry.npmjs.org/livekit-client" in u:
            return httpx.Response(200, json={"version": "2.22.3"})
        if "pypi.org/pypi/livekit-api" in u:
            return httpx.Response(200, json={"info": {"version": "1.2.1"}})
        if "pypi.org/pypi/livekit/" in u:
            return httpx.Response(200, json={"info": {"version": "1.1.99"}})
        if "pypi.org/pypi/" in u:
            return httpx.Response(200, json={"info": {"version": "99.0.0"}})
        if "api.github.com/repos/leonheard/peregovorka/commits/main" in u:
            return httpx.Response(200, json={"sha": "f" * 40})
        return httpx.Response(404)
    return httpx.MockTransport(h)


def test_components_show_installed_tested_latest_and_explain_risk(tmp_path, directory):
    gen = running_app(make_settings(tmp_path, livekit_server_version="v1.13.7"), directory, transports={"updates": external()})
    with gen as c:
        login(c, "root")
        r = c.get(f"{U}/components").json()
        assert r["internet"] is True and r["project_latest"] == "f" * 12
        rows = {x["key"]: x for x in r["rows"]}
        lk = rows["livekit_server"]
        assert (lk["installed"], lk["tested"], lk["latest"], lk["status"]) == ("v1.13.7", "v1.13.7", "v1.14.0", "newer")
        assert "не проверена" in lk["note"] and "новой редакции проекта" in lk["note"], "закреплённый компонент: новая версия не ставится отдельно"
        assert rows["livekit_api_python"]["status"] in ("ok", "ahead", "newer") and rows["livekit_api_python"]["installed"]
        assert rows["fastapi"]["status"] == "newer" and "пересборке" in rows["fastapi"]["note"]
        assert rows["livekit_client_js"]["installed"] is None and rows["livekit_client_js"]["latest"] == "2.22.3", "версия браузерной части известна только браузеру"
        assert all(x["title"] and "note" in x for x in r["rows"])


def test_components_without_internet_say_so_and_do_not_fail(tmp_path, directory):
    gen = running_app(make_settings(tmp_path, livekit_server_version="v1.13.7"), directory, transports={"updates": external(fail=True)})
    with gen as c:
        login(c, "root")
        r = c.get(f"{U}/components").json()
        assert r["internet"] is False and r["project_latest"] is None
        lk = {x["key"]: x for x in r["rows"]}["livekit_server"]
        assert lk["status"] == "unknown" and lk["latest"] is None and "нет доступа в интернет" in lk["note"]
        assert lk["installed"] == "v1.13.7"


def test_components_are_cached_and_refresh_bypasses_the_cache(tmp_path, directory):
    hits = {"n": 0}

    def h(req):
        hits["n"] += 1
        return httpx.Response(200, json={"tag_name": "v1.13.7", "version": "2.22.3", "info": {"version": "1.0.0"}, "sha": "a" * 40})

    gen = running_app(make_settings(tmp_path), directory, transports={"updates": httpx.MockTransport(h)})
    with gen as c:
        login(c, "root")
        c.get(f"{U}/components")
        n1 = hits["n"]
        c.get(f"{U}/components")
        assert hits["n"] == n1, "второй запрос — из кэша"
        c.get(f"{U}/components", params={"refresh": "true"})
        assert hits["n"] > n1


@pytest.mark.parametrize("a,b,expect", [("v1.13.7", "v1.13.7", "ok"), ("1.1.20", "1.1.21", "newer"), ("1.2.1", "1.2.0", "ahead"), ("v1.9.0", "v1.13.7", "newer"),
                                        ("0.30.1", "0.30.1", "ok"), (None, "1.0", "unknown"), ("x", "1.0", "unknown"), ("2.10", "2.9", "ahead")])
def test_version_comparison_is_numeric_not_textual(a, b, expect):
    assert upd.compare(a, b) == expect


def test_tested_versions_match_deployment_compat_env():
    env = dict(line.split("=", 1) for line in (REPO / "deployment" / "compat.env").read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    assert env["TESTED_LIVEKIT_SERVER"].strip() == upd.TESTED["livekit_server"]
    assert env["TESTED_LIVEKIT_CLIENT_JS"].strip() == upd.TESTED["livekit_client_js"]
    assert env["TESTED_LIVEKIT_PYTHON_SDK"].strip() == upd.TESTED["livekit_python_sdk"]
    assert env["TESTED_LIVEKIT_API_PYTHON"].strip() == upd.TESTED["livekit_api_python"]
