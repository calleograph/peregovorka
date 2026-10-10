"""Состояния помощника обновлений: «не установлен» / «установлен, но не запущен» / «запущен, но не отвечает» / «работает не от root» / «работает вручную» различаются
(раньше всё сливалось в «не установлен»). Пульс отдельным процессом не даёт принять работающий помощник за пропавший во время долгой сборки."""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.services import updates as upd

from .conftest import login

U = "/api/v1/admin/updates"
NOW = time.time


def chan(client) -> Path:
    d = Path(client.app_obj.state.settings.data_dir) / "updater"
    d.mkdir(exist_ok=True)
    return d


def put(d: Path, name: str, **fields):
    (d / name).write_text(json.dumps(fields), encoding="utf-8")


def status_file(d: Path, age: float = 1, **over):
    put(d, "status.json", **{"ts": NOW() - age, "pid": 100, "uid": 0, "state": "idle", "request_id": "", "step_no": 0, "step_total": 16, "step_name": "", "started_at": 0,
                             "finished_at": 0, "exit_code": None, "result": "", "by": "", "project": "pg", **over})


def pulse_file(d: Path, age: float = 1, **over):
    put(d, "helper.json", **{"ts": NOW() - age, "pid": 100, "uid": 0, "user": "root", "under_systemd": True, "unit": "u", "started_at": 0, "version": "1", "stopped": False, **over})


def state(client) -> dict:
    login(client, "root")
    return client.get(U).json()["helper"]


def test_nothing_on_disk_means_not_installed(client):
    chan(client)
    h = state(client)
    assert h["state"] == "not_installed" and not h["available"] and "не установлен" in h["message"] and "updater.sh install --yes" in h["fix"]


def test_a_healthy_helper_is_ok(client):
    d = chan(client)
    status_file(d)
    pulse_file(d)
    h = state(client)
    assert h["state"] == "ok" and h["available"] and h["privileged"] and h["problem"] is None


def test_pulse_keeps_the_helper_alive_during_a_long_build_when_status_json_is_old(client):
    """Реальная причина «помощник не установлен» после обновления: сборка образов идёт минуты, а status.json меняется только при смене этапа."""
    d = chan(client)
    status_file(d, age=600, state="updating", action="update")
    pulse_file(d, age=3)
    login(client, "root")
    ov = client.get(U).json()
    assert ov["updater"]["available"] is True and ov["helper"]["state"] == "ok" and ov["helper"]["privileged"] is True
    assert "Сейчас выполняет обновление" in ov["helper"]["message"]
    assert not any("не установлен" in r for r in ov["reasons"]), ov["reasons"]


def test_old_status_without_any_pulse_is_not_alive(client):
    d = chan(client)
    status_file(d, age=600)
    h = state(client)
    assert h["available"] is False and h["state"] == "installed_inactive", "файлы есть, пульса давно нет — установлен, но не запущен, а не «не установлен»"
    assert "пульс пропал" in h["message"]


def test_installed_but_stopped_reports_how_the_service_ended(client):
    d = chan(client)
    status_file(d, age=900, state="stopped")
    put(d, "unit-state.json", ts=NOW() - 800, result="exit-code", exit_code="exited", exit_status="1", unit="u")
    h = state(client)
    assert h["state"] == "installed_inactive" and "exit-code" in h["message"] and "код 1" in h["message"]


def test_clean_stop_is_shown_as_stopped_not_missing(client):
    d = chan(client)
    status_file(d, age=900, state="stopped")
    pulse_file(d, age=900, stopped=True)
    h = state(client)
    assert h["state"] == "installed_inactive" and "остановлен" in h["message"]


def test_host_verification_verdicts_are_shown_with_their_reason(client):
    d = chan(client)
    inst = {"checked_at": NOW() - 60, "fix": "sudo ./scripts/updater.sh install --yes", "unit_exists": True, "unit_user": "root", "enabled": "enabled", "active": "failed", "substate": "failed",
            "result": "exit-code", "restarts": 7, "main_pid": 0, "proc_uid": "", "proc_user": "", "expected_uid": 0, "ping": ""}
    put(d, "install-state.json", verdict="failed", why="Служба установлена, но не работает: состояние «failed/failed», результат «exit-code», перезапусков: 7.", **inst)
    h = state(client)
    assert h["state"] == "failed" and "перезапусков: 7" in h["message"] and h["details"]["restarts"] == 7 and h["available"] is False
    put(d, "install-state.json", verdict="not_installed", why="Служба помощника не установлена.", **inst)
    assert state(client)["state"] == "not_installed"
    put(d, "install-state.json", verdict="wrong_user", why="Процесс 77 работает от пользователя ivan (uid 1000), а должен — от uid 0 (в файле службы User=root).", **inst)
    h = state(client)
    assert h["state"] == "wrong_user" and "uid 1000" in h["message"], "старая проблема: unit User=root, а процесс — чужой; вердикт берётся из фактического UID"


