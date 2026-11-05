"""Перенос данных между двумя хранилищами: копирование, сверка, переключение, очистка. Источник не меняется до явного подтверждения."""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import ChatAttachment, Recording, StorageMigration, StorageMigrationItem
from app.services import storage_migration as sm
from app.services.storage import LocalStorage, StorageError, StreamStopped
from .conftest import make_room, put_settings, login
from .test_chat_files_storage import profile
from .test_transcripts import _join
from .test_video_recording import call

API = "/api/v1/admin/storage/migrations"


class Bench:
    def __init__(self, client, settings):
        self.c, self.s = client, settings
        login(client, "root")
        self.root_a = Path(settings.data_dir, "vol-a")
        self.root_b = Path(settings.data_dir, "vol-b")
        self.root_a.mkdir(parents=True)
        self.root_b.mkdir(parents=True)
        self.a = profile(client, settings, name="Старый том", config={"local_path": str(self.root_a)})
        self.b = profile(client, settings, name="Новый том", config={"local_path": str(self.root_b)})
        self.svc = client.app_obj.state.migrations
        self.svc.clean_grace_s = 0
        self.svc.wait_s = 0

    def file(self, rel: str, data: bytes = b"x" * 1000) -> Path:
        p = self.root_a / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def run(self, until: tuple[str, ...], limit: int = 60) -> dict:
        """Шаги фонового цикла, пока задание не дойдёт до нужного состояния."""
        for _ in range(limit):
            job = self.job()
            if job["state"] in until:
                return job
            self.c.portal.call(self.svc.step)
        raise AssertionError(f"задание застряло: {self.job()}")

    def create(self, groups=None) -> dict:
        r = self.c.post(API, json={"source_id": self.a["id"], "target_id": self.b["id"], "groups": groups})
        assert r.status_code == 201, r.text
        self.id = r.json()["id"]
        return r.json()

    def job(self) -> dict:
        return self.c.get(f"{API}/{self.id}").json()

    def start(self):
        r = self.c.post(f"{API}/{self.id}/start")
        assert r.status_code == 200, r.text

    def items(self):
        async def q(db):
            return list((await db.execute(select(StorageMigrationItem).where(StorageMigrationItem.migration_id == uuid.UUID(self.id)))).scalars())
        return call(self.c, q)


@pytest.fixture
def b(client, settings):
    return Bench(client, settings)


def use(b, group="audio_storage", **extra):
    put_settings(b.c, group, profile_id=b.a["id"], **extra)


def test_estimate_shows_breakdown_and_touches_nothing(b):
    b.file("Audio/Комната/запись.wav", b"a" * 2000)
    b.file("Video/Комната/video-1.mp4", b"v" * 3000)
    b.file("Protocols/Комната/протокол.docx", b"p" * 10)
    b.file("Chat/files/m1/f.png", b"c" * 5)
    b.file("Chat/Комната/chat.txt", b"t" * 7)
    b.file(".peregovorka-volume", "метка".encode())
    b.file("Audio/Комната/обрыв.wav.1234.tmp", b"t")
    b.create()
    j = b.run(("ready",))
    assert j["total"] == 5 and j["bytes_total"] == 5022
    cats = {c["key"]: c for c in j["estimate"]["categories"]}
    assert cats["audio"]["bytes"] == 2000 and cats["video"]["files"] == 1 and cats["chat_files"]["files"] == 1 and cats["chat"]["files"] == 1
    assert j["estimate"]["target_free"] > 0 and j["estimate"]["speed_bps"] > 0 and j["estimate"]["seconds"] is not None
    assert not (b.root_b / "Audio").exists(), "до подтверждения на цель ничего не пишется"


