"""Видеозаписи в общей системе хранения: подпапка Video/ в уже подключённом хранилище, своё хранилище в разделе «Видеозапись», проверка копии перед удалением
локального файла, сбой доставки и повтор, старые видеозаписи в Audio/, перенос. Внешнее хранилище — папка-профиль (настоящий SMB здесь не проверяется)."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select

from app.models import Recording, utcnow
from app.services.storage import StorageError

from .conftest import login, make_settings, put_settings, running_app
from .test_meeting_media import _meeting_with_recordings
from .test_storage_transfer import _external_volume_is_mounted, _fast, db_run, process, start  # noqa: F401 — автофикстуры переноса

MP4 = b"\x00\x00\x00\x20ftypisom" + b"v" * 4096


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def profile(c, s, folder: str, name: str) -> str:
    login(c, "root")
    p = c.post("/api/v1/admin/storages", json={"name": name, "kind": "local", "config": {"local_path": str(Path(s.data_dir) / folder), "external_volume": True}}).json()
    assert c.post(f"/api/v1/admin/storages/{p['id']}/test").status_code == 200
    return p["id"]


def add_video(c, s, mid: str, n: int = 1, *, exported_area: str | None = None, fs: Path | None = None) -> uuid.UUID:
    """Готовая видеозапись встречи (как после финализации Egress): файл на томе записей или — для «старой» — уже в хранилище."""
    async def go(db):
        any_rec = (await db.execute(select(Recording).where(Recording.meeting_id == uuid.UUID(mid)))).scalars().first()
        rel = f"{any_rec.path.rsplit('/', 1)[0]}/video-{n}.mp4"
        if exported_area:
            dst = fs / ("Audio" if exported_area == "audio" else "Video") / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(MP4)
        else:
            full = Path(s.recordings_path) / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(MP4)
        r = Recording(meeting_id=any_rec.meeting_id, room_id=any_rec.room_id, participant_identity="meeting", path=rel, size_bytes=len(MP4), kind="mix_video", mime="video/mp4",
                      has_video=True, status="ready", export_status="exported" if exported_area else "local", export_area=exported_area, sha256=sha(MP4) if exported_area else None,
                      exported_at=utcnow() if exported_area else None)
        db.add(r)
        await db.commit()
        return r.id
    return db_run(c, go)


def export(c, rid):
    async def go(db):
        rec = await db.get(Recording, rid)
        await c.app_obj.state.protocols.export_recording(db, rec)
        await db.refresh(rec)
        db.expunge(rec)
        return rec
    return db_run(c, go)


def download(c, mid, rid) -> bytes:
    login(c, "root")
    r = c.get(f"/api/v1/meetings/{mid}/recordings/{rid}")
    assert r.status_code == 200, r.text
    return r.content


def test_video_goes_to_video_folder_of_the_audio_storage_by_default_and_keeps_the_local_copy(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        pid = profile(c, s, "fs", "Файловый сервер")
        put_settings(c, "audio_storage", enabled=True, profile_id=pid)
        rid = add_video(c, s, mid)
        rec = export(c, rid)
        dst = Path(s.data_dir) / "fs" / "Video" / rec.path
        assert rec.export_status == "exported" and rec.export_area == "video" and rec.sha256 == sha(MP4)
        assert dst.read_bytes() == MP4 and (Path(s.recordings_path) / rec.path).is_file(), "копия в Video/, локальная оставлена (по умолчанию)"
        assert not (Path(s.data_dir) / "fs" / "Audio" / rec.path).exists(), "видео не смешивается с аудио"
        storages = c.get("/api/v1/admin/storages").json()["items"]
        assert "Видеозаписи" in storages[0]["used_by"] and "Аудиозаписи" in storages[0]["used_by"]
        assert download(c, mid, rid) == MP4


def test_own_storage_for_video_and_local_file_is_removed_only_after_a_verified_copy(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        audio_pid, video_pid = profile(c, s, "fs", "Аудио"), profile(c, s, "vfs", "Видео")
        put_settings(c, "audio_storage", enabled=True, profile_id=audio_pid)
        put_settings(c, "video", storage="profile", profile_id=video_pid, keep_local_copy=False)
        rid = add_video(c, s, mid)
        rec = export(c, rid)
        assert (Path(s.data_dir) / "vfs" / "Video" / rec.path).read_bytes() == MP4
        assert not (Path(s.recordings_path) / rec.path).exists(), "исходник удалён после сверки размера и SHA-256"
        assert download(c, mid, rid) == MP4, "просмотр и скачивание — из хранилища"
        # выбранное хранилище обязательно существует; «выбранное» без хранилища не сохраняется
        login(c, "root")
        assert c.put("/api/v1/admin/settings/video", json={"storage": "profile", "profile_id": ""}).status_code == 422
        assert c.put("/api/v1/admin/settings/video", json={"storage": "profile", "profile_id": str(uuid.uuid4())}).status_code in (404, 422)


def test_storage_failure_keeps_the_file_locally_and_the_retry_delivers_it(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        pid = profile(c, s, "fs", "Файловый сервер")
        put_settings(c, "audio_storage", enabled=True, profile_id=pid)
        put_settings(c, "video", keep_local_copy=False)
        files = c.app_obj.state.files
        real = files.video_backend

        async def down(db):
            raise StorageError("сетевой ресурс недоступен")
        monkeypatch.setattr(files, "video_backend", down)
        rid = add_video(c, s, mid)
        rec = export(c, rid)
        assert rec.export_status == "failed" and "недоступ" in rec.export_error and (Path(s.recordings_path) / rec.path).read_bytes() == MP4, "файл не потерян"
        login(c, "root")
        monkeypatch.setattr(files, "video_backend", real)
        r = c.post("/api/v1/admin/recordings/retry-exports")
        assert r.status_code == 200 and r.json()["exported"] >= 1
        got = db_run(c, lambda db: db.get(Recording, rid))
        assert got.export_status == "exported" and got.export_area == "video" and (Path(s.data_dir) / "fs" / "Video" / got.path).read_bytes() == MP4


def test_old_video_in_audio_folder_stays_readable_and_moves_back_to_local(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        pid = profile(c, s, "fs", "Файловый сервер")
        put_settings(c, "audio_storage", enabled=True, profile_id=pid)
        rid = add_video(c, s, mid, exported_area="audio", fs=Path(s.data_dir) / "fs")      # выгружена до 0.52.0 — лежит в Audio/
        assert download(c, mid, rid) == MP4
        job = process(c, start(c, "to_local", mid))
        assert job["state"] == "done"
        got = db_run(c, lambda db: db.get(Recording, rid))
        assert got.export_status == "local" and (Path(s.recordings_path) / got.path).read_bytes() == MP4
        job = process(c, start(c, "to_external", mid))
        got = db_run(c, lambda db: db.get(Recording, rid))
        assert job["state"] == "done" and got.export_status == "exported" and got.export_area == "video", "обратно — уже в хранилище видеозаписей"
        assert (Path(s.data_dir) / "fs" / "Video" / got.path).read_bytes() == MP4
