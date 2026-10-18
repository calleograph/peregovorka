"""Обновление из веб-интерфейса: обмен с исполнителем (каталог updater), журнал обновления, версии компонентов."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import pytest

from app.services import changelog as chlog
from app.services import components as comp
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
    assert any("помощник" in r.lower() for r in o["reasons"]) and o["commands"]["install"] == "sudo ./scripts/updater.sh install --yes"
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
            return httpx.Response(200, json={"tag_name": "v1.99.0"})
        if "registry.npmjs.org/livekit-client" in u:
            return httpx.Response(200, json={"version": comp.tested_map()["livekit_client_js"]})
        if "pypi.org/pypi/fastapi/" in u:
            return httpx.Response(200, json={"info": {"version": "99.0.0"}})
        if "pypi.org/pypi/" in u:
            return httpx.Response(200, json={"info": {"version": comp.tested_map().get("livekit_api_python", "1.0")}})
        if "api.github.com/repos/calleograph/peregovorka/commits/main" in u:
            return httpx.Response(200, json={"sha": "f" * 40})
        return httpx.Response(404)
    return httpx.MockTransport(h)


def web_transport(components: dict | None = None):
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/version.json":
            return httpx.Response(200, json={"version": "x", "node": "24.22.0", "components": components or {}})
        if req.url.path == "/nginx-version.txt":
            return httpx.Response(200, text="1.30.6\n")
        if req.url.path == "/drawio/VERSION":
            return httpx.Response(200, text=comp.tested_map()["drawio"] + "\n")
        return httpx.Response(404)
    return httpx.MockTransport(h)


def test_components_table_shows_running_tested_available_status_and_decision(tmp_path, directory):
    tested = comp.tested_map()
    web = {"livekit-client": tested["livekit_client_js"], "react": "0.0.1"}
    gen = running_app(make_settings(tmp_path, livekit_server_version=tested["livekit_server"]), directory, transports={"updates": external(), "web": web_transport(web)})
    with gen as c:
        login(c, "root")
        r = c.get(f"{U}/components").json()
        assert r["internet"] is True and r["project_latest"] == "f" * 12 and r["release"] and r["reviewed_at"]
        rows = {x["key"]: x for x in r["rows"]}
        lk = rows["livekit_server"]
        assert (lk["installed"], lk["tested"], lk["latest"]) == (tested["livekit_server"], tested["livekit_server"], "v1.99.0")
        assert lk["status"] == "available" and "следующего релиза" in lk["note"] and lk["bundle"] == "livekit" and lk["changelog"].startswith("https://")
        assert rows["fastapi"]["installed"] and rows["fastapi"]["status"] in ("available", "current"), "версия backend — из работающего процесса"
        assert rows["livekit_client_js"]["installed"] == tested["livekit_client_js"] and rows["livekit_client_js"]["status"] == "current", "frontend — из собранного приложения"
        assert rows["react"]["installed"] == "0.0.1" and rows["react"]["status"] == "review", "работает не та версия, что проверена в релизе — требуется проверка"
        assert rows["nginx"]["installed"] == "1.30.6" and rows["node"]["installed"] == "24.22.0" and rows["drawio"]["installed"] == tested["drawio"]
        assert rows["torch"]["status"] == "error" and rows["torch"]["installed"] is None, "ASR не запущен: версия не определена — это ошибка определения, а не «актуально»"
        assert rows["livekit_sip"]["status"] == "off" and rows["officejs"]["status"] == "manual"
        assert all(x["title"] and x["status"] in comp.STATUSES and x["checked_at"] for x in r["rows"])
        assert rows["fastapi"]["security"]["state"] in ("ok", "advisories", "unknown")


def test_deferred_update_is_reported_with_reason_task_and_date(tmp_path, directory, monkeypatch):
    m = comp.load_manifest()
    pg = next(x for x in m["components"] if x["key"] == "postgres")
    assert pg.get("deferred") and pg["deferred"]["issue"].startswith("DEP-"), "мажорная смена PostgreSQL отложена с задачей"
    rows = comp.build_rows(m, {"postgres": pg["pinned"] + ".1", "livekit_server": m["components"][0]["pinned"]}, {}, sip_enabled=False)
    r = {x["key"]: x for x in rows}["postgres"]
    assert r["status"] == "deferred" and r["deferred"]["reason"] and r["deferred"]["recheck"] and r["deferred"]["issue"].startswith("DEP-")


def test_components_without_internet_say_so_and_do_not_fail(tmp_path, directory):
    tested = comp.tested_map()
    gen = running_app(make_settings(tmp_path, livekit_server_version=tested["livekit_server"]), directory, transports={"updates": external(fail=True), "web": web_transport()})
    with gen as c:
        login(c, "root")
        r = c.get(f"{U}/components").json()
        assert r["internet"] is False and r["project_latest"] is None
        lk = {x["key"]: x for x in r["rows"]}["livekit_server"]
        assert lk["status"] == "current" and lk["latest"] == tested["livekit_server"] and "интернета получить не удалось" in lk["note"]
        assert lk["installed"] == tested["livekit_server"]


def test_components_are_cached_and_refresh_bypasses_the_cache(tmp_path, directory):
    hits = {"n": 0}

    def h(req):
        hits["n"] += 1
        return httpx.Response(200, json={"tag_name": "v1.13.7", "version": "2.22.3", "info": {"version": "1.0.0"}, "sha": "a" * 40})

    gen = running_app(make_settings(tmp_path), directory, transports={"updates": httpx.MockTransport(h), "web": web_transport()})
    with gen as c:
        login(c, "root")
        c.get(f"{U}/components")
        n1 = hits["n"]
        c.get(f"{U}/components")
        assert hits["n"] == n1, "второй запрос — из кэша"
        c.get(f"{U}/components", params={"refresh": "true"})
        assert hits["n"] > n1


@pytest.mark.parametrize("a,b,expect", [("v1.13.7", "v1.13.7", "ok"), ("1.1.20", "1.1.21", "newer"), ("1.2.1", "1.2.0", "ahead"), ("v1.9.0", "v1.13.7", "newer"),
                                        ("0.30.1", "0.30.1", "ok"), (None, "1.0", "unknown"), ("x", "1.0", "unknown"), ("2.10", "2.9", "ahead"), ("server-b11371", "server-b11718", "newer"),
                                        ("7447938d791c", "7447938d791c", "ok"), ("a" * 40, "b" * 40, "newer")])
def test_version_comparison_is_numeric_not_textual(a, b, expect):
    assert comp.compare(a, b) == expect


def test_compat_env_matches_the_release_manifest():
    env = dict(line.split("=", 1) for line in (REPO / "deployment" / "compat.env").read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    t = comp.tested_map()
    assert env["TESTED_LIVEKIT_SERVER"].strip() == t["livekit_server"]
    assert env["TESTED_LIVEKIT_CLIENT_JS"].strip() == t["livekit_client_js"]
    assert env["TESTED_LIVEKIT_PYTHON_SDK"].strip() == t["livekit_python_sdk"]
    assert env["TESTED_LIVEKIT_API_PYTHON"].strip() == t["livekit_api_python"]


def hist(d: Path, *rows):
    (d / "history.ndjson").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def test_history_separates_installed_last_success_and_attempts(client):
    d = chan(client)
    # старая неудачная попытка из веб-интерфейса, затем успешное обновление из терминала
    heartbeat(d, state="idle", result="failed", exit_code=1, finished_at=1000, step_no=5, step_name="Сборка образов")
    hist(d, {"at": 1000, "started": 900, "result": "failed", "stage": "Сборка образов", "from_version": "0.1.3", "to_version": "0.1.4", "from_commit": "b93967a", "to_commit": "99defd0", "source": "web", "by": "root"},
         {"at": 2000, "started": 1900, "result": "ok", "stage": "", "from_version": "0.1.3", "to_version": "0.1.4", "from_commit": "b93967a", "to_commit": "99defd0", "source": "cli", "by": ""},
         {"at": 1, "started": 0, "result": "weird"})
    login(client, "root")
    o = client.get(U).json()
    assert [h["result"] for h in o["history"]] == ["ok", "failed"], "новые первыми, мусорные записи игнорируются"
    assert o["last_success"]["source"] == "cli" and o["last_success"]["to_version"] == "0.1.4"
    assert o["updater"]["stale"] is True, "неудачная попытка старше успешного обновления не должна выглядеть текущей"


def test_failed_attempt_is_not_stale_without_later_success_and_running_is_never_stale(client):
    d = chan(client)
    heartbeat(d, state="idle", result="failed", exit_code=1, finished_at=3000)
    hist(d, {"at": 3000, "started": 2900, "result": "failed", "stage": "Миграции", "source": "web"})
    login(client, "root")
    o = client.get(U).json()
    assert o["updater"]["stale"] is False and o["last_success"] is None
    heartbeat(d, state="updating", finished_at=0)
    hist(d, {"at": 5000, "started": 4000, "result": "ok", "source": "cli"})
    assert client.get(U).json()["updater"]["stale"] is False


def test_no_history_file_is_fine(client):
    d = chan(client)
    heartbeat(d)
    login(client, "root")
    o = client.get(U).json()
    assert o["history"] == [] and o["last_success"] is None


# ------------------------------------------------------------------------------------------------ «Что нового»
CHANGELOG = """## 0.6.1 (2026-10-10)
- **Исправлена ошибка** в формировании протокола: оборванный ответ больше не выглядит готовым.
- Новая страница «Доступ к системе» в разделе администрирования.
- Добавлен порт UDP 7882 для LiveKit: откройте его в файрволе.
- Изменена схема базы данных: добавлены таблицы временных переговорок.

