"""Сохранность данных из аудита: сбой на любом шаге не теряет реплики, записи и материалы встречи."""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Meeting, Protocol, Recording, TranscriptSegment
from app.services import video_rec
from app.services.asr_bridge import SEGMENTS_STREAM
from app.services.recordings import BYTES_PER_SEC, drop_pcm, finalize_pcm_files
from app.services.storage import meeting_relpath
from app.workers.segment_consumer import GROUP, claim_stale, drop_dead_consumers, ensure_group, process_batch
from .conftest import make_room
from .test_transcripts import _join, _segment
from .test_video_recording import call


# ---------------------------------------------------------------------------------------- реплики
def test_message_of_a_vanished_consumer_is_stored_once(client):
    """Контейнер пересоздан: сообщение осталось без подтверждения у прежнего имени потребителя."""
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    app = client.app_obj
    r = app.state.redis
    seg = _segment(a["meeting_id"], a["identity"], "Реплика из упавшего контейнера")

    async def run():
        await ensure_group(r)
        await r.xadd(SEGMENTS_STREAM, seg)
        await r.xreadgroup(GROUP, "backend-old-container", {SEGMENTS_STREAM: ">"}, count=10)
        assert await claim_stale(r, app.state.session_maker, "backend-new", idle_ms=0) == 1
        assert await claim_stale(r, app.state.session_maker, "backend-new", idle_ms=0) == 0, "подтверждённое не повторяется"
        await r.xadd(SEGMENTS_STREAM, seg)                     # тот же segment_uid: например, сообщение прочитано дважды
        await process_batch(r, app.state.session_maker, "backend-new", block_ms=10)
        async with app.state.session_maker() as db:
            return (await db.execute(select(func.count()).select_from(TranscriptSegment))).scalar_one()
    assert client.portal.call(run) == 1