def test_full_move_copies_verifies_switches_then_cleans_only_after_confirmation(b):
    use(b, enabled=True)
    put_settings(b.c, "storage", enabled=True, profile_id=b.a["id"])
    data = os.urandom(3 << 20)
    src = b.file("Audio/Комната/запись.wav", data)
    os.utime(src, (1_700_000_000, 1_700_000_000))
    doc = b.file("Protocols/Комната/протокол.pdf", b"pdf")
    b.create()
    b.run(("ready",))
    b.start()
    j = b.run(("switched",))
    copy = b.root_b / "Audio/Комната/запись.wav"
    assert copy.read_bytes() == data and int(copy.stat().st_mtime) == 1_700_000_000, "содержимое и время файла сохранены"
    assert src.exists() and doc.exists(), "источник цел до очистки"
    assert j["switched_at"] and j["bytes_done"] == len(data) + 3
    assert all(i.sha256 == hashlib.sha256((b.root_a / i.path).read_bytes()).hexdigest() for i in b.items())

    async def cfg(db):
        return (await b.c.app_obj.state.settings_svc.get(db, "audio_storage")).profile_id, (await b.c.app_obj.state.settings_svc.get(db, "storage")).profile_id
    assert call(b.c, cfg) == (b.b["id"], b.b["id"]), "новые файлы пишутся уже на целевое хранилище"

    # очистка: только по подтверждению именем хранилища
    assert b.c.post(f"{API}/{b.id}/clean", json={"confirm": "не то"}).status_code == 409
    assert src.exists()
    assert b.c.post(f"{API}/{b.id}/clean", json={"confirm": "Старый том"}).status_code == 200
    j = b.run(("cleaned",))
    assert not src.exists() and copy.exists()
    assert j["report"]["freed_bytes"] == len(data) + 3 and j["report"]["removed_files"] == 2 and j["report"]["kept"] == []


def test_file_changed_after_copy_is_not_deleted_from_source(b):
    p = b.file("Audio/a.wav", b"1" * 100)
    b.create(["audio"])
    b.run(("ready",))
    b.start()
    b.run(("switched",))
    p.write_bytes(b"2" * 150)
    os.utime(p, (1_800_000_000, 1_800_000_000))
    # очистка сама докопирует изменившийся файл
    b.c.post(f"{API}/{b.id}/clean", json={"confirm": "Старый том"})
    j = b.run(("cleaned",))
    assert (b.root_b / "Audio/a.wav").read_bytes() == b"2" * 150 and j["report"]["removed_files"] == 1


def test_files_written_during_copying_are_picked_up_before_the_switch(b):
    b.file("Audio/old.wav")
    b.create(["audio"])
    b.run(("ready",))
    b.start()
    b.run(("verifying",))
    b.file("Audio/new.wav", b"n" * 50)
    j = b.run(("switched",))
    assert (b.root_b / "Audio/new.wav").exists() and j["total"] == 2


def test_selected_groups_leave_the_rest_alone(b):
    b.file("Audio/a.wav")
    v = b.file("Video/v.mp4")
    b.create(["audio"])
    b.run(("ready",))
    b.start()
    b.run(("switched",))
    b.c.post(f"{API}/{b.id}/clean", json={"confirm": "Старый том"})
    b.run(("cleaned",))
    assert not (b.root_b / "Video/v.mp4").exists() and v.exists()


def test_conflicting_file_on_target_is_never_replaced_silently(b):
    b.file("Audio/a.wav", b"source")
    (b.root_b / "Audio").mkdir()
    (b.root_b / "Audio/a.wav").write_bytes(b"other!")
    b.create(["audio"])
    b.run(("ready",))
    b.start()
    j = b.run(("attention",))
    assert j["failed"] == 1 and (b.root_b / "Audio/a.wav").read_bytes() == b"other!"
    assert "другой файл" in b.job()["problems"][0]["error"]
    assert b.c.post(f"{API}/{b.id}/retry", json={"overwrite": True}).status_code == 200
    b.run(("switched",))
    assert (b.root_b / "Audio/a.wav").read_bytes() == b"source"


def test_rerun_skips_identical_files(b, monkeypatch):
    b.file("Audio/a.wav", b"same")
    (b.root_b / "Audio").mkdir()
    (b.root_b / "Audio/a.wav").write_bytes(b"same")
    calls = []
    real = LocalStorage.write_stream
    monkeypatch.setattr(LocalStorage, "write_stream", lambda self, rel, chunks, mtime=None: (calls.append(rel), real(self, rel, chunks, mtime))[1])
    b.create(["audio"])
    b.run(("ready",))
    calls.clear()
    b.start()
    b.run(("switched",))
    assert calls == [], "готовая копия не перезаписывается"