## 0.6.0 (2026-10-09)
- Необязательная локальная модель Qwen3 1.7B.
- Известное ограничение: на часовой встрече модель путает ответственных.

## 0.5.2 (2026-10-08)
- Старый раздел, который уже установлен.
"""


def test_changelog_between_installed_and_available_is_grouped_and_highlights_risky_changes():
    d = chlog.build(CHANGELOG, "0.6.0", "0.6.1", migrations_changed=1, env_changed=True)
    assert d["installed"] == "0.6.0" and d["available"] == "0.6.1" and d["versions"] == ["0.6.1"], "версия 0.6.0 уже установлена: в список она не попадает"
    by = {g["id"]: [i["text"] for i in g["items"]] for g in d["groups"]}
    assert any("оборванный ответ" in t for t in by["fixes"]) and any("Доступ к системе" in t for t in by["ui"])
    assert any("Изменена схема" in t for t in by["db"]) and any("порт UDP 7882" in t for t in by["config"])
    kinds = {k for x in d["important"] for k in x["kinds"]}
    assert {"db", "port"} <= kinds and {f["kind"] for f in d["facts"]} == {"db", "config"} and d["empty"] is False
    both = chlog.build(CHANGELOG, "0.5.2", "0.6.1")
    assert both["versions"] == ["0.6.1", "0.6.0"] and any("Известное ограничение" in i["text"] for g in both["groups"] if g["id"] == "limits" for i in g["items"])
    assert chlog.build(CHANGELOG, "0.6.1", "0.6.1")["empty"] is True and chlog.build("", "0.6.0", "0.6.1")["empty"] is True


def test_changelog_subheadings_set_the_group_and_continuations_join():
    text = ("## 0.7.0\n### Исправления\n- Поправлена подпись\n  в окне.\n"
            "### Что потребуется от администратора\n- Один раз выполните: sudo ./scripts/updater.sh install --yes\n")
    d = chlog.build(text, "0.6.0", "0.7.0")
    by = {g["id"]: [i["text"] for i in g["items"]] for g in d["groups"]}
    assert by["fixes"] == ["Поправлена подпись в окне."] and "admin" in by and "manual" in {k for x in d["important"] for k in x["kinds"]}


def test_overview_has_changes_and_run_saves_them_for_the_history(client):
    d = chan(client)
    heartbeat(d)
    remote(d, current_version="0.6.0", remote_version="0.6.1", changelog=CHANGELOG.split("## 0.6.0")[0])
    login(client, "root")
    o = client.get(U).json()
    assert o["changes"]["installed"] == "0.6.0" and o["changes"]["available"] == "0.6.1" and o["changes"]["important"]
    assert client.get(f"{U}/changes", params={"from": "0.6.0", "to": "0.6.1"}).status_code == 404
    assert client.post(f"{U}/run", json={"confirm": True}).status_code == 200
    saved = client.get(f"{U}/changes", params={"from": "0.6.0", "to": "0.6.1"}).json()
    assert saved["by"] == "root" and saved["versions"] == ["0.6.1"] and saved["groups"]
    row = {"at": time.time(), "started": time.time() - 60, "result": "ok", "stage": "", "from_version": "0.6.0", "to_version": "0.6.1",
           "from_commit": "a", "to_commit": "b", "source": "web", "by": "root"}
    (d / "history.ndjson").write_text(json.dumps(row) + "\n", encoding="utf-8")
    h = client.get(U).json()["history"][0]
    assert h["has_changes"] is True
    for bad in ("../x", "0.6", "a.b.c"):
        assert client.get(f"{U}/changes", params={"from": bad, "to": "0.6.1"}).status_code == 404, "имя файла строится только из корректных версий"
    remote(d, behind=0, commits=[])
    assert client.get(U).json()["changes"] is None


def test_user_changelog_format_groups_by_release_headings():
    """Пользовательский формат CHANGELOG (docs/VERSIONING.md): подзаголовки релиза раскладываются по группам окна «Что нового» без угадывания по словам."""
    text = """## 0.11.0 (2026-10-10)

### Новые возможности
- Можно закреплять участников на своей сцене.

### Улучшения
- Переключение раскладки стало плавным.

### Исправления
- Исправлено зависание.

### Для администраторов
- Новых портов нет; миграций нет.
"""
    d = chlog.build(text, "0.10.1", "0.11.0")
    by = {g["id"]: [i["text"] for i in g["items"]] for g in d["groups"]}
    assert by["features"] == ["Можно закреплять участников на своей сцене."]
    assert by["improvements"] == ["Переключение раскладки стало плавным."]
    assert by["fixes"] == ["Исправлено зависание."]
    assert by["admin"] == ["Новых портов нет; миграций нет."]
    assert [g["title"] for g in d["groups"]][:2] == ["Новые возможности", "Улучшения"]