def test_database_failure_keeps_the_message_for_the_next_claim(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    app = client.app_obj
    r = app.state.redis

    class Broken:
        def __call__(self):
            raise OSError("база недоступна")

    async def run():
        await ensure_group(r)
        await r.xadd(SEGMENTS_STREAM, _segment(a["meeting_id"], a["identity"], "Пока база лежала"))
        await process_batch(r, Broken(), "backend-a", block_ms=10)
        assert (await r.xpending(SEGMENTS_STREAM, GROUP))["pending"] == 1
        assert await claim_stale(r, app.state.session_maker, "backend-a", idle_ms=0) == 1
        return (await r.xpending(SEGMENTS_STREAM, GROUP))["pending"]
    assert client.portal.call(run) == 0


def test_only_idle_consumers_without_messages_are_forgotten(client):
    r = client.app_obj.state.redis

    async def run():
        await ensure_group(r)
        await r.xadd(SEGMENTS_STREAM, {"a": "1"})
        await r.xreadgroup(GROUP, "old", {SEGMENTS_STREAM: ">"}, count=1)
        await drop_dead_consumers(r, "new")
        return [c["name"] for c in await r.xinfo_consumers(SEGMENTS_STREAM, GROUP)]
    assert "old" in client.portal.call(run), "у него есть неподтверждённое сообщение"


# ---------------------------------------------------------------------------------------- каталоги
def test_two_rooms_with_one_name_and_start_minute_get_different_folders():
    start = datetime(2026, 11, 6, 10, 0)
    a, b = uuid.uuid4(), uuid.uuid4()
    assert meeting_relpath("Планёрка", start, a) != meeting_relpath("Планёрка", start, b)
    from app.services.restore import _TIME_DIR
    assert _TIME_DIR.match(meeting_relpath("Планёрка", start, a).rsplit("/", 1)[1]), "разбор каталога при восстановлении принимает новое имя"


def test_every_writer_of_meeting_files_passes_the_meeting_id():
    root = Path(video_rec.__file__).parent
    for name in ("video_rec.py", "live.py", "protocols.py"):
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            if "meeting_relpath(" in line and "import" not in line:
                assert "meeting.id" in line, (name, line)


# ---------------------------------------------------------------------------------------- дорожки
def test_pcm_stays_until_the_database_row_is_saved(tmp_path):
    d = tmp_path / "m-abc"
    d.mkdir()
    (d / "u-a.pcm").write_bytes(b"\x01\x00" * (BYTES_PER_SEC // 2 * 5))
    done = finalize_pcm_files(str(tmp_path), "m-abc", "К/д/в", {"u-a": "А"})
    assert len(done) == 1 and (d / "u-a.pcm").exists(), "сбой до записи в базу не должен потерять дорожку"
    assert finalize_pcm_files(str(tmp_path), "m-abc", "К/д/в", {"u-a": "А"}, {"u-a"}) == [] and not d.exists(), "дорожка уже в базе: исходник просто убирается"
    drop_pcm(str(tmp_path), "m-abc", ["u-a"])


# ---------------------------------------------------------------------------------------- обработка встречи
def _meeting(client, mid):
    return call(client, lambda db: db.get(Meeting, uuid.UUID(mid)))


def _ended(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    client.app_obj.state.protocols.flush_delay = 0
    assert client.post(f"/api/v1/meetings/{a['meeting_id']}/end").status_code == 204
    return a["meeting_id"]


def _wait(cond, sec=5.0):
    end = time.time() + sec
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_normal_end_marks_the_meeting_processed(client):
    mid = _ended(client)
    assert _wait(lambda: _meeting(client, mid).finalized_at is not None)


def test_meeting_lost_by_a_crash_is_picked_up_and_processed_once(client, monkeypatch):
    """Процесс умер после ended_at: задача в памяти пропала, а запись в базе осталась."""
    ps = client.app_obj.state.protocols
    real = ps.finalize

    async def lost(mid):
        return None
    monkeypatch.setattr(ps, "finalize", lost)
    mid = _ended(client)
    assert _meeting(client, mid).finalized_at is None
    monkeypatch.setattr(ps, "finalize", real)

    async def age(db):
        m = await db.get(Meeting, uuid.UUID(mid))
        m.ended_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()
    call(client, age)
    assert call(client, ps.finalize_pending) == 1
    assert _wait(lambda: _meeting(client, mid).finalized_at is not None)
    assert call(client, ps.finalize_pending) == 0, "обработанная встреча больше не подбирается"


def test_failed_step_is_retried_with_a_pause_and_stops_after_the_limit(client, monkeypatch):
    ps = client.app_obj.state.protocols

    async def boom(meeting_id):
        return "RuntimeError: нет связи с хранилищем"
    monkeypatch.setattr(ps, "_finalize", boom)
    mid = _ended(client)
    assert _wait(lambda: _meeting(client, mid).finalize_attempts == 1)
    m = _meeting(client, mid)
    assert m.finalized_at is None and "хранилищем" in m.finalize_error and m.finalize_after is not None

    async def age(db):
        row = await db.get(Meeting, uuid.UUID(mid))
        row.ended_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()
    call(client, age)
    assert call(client, ps.finalize_pending) == 0, "пауза ещё не прошла"

    async def exhausted(db):
        row = await db.get(Meeting, uuid.UUID(mid))
        row.finalize_after, row.finalize_attempts = None, 8
        await db.commit()
    call(client, exhausted)
    assert call(client, ps.finalize_pending) == 0, "попытки исчерпаны"


def test_two_processes_do_not_process_one_meeting(client):
    ps = client.app_obj.state.protocols
    mid = _ended(client)
    assert _wait(lambda: _meeting(client, mid).finalized_at is not None)
    calls = []

    async def slow(meeting_id):
        calls.append(meeting_id)
        await asyncio.sleep(0.2)
        return None
    ps._finalize = slow                 # noqa: SLF001

    async def both():
        await asyncio.gather(ps.finalize(uuid.UUID(mid)), ps.finalize(uuid.UUID(mid)))
    client.portal.call(both)
    assert len(calls) == 1


def test_repeated_processing_does_not_duplicate_the_transcript_document(client, monkeypatch):
    ps = client.app_obj.state.protocols
    mid = _ended(client)
    assert _wait(lambda: _meeting(client, mid).finalized_at is not None)

    async def run():
        await ps.finalize(uuid.UUID(mid))
        await ps.finalize(uuid.UUID(mid))
    client.portal.call(run)
    n = call(client, lambda db: _count(db, mid))
    assert n <= 1


async def _count(db, mid):
    return (await db.execute(select(func.count()).select_from(Protocol).where(Protocol.meeting_id == uuid.UUID(mid), Protocol.kind == "transcript"))).scalar_one()


# ---------------------------------------------------------------------------------------- видео
def test_two_finalizations_of_one_video_do_not_run_together(client, monkeypatch, tmp_path):
    """Webhook и сверка пришли вместе: второй запуск ffmpeg писал в тот же временный файл."""
    app = client.app_obj
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    rec_id = uuid.uuid4()
    rel = f"r/{rec_id}.mp4"
    full = Path(app.state.settings.recordings_path, rel)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"x" * 1000)

    async def add(db):
        db.add(Recording(id=rec_id, meeting_id=uuid.UUID(a["meeting_id"]), room_id=uuid.UUID(room["id"]), participant_identity="meeting", path=rel, size_bytes=0, kind="mix_video",
                         mime="video/mp4", has_video=True, status="finishing"))
        await db.commit()
    call(client, add)
    running, peak, lock = [0], [0], threading.Lock()

    def slow(path):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.3)
        with lock:
            running[0] -= 1
        return False
    monkeypatch.setattr(video_rec, "faststart", slow)
    monkeypatch.setattr(video_rec, "readable_duration", lambda p: 60)
    video = app.state.video
    video.export = None

    async def one():
        async with app.state.session_maker() as db:
            rec = await db.get(Recording, rec_id)
            await video.finalize(db, rec, None)

    async def both():
        await asyncio.gather(one(), one())
    client.portal.call(both)
    assert peak[0] == 1
    assert call(client, lambda db: db.get(Recording, rec_id)).status == "ready"


def test_faststart_uses_a_private_temporary_name(monkeypatch, tmp_path):
    seen = []

    class R:
        returncode = 1

    def run(cmd, **kw):
        seen.append(cmd[-1])
        return R()
    monkeypatch.setattr(video_rec.mixdown, "ffmpeg_path", lambda: "/bin/ffmpeg")
    monkeypatch.setattr(video_rec, "moov_first", lambda p: False)
    monkeypatch.setattr(video_rec.subprocess, "run", run)
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x")
    video_rec.faststart(p)
    video_rec.faststart(p)
    assert len(set(seen)) == 2


# ---------------------------------------------------------------------------------------- выгрузка
@pytest.mark.parametrize("delete_fails", [False, True])
def test_export_saves_the_result_before_removing_the_source(client, monkeypatch, tmp_path, delete_fails):
    app = client.app_obj
    ps = app.state.protocols
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    rec_id = uuid.uuid4()
    Path(app.state.settings.recordings_path, "x").mkdir(parents=True, exist_ok=True)
    src = Path(app.state.settings.recordings_path, "x/a.wav")
    src.write_bytes(b"w" * 100)

    async def add(db):
        db.add(Recording(id=rec_id, meeting_id=uuid.UUID(a["meeting_id"]), room_id=uuid.UUID(room["id"]), participant_identity="u", path="x/a.wav", size_bytes=100, kind="participant", mime="audio/wav"))
        await db.commit()
    call(client, add)

    class Store:
        def copy_in(self, rel, path):
            return "loc"

        def size_of(self, rel):
            return 100

    async def target(db, area):
        return True, False

    async def store(db, **kw):
        return Store()
    order = []

    def delete(root, rel):
        order.append("delete")
        if delete_fails:
            raise OSError("занято")
        return True
    monkeypatch.setattr(ps, "export_target", target)
    monkeypatch.setattr(ps, "storage_for", store)
    monkeypatch.setattr("app.services.protocols.delete_recording_file", delete)
    monkeypatch.setattr("app.services.transfer.sha256_storage", lambda *a, **k: __import__("hashlib").sha256(b"w" * 100).hexdigest())

    async def run():
        async with app.state.session_maker() as db:
            rec = await db.get(Recording, rec_id)
            real = db.commit

            async def commit():
                order.append("commit")
                await real()
            db.commit = commit
            await ps.export_recording(db, rec)
    client.portal.call(run)
    assert order[:2] == ["commit", "delete"]
    assert call(client, lambda db: db.get(Recording, rec_id)).export_status == "exported"


def test_streaming_routes_release_the_database_connection_first():
    """Поток записи длится минуты: соединение, не отданное до него, занимало пул на всё время просмотра."""
    import inspect
    from app.api import live, media
    for fn in (live.viewer_replay_file, media.stream_media):
        src = inspect.getsource(fn)
        assert src.rindex("await release(db)") > src.index("parse_range(") and "await release(db)" in src