def test_transient_error_is_retried_and_persistent_one_stops_before_the_switch(b, monkeypatch):
    b.file("Audio/a.wav")
    b.create(["audio"])
    b.run(("ready",))
    b.start()
    real, n = sm.copy_file, [0]

    def flaky(*a, **k):
        n[0] += 1
        if n[0] < 3:
            raise StorageError("сеть моргнула")
        return real(*a, **k)
    monkeypatch.setattr(sm, "copy_file", flaky)
    b.run(("switched",))
    assert n[0] == 3


def test_failed_copy_keeps_source_and_settings(b, monkeypatch):
    use(b, enabled=True)
    b.file("Audio/a.wav")
    b.create(["audio"])
    b.run(("ready",))
    b.start()

    def broken(*a, **k):
        raise StorageError("диск вернул ошибку")
    monkeypatch.setattr(sm, "copy_file", broken)
    j = b.run(("attention",))
    assert j["failed"] == 1 and (b.root_a / "Audio/a.wav").exists()

    async def cfg(db):
        return (await b.c.app_obj.state.settings_svc.get(db, "audio_storage")).profile_id
    assert call(b.c, cfg) == b.a["id"], "при ошибках переключения нет"
    assert b.c.post(f"{API}/{b.id}/clean", json={"confirm": "Старый том"}).status_code == 409, "очистки до переключения нет"


def test_unavailable_storage_waits_and_continues(b, monkeypatch):
    b.file("Audio/a.wav")
    b.create(["audio"])
    b.run(("ready",))
    b.start()
    down = [True]
    real = LocalStorage.probe

    def probe(self):
        if down[0] and "vol-b" in str(self._root):
            raise StorageError("том не отвечает")
        return real(self)
    monkeypatch.setattr(LocalStorage, "probe", probe)
    j = b.run(("waiting",))
    assert j["resume_state"] == "copying" and "том не отвечает" in j["error"]
    down[0] = False
    b.run(("switched",))


def test_cancel_during_copy_leaves_source_and_second_run_is_allowed(b):
    src = b.file("Audio/a.wav")
    b.create(["audio"])
    assert b.c.post(API, json={"source_id": b.a["id"], "target_id": b.b["id"]}).status_code == 409, "второй перенос одновременно не начинается"
    b.run(("ready",))
    b.start()
    assert b.c.post(f"{API}/{b.id}/cancel").status_code == 200
    j = b.run(("cancelled",))
    assert src.exists() and j["finished_at"]
    b.create(["audio"])


def test_interrupted_copy_leaves_no_partial_file():
    import threading
    stop = threading.Event()

    class Src:
        def stat_of(self, rel):
            return 3 << 20, 0.0

        def read_range(self, rel, a, b):
            yield b"x" * (1 << 20)
            stop.set()
            yield b"x" * (1 << 20)
            yield b"x" * (1 << 20)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        dst = LocalStorage(d)
        with pytest.raises(StreamStopped):
            sm.copy_file(Src(), dst, "a.bin", 0.0, overwrite=False, stop=stop)
        assert os.listdir(d) == []


def test_large_file_is_copied_in_chunks_not_read_into_memory():
    import threading

    class Src:
        peak = 0

        def stat_of(self, rel):
            return 64 << 20, 0.0

        def read_range(self, rel, a, b):
            for _ in range(64):
                chunk = b"z" * (1 << 20)
                Src.peak = max(Src.peak, len(chunk))
                yield chunk
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        dst = LocalStorage(d)
        digest, size = sm.copy_file(Src(), dst, "big.bin", 0.0, overwrite=False, stop=threading.Event())
        assert size == 64 << 20 and Src.peak == 1 << 20 and digest == hashlib.sha256(b"z" * (64 << 20)).hexdigest()


