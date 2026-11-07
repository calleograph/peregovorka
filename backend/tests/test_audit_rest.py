"""Замечания аудита второго ряда: эфиры, вход, очередь помощника, удаление файлов, настройки между процессами."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone

import pytest

from app.security import passwords, storage_crypto as sc
from app.services import storage_encryption
from app.services.ldap_profiles import ProfileDirectory
from app.services.storage import LocalStorage, StorageError
from app.services.updates import Channel, ChannelBusy
from .conftest import login, make_room
from .test_live import access, get_room, go_live, lv, room_live  # noqa: F401
from .test_video_recording import call


# ---------------------------------------------------------------------------------------- очередь помощника
def test_pending_update_request_is_not_overwritten(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    ch = Channel(str(tmp_path))
    ch.request("update", by="admin")
    first = (tmp_path / "request.txt").read_text(encoding="utf-8")
    with pytest.raises(ChannelBusy):
        ch.request("volume", by="admin", op="attach", arg="/mnt/x")
    assert (tmp_path / "request.txt").read_text(encoding="utf-8") == first and "action=update" in first
    assert [p.name for p in tmp_path.iterdir()] == ["request.txt"], "временный файл не остаётся"


def test_request_left_by_a_dead_helper_is_replaced(tmp_path):
    ch = Channel(str(tmp_path))
    ch.request("check", by="admin")
    old = time.time() - 600
    os.utime(tmp_path / "request.txt", (old, old))
    ch.request("ping", by="admin")
    assert "action=ping" in (tmp_path / "request.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------- хранилище
def test_delete_and_exists_on_a_detached_volume_are_errors_not_success(tmp_path):
    root = tmp_path / "vol"
    root.mkdir()
    (root / ".peregovorka-volume").write_text("метка", encoding="utf-8")
    st = LocalStorage(str(root), marker="метка")
    st.write_bytes("a.txt", b"x")
    assert st.exists("a.txt")
    (root / ".peregovorka-volume").unlink()              # том отключён: каталог-точка пуст
    with pytest.raises(StorageError):
        st.delete("a.txt")
    with pytest.raises(StorageError):
        st.exists("a.txt")


# ---------------------------------------------------------------------------------------- пароли
def test_password_check_does_not_block_the_event_loop():
    h = passwords.hash_room_password("пароль-1234")

    async def run():
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.001)
        t = asyncio.create_task(tick())
        await asyncio.gather(*(passwords.check_password(h, "пароль-1234") for _ in range(4)))
        t.cancel()
        return ticks
    assert asyncio.run(run()) > 3


def test_room_password_limit_holds_under_parallel_attempts(client):
    """Счётчик читался отдельно, а рос после argon2: параллельные попытки проходили мимо предела."""
    login(client, "root")
    room = make_room(client, password="верный-пароль-1")
    svc = client.app_obj.state.meetings
    from app.models import Room
    from app.services.meetings import JoinError

    class U:
        user_id = uuid.uuid4()

    async def one(rm):
        try:
            await svc._check_room_password(rm, U, "неверный")     # noqa: SLF001
            return "ok"
        except JoinError as exc:
            return exc.code

    async def run():
        async with client.app_obj.state.session_maker() as db:
            rm = await db.get(Room, uuid.UUID(room["id"]))
        return await asyncio.gather(*(one(rm) for _ in range(30)))
    res = client.portal.call(run)
    assert res.count("room_password_invalid") <= client.app_obj.state.settings.room_password_max_failures
    assert "throttled" in res


def test_one_viewer_does_not_lock_out_the_whole_office(lv):  # noqa: F811
    room = room_live(lv)
    go_live(lv, room)
    login(lv, "carol")
    assert lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": "zritel-2026"}).status_code == 200
    token = get_room(lv, room["id"]).live_token
    for i in range(10):
        access(lv, token, password=f"bad-{i}")
    other = lv.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Сосед по офису"}).json()["viewer"]
    assert access(lv, token, viewer=other, password="zritel-2026").status_code == 200


# ---------------------------------------------------------------------------------------- настройки между процессами
def test_key_rotation_in_another_process_reaches_this_one(client):
    async def run(db):
        kid = await storage_encryption.rotate(db)
        return kid
    kid = call(client, run)
    sc.keyring.load([])                                   # этот процесс ключей ещё не знает
    assert sc.keyring.signature() == (None, None)
    call(client, storage_encryption.reload_if_changed)
    assert sc.keyring.signature()[1] == kid


def test_directory_profile_change_is_noticed_without_a_reload_call(client):
    async def run(db):
        d = ProfileDirectory(client.app_obj.state.settings, client.app_obj.state.ldap)
        calls = []

        async def reload(_db):
            calls.append(1)
        d.reload = reload
        await d.reload_if_changed(db)
        await d.reload_if_changed(db)
        assert calls == [], "первая сверка только запоминает состояние, запуск уже загрузил каталог"
        d._sign = ("другое",)                               # noqa: SLF001
        await d.reload_if_changed(db)
        return calls
    assert call(client, run) == [1]


# ---------------------------------------------------------------------------------------- эфир
def test_broadcast_stuck_in_ending_is_closed_after_its_meeting_ended(lv):  # noqa: F811
    """У завершённой встречи причина meeting_end заслоняла stuck, и эфир висел вечно, занимая место в лимите."""
    from livekit.protocol import egress as eg
    from sqlalchemy import select
    from app.models import Broadcast, Meeting
    from app.services.meetings import utcnow
    room = room_live(lv)
    go_live(lv, room)

    async def stuck(db):
        b = (await db.execute(select(Broadcast))).scalars().first()
        b.status = "ending"
        m = await db.get(Meeting, b.meeting_id)
        m.ended_at = utcnow()
        await db.commit()
        lv.fake.infos[b.egress_id] = eg.EgressInfo(egress_id=b.egress_id, status=eg.EgressStatus.EGRESS_ACTIVE)   # LiveKit считает его живым
        return b.egress_id
    egress = call(lv, stuck)

    async def rec(db):
        await lv.app_obj.state.live.reconcile(db)
    call(lv, rec)
    assert egress in lv.fake.stopped


def test_viewer_stall_total_is_not_lost_between_saved_snapshots():
    from app.services import live_diag
    snaps = [{"viewers": {"stalls_total": 3, "reconn_total": 1}}, {"viewers": {"stalls_total": 9, "reconn_total": 2}}]

    class B:
        quality, status, error, peak_viewers, hls_dir = "720p", "ended", None, 0, "x"
        started_at = ended_at = datetime(2026, 11, 6, tzinfo=timezone.utc)
    out = live_diag.summarize(snaps, [], B)
    assert out["stalls"] == 9 and out["reconnects"] == 2


def test_idle_restart_is_not_blamed_on_the_next_broadcast():
    from app.services.live_diag import LiveDiag
    d = LiveDiag.__new__(LiveDiag)
    d._starts = {}
    fresh: list[str] = []
    d._restart_check("egress", 1000.0, fresh)
    d._starts = {}                        # простой
    d._restart_check("egress", 5000.0, fresh)
    assert fresh == []


def test_hung_service_does_not_hold_the_database_or_the_page(lv, monkeypatch):  # noqa: F811
    """Опрос служб шёл по очереди внутри транзакции: зависшая служба держала соединение почти 30 секунд."""
    from app.services import media_checks
    monkeypatch.setattr(media_checks, "PROBE_S", 0.2)
    held = []

    async def hang():
        pool = lv.app_obj.state.engine.sync_engine.pool
        held.append(pool.checkedout() if hasattr(pool, "checkedout") else 0)
        await asyncio.sleep(30)
    lv.fake.probe = hang
    lv.fake.probe_livekit = hang
    login(lv, "root")
    t = time.time()
    r = lv.get("/api/v1/admin/live/status")
    assert r.status_code == 200 and time.time() - t < 3 and r.json()["livekit_ok"] is False
    assert not any(c["id"] == "egress" and c["status"] == "ok" for c in r.json()["checks"])


def test_journal_tells_a_container_start_from_a_replaced_worker():
    from app import main
    stat = "1 (uvicorn) S " + " ".join(["0"] * 18) + " 1000 0 0"          # 22-е поле: время запуска, тики от загрузки
    assert main.start_reason(stat, uptime=20.0, ticks=100)[0] == "app_started"
    code, level, text = main.start_reason(stat, uptime=4000.0, ticks=100)
    assert code == "app_worker_restarted" and level == "warn" and "66 мин" in text
    assert main.start_reason("мусор", uptime=1.0, ticks=100)[0] == "app_started"
