"""Автоматическое обновление: проверка по расписанию, защита от идущих встреч, запись итога."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .conftest import login, make_room, put_settings
from .test_transcripts import _join
from .test_updates import chan, heartbeat, remote

U = "/api/v1/admin/updates"
NIGHT = datetime(2026, 10, 12, 0, 5, tzinfo=timezone.utc)       # 00:05 — через пять минут после назначенных 00:00
NOON = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def tick(client, now=NIGHT) -> str:
    return client.portal.call(lambda: client.app_obj.state.autoupdate.tick(now))


def state(client) -> dict:
    return client.portal.call(client.app_obj.state.autoupdate.state)


def request_of(d: Path) -> dict:
    return dict(x.split("=", 1) for x in (d / "request.txt").read_text(encoding="utf-8").strip().splitlines())


def answer_check(d: Path, behind: int, **over):
    (d / "request.txt").unlink()
    remote(d, behind=behind, ok=True, checked_at=time.time(), current_version="0.7.0", remote_version="0.7.1" if behind else "0.7.0", **over)


def finish_update(d: Path, rid: str, ok: bool):
    heartbeat(d, state="idle", request_id=rid, started_at=time.time() - 240, finished_at=time.time(), exit_code=0 if ok else 1, result="ok" if ok else "failed", step_name="Сборка образов")
    row = {"at": time.time(), "started": time.time() - 240, "result": "ok" if ok else "failed", "stage": "" if ok else "Сборка образов", "from_version": "0.7.0", "to_version": "0.7.1",
           "from_commit": "a", "to_commit": "b", "source": "web", "by": "auto-update"}
    (d / "history.ndjson").write_text(json.dumps(row) + "\n", encoding="utf-8")


def setup(client, enabled=True, **over):
    d = chan(client)
    heartbeat(d)
    put_settings(client, "autoupdate", enabled=enabled, **over)
    return d


def test_disabled_does_nothing_and_settings_are_validated(client):
    d = setup(client, enabled=False)
    assert tick(client) == "disabled" and not (d / "request.txt").exists()
    login(client, "root")
    assert client.put("/api/v1/admin/settings/autoupdate", json={"time": "25:99"}).status_code == 422
    assert client.put("/api/v1/admin/settings/autoupdate", json={"time": "03:30"}).status_code == 200
    got = client.get(f"{U}/auto").json()
    assert got["settings"]["time"] == "03:30" and got["settings"]["enabled"] is False and got["next_run_at"] is None and got["phase"] == "idle"


def test_before_the_scheduled_time_nothing_happens(client):
    d = setup(client, time="03:00")
    assert tick(client, NIGHT) == "idle" and not (d / "request.txt").exists()


def test_no_new_version_means_no_update_and_one_check_per_day(client):
    d = setup(client)
    assert tick(client) == "check_requested" and request_of(d)["action"] == "check" and request_of(d)["by"] == "auto-update"
    assert tick(client) == "checking"
    answer_check(d, behind=0)
    assert tick(client) == "no_update"
    assert state(client)["last"]["result"] == "no_update"
    assert tick(client) == "idle" and not (d / "request.txt").exists(), "в эти сутки больше не проверяем"
    assert tick(client, datetime(2026, 10, 13, 0, 1, tzinfo=timezone.utc)) == "check_requested", "на следующие сутки — снова"


def test_new_version_is_installed_through_the_normal_updater_and_the_result_is_recorded(client):
    d = setup(client)
    tick(client)
    answer_check(d, behind=2)
    assert tick(client) == "update_started"
    req = request_of(d)
    assert req["action"] == "update" and req["by"] == "auto-update" and req["force_build"] == "0"
    heartbeat(d, state="updating", request_id=req["id"], started_at=time.time())
    assert tick(client) == "running"
    finish_update(d, req["id"], ok=True)
    assert tick(client) == "updated"
    last = state(client)["last"]
    assert last["result"] == "updated" and last["from_version"] == "0.7.0" and last["to_version"] == "0.7.1" and 200 <= last["duration_s"] <= 400
    login(client, "root")
    shown = client.get(f"{U}/auto").json()
    assert shown["last"]["result"] == "updated" and shown["phase"] == "idle"
    h = client.get(U).json()["history"][0]
    assert h["by"] == "auto-update" and h["result"] == "ok", "в истории попыток обновление помечено как автоматическое"


def test_failed_update_is_reported_with_a_readable_reason(client):
    d = setup(client)
    tick(client)
    answer_check(d, behind=1)
    tick(client)
    finish_update(d, request_of(d)["id"], ok=False)
    assert tick(client) == "failed"
    last = state(client)["last"]
    assert last["result"] == "failed" and "Сборка образов" in last["error"] and "Подробности" in last["error"]


def test_active_meeting_postpones_the_update_until_it_ends_and_then_installs(client):
    d = setup(client)
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    tick(client)
    answer_check(d, behind=1)
    assert tick(client) == "deferred", "идёт встреча — не обновляемся"
    assert not (d / "request.txt").exists() and "идут встречи" in state(client)["deferred"]
    login(client, "root")
    assert "идут встречи" in client.get(f"{U}/auto").json()["deferred"]
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{a['meeting_id']}/end").status_code == 204
    assert state(client)["phase"] == "waiting"
    assert tick(client) == "update_started" and request_of(d)["action"] == "update", "встреч нет — обновление запускается"


def test_waiting_for_a_free_slot_gives_up_after_the_window_and_tries_again_tomorrow(client):
    d = setup(client, window_hours=1)
    room = make_room(client)
    _join(client, "alice", room["id"])
    tick(client)
    answer_check(d, behind=1)
    assert tick(client) == "deferred"

    async def age():
        st = await client.app_obj.state.autoupdate.state()
        st["first_wait"] = time.time() - 2 * 3600
        await client.app_obj.state.autoupdate._save(st)
    client.portal.call(age)
    assert tick(client) == "deferred_giveup"
    last = state(client)["last"]
    assert last["result"] == "deferred" and "окна без встреч" in last["error"] and last["to_version"] == "0.7.1"
    assert not (d / "request.txt").exists(), "встречу не прерывали"


def test_without_the_helper_the_cycle_is_skipped_with_an_explanation(client):
    setup(client)
    (chan(client) / "status.json").unlink()
    assert tick(client) == "no_helper"
    assert "не запущен" in state(client)["last"]["error"]


def test_run_now_uses_the_same_rules(client):
    d = setup(client, enabled=False, time="23:59")
    login(client, "root")
    assert client.post(f"{U}/auto/run").status_code == 200
    assert tick(client, NOON) == "check_requested" and request_of(d)["by"] == "auto-update", "«Выполнить сейчас» запускает цикл вне расписания"
    assert client.post(f"{U}/auto/run").status_code == 409