def test_switch_moves_links_of_recordings_and_chat_attachments(b):
    use(b, enabled=True)
    room = make_room(b.c)
    a = _join(b.c, "alice", room["id"])
    login(b.c, "root")
    b.file("Audio/К/a.wav", b"w" * 100)
    b.file("Chat/files/m/f.png", b"p" * 10)
    rid, aid = uuid.uuid4(), uuid.uuid4()
    old = str(b.root_a / "Audio/К/a.wav")

    async def add(db):
        db.add(Recording(id=rid, meeting_id=uuid.UUID(a["meeting_id"]), room_id=uuid.UUID(room["id"]), participant_identity="u", path="К/a.wav", size_bytes=100, kind="participant", mime="audio/wav",
                         export_status="exported", export_area="audio", export_profile_id=uuid.UUID(b.a["id"]), export_location=old))
        db.add(ChatAttachment(id=aid, meeting_id=uuid.UUID(a["meeting_id"]), name="f.png", mime="image/png", size=10, kind="image", storage_key="m/f.png", profile_id=uuid.UUID(b.a["id"]), uploader_type="user", uploader_id=uuid.uuid4()))
        await db.commit()
    call(b.c, add)
    b.create(["audio", "chat_files"])
    b.run(("ready",))
    b.start()
    b.run(("switched",))

    async def read(db):
        rec, att = await db.get(Recording, rid), await db.get(ChatAttachment, aid)
        ps = b.c.app_obj.state.protocols
        size, reader = await ps.media_source(db, rec)
        return rec.export_profile_id, rec.export_location, att.profile_id, size, b"".join(reader(0, size - 1))
    prof, loc, att_prof, size, content = call(b.c, read)
    assert str(prof) == b.b["id"] and str(att_prof) == b.b["id"] and "vol-b" in loc and size == 100 and content == b"w" * 100
    # источник можно очистить и удалить: воспроизведение идёт с новой копии
    b.c.post(f"{API}/{b.id}/clean", json={"confirm": "Старый том"})
    b.run(("cleaned",))
    assert not (b.root_a / "Audio/К/a.wav").exists() and call(b.c, read)[4] == b"w" * 100


def test_failed_acceptance_returns_everything_to_the_source(b, monkeypatch):
    use(b, enabled=True)
    room = make_room(b.c)
    a = _join(b.c, "alice", room["id"])
    login(b.c, "root")
    rid = uuid.uuid4()
    b.file("Audio/a.wav", b"w" * 10)

    async def add(db):
        db.add(Recording(id=rid, meeting_id=uuid.UUID(a["meeting_id"]), room_id=uuid.UUID(room["id"]), participant_identity="u", path="a.wav", size_bytes=10, kind="participant", mime="audio/wav",
                         export_status="exported", export_area="audio", export_profile_id=uuid.UUID(b.a["id"])))
        await db.commit()
    call(b.c, add)

    async def nope(job_id):
        return "запись на новое хранилище не прошла"
    monkeypatch.setattr(b.svc, "_acceptance", nope)
    b.create(["audio"])
    b.run(("ready",))
    b.start()
    j = b.run(("attention",))
    assert "не прошла" in j["error"] and j["switched_at"] is None

    async def state(db):
        return (await b.c.app_obj.state.settings_svc.get(db, "audio_storage")).profile_id, (await db.get(Recording, rid)).export_profile_id
    pid, rec_pid = call(b.c, state)
    assert pid == b.a["id"] and str(rec_pid) == b.a["id"]
    assert (b.root_a / "Audio/a.wav").exists()


def test_scan_restart_after_crash_does_not_duplicate(b):
    b.file("Audio/a.wav")
    b.create(["audio"])

    async def half(db):
        db.add(StorageMigrationItem(migration_id=uuid.UUID(b.id), path="Audio/a.wav", category="audio", size=1))
        await db.commit()
    call(b.c, half)
    j = b.run(("ready",))
    assert j["total"] == 1 and len(b.items()) == 1


def test_only_admin_reaches_the_api(client, settings):
    login(client, "alice")
    assert client.get(API).status_code == 403


def test_same_source_and_target_are_refused(b):
    assert b.c.post(API, json={"source_id": b.a["id"], "target_id": b.a["id"]}).status_code == 409


def test_folder_categories():
    assert sm.category_of("Chat/files/m/a.png") == "chat_files" and sm.category_of("Chat/К/chat.txt") == "chat" and sm.category_of("ConfigBackups/a.pgbak") == "backups"
    assert sm.category_of("Что-то/ещё.bin") == "other" and not sm.scanned(".peregovorka-volume") and not sm.scanned("Audio/a.partial")


def test_events_are_kept_for_the_journal(b):
    b.file("Audio/a.wav")
    b.create(["audio"])
    b.run(("ready",))
    assert any("Оценка" in e["text"] for e in b.job()["events"])
    assert asyncio.iscoroutinefunction(b.svc.step) and StorageMigration