def test_alive_helper_running_as_another_user_is_wrong_user_and_not_privileged(client):
    d = chan(client)
    status_file(d, uid=1000)
    pulse_file(d, uid=1000, user="ivan", under_systemd=False)
    h = state(client)
    assert h["state"] == "wrong_user" and h["available"] and not h["privileged"] and h["problem"] == "no_privileges"
    assert "ivan" in h["message"] and "вручную" in h["message"] and "заменит" in h["message"]


def test_root_but_started_by_hand_is_a_warning_not_an_error(client):
    d = chan(client)
    status_file(d)
    pulse_file(d, under_systemd=False)
    h = state(client)
    assert h["state"] == "manual_process" and h["privileged"] is True and "перезагрузки" in h["message"]


def test_pulse_alive_but_main_loop_stuck_is_unresponsive(client):
    d = chan(client)
    status_file(d, age=400, state="idle")
    pulse_file(d, age=2)
    h = state(client)
    assert h["state"] == "unresponsive" and h["available"] and "не отвечает" in h["message"]


def test_busy_helper_with_old_status_is_not_called_unresponsive(client):
    d = chan(client)
    status_file(d, age=400, state="repairing")
    pulse_file(d, age=2)
    assert state(client)["state"] == "ok"


def test_repairs_list_names_the_exact_problem(client):
    d = chan(client)
    status_file(d, age=900)
    put(d, "unit-state.json", ts=NOW() - 800, result="success", exit_code="exited", exit_status="0")
    login(client, "root")
    items = client.get(U + "/repairs").json()["items"]
    it = next(i for i in items if i["id"] == "updater_helper")
    assert it["state"] == "installed_inactive" and "установлен, но не запущен" in it["title"] and it["command"].endswith("install --yes") and "идемпотентна" in it["fix"]
    d2 = chan(client)
    (d2 / "status.json").unlink()
    (d2 / "unit-state.json").unlink()
    it = next(i for i in client.get(U + "/repairs").json()["items"] if i["id"] == "updater_helper")
    assert it["state"] == "not_installed" and "не установлен" in it["title"]


def test_ping_asks_the_helper_and_is_refused_when_it_cannot_answer(client):
    d = chan(client)
    login(client, "root")
    r = client.post(U + "/ping")
    assert r.status_code == 409 and "не установлен" in r.json()["detail"]
    status_file(d)
    pulse_file(d)
    r = client.post(U + "/ping")
    assert r.status_code == 200
    body = (d / "request.txt").read_text()
    assert f"id={r.json()['request_id']}" in body and "action=ping" in body
    assert client.post(U + "/ping").status_code == 409, "пока предыдущий запрос не принят, второй не ставится"
    (d / "request.txt").unlink()
    status_file(d, state="updating")
    assert client.post(U + "/ping").status_code == 409
    login(client, "alice")
    assert client.post(U + "/ping").status_code == 403


def test_ping_answer_is_visible_in_the_overview(client):
    d = chan(client)
    status_file(d, ping_id="abc123", ping_at=int(NOW()))
    pulse_file(d)
    login(client, "root")
    up = client.get(U).json()["updater"]
    assert up["ping_id"] == "abc123" and up["ping_at"]


def test_state_function_is_pure_and_covers_every_verdict_the_host_script_can_write():
    known = {"ok", "manual_process", "wrong_user", "unresponsive", "installed_inactive", "failed", "stale_unit", "not_installed"}
    for verdict in ("failed", "wrong_user", "stale_unit", "not_enabled", "manual_process", "unresponsive", "permission_error", "installed_inactive", "not_installed"):
        st = {"available": False, "install": {"verdict": verdict, "why": "причина", "checked_at": NOW() - 5}, "heartbeat_age_s": 500}
        assert upd.helper_state(st)["state"] in known, verdict
