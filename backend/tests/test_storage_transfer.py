"""Перенос записей между локальным диском и внешним хранилищем и показатели хранилища.

Внешнее хранилище в тестах — папка-профиль (с меткой тома); настоящий SMB здесь не проверяется: проверяются логика, порядок шагов и сбои, но не сетевые обрывы.
Главное требование: при любом сбое и «перезапуске» остаётся хотя бы одна проверенная копия файла, а запись в базе указывает на существующую.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.models import Meeting, Recording, StorageTransferItem, utcnow
from app.services import transfer as tr
from app.services.storage import LocalStorage, StorageError

from .conftest import login, make_settings, put_settings, running_app
from .test_meeting_media import _meeting_with_recordings, needs_ffmpeg


class Crash(BaseException):
    """«Падение процесса»: не перехватывается обычными обработчиками, задание остаётся в состоянии «выполняется»."""


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(tr, "SETTLE_S", 0)
    monkeypatch.setattr(tr, "RECENT_S", 0)
    monkeypatch.setattr(tr, "GRACE_S", 0)
    monkeypatch.setattr(tr, "RESERVE", 0)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def db_run(c, fn):
    async def go():
        async with c.app_obj.state.session_maker() as db:
            return await fn(db)
    return c.portal.call(go)


def recs(c) -> list[Recording]:
    async def q(db):
        rows = (await db.execute(select(Recording).order_by(Recording.created_at, Recording.id))).scalars().all()
        for r in rows:
            db.expunge(r)
        return rows
    return db_run(c, q)


def world(c, s, *, mix: bool = False):
    """Встреча с записями на локальном диске; затем подключается внешнее хранилище (папка-профиль, проверенная кнопкой «Проверить»)."""
    mid = _meeting_with_recordings(c, s)
    login(c, "root")
    p = c.post("/api/v1/admin/storages", json={"name": "Файловый сервер", "kind": "local", "config": {"local_path": str(Path(s.data_dir) / "fs")}}).json()
    t = c.post(f"/api/v1/admin/storages/{p['id']}/test")
    assert t.status_code == 200, t.text
    put_settings(c, "audio_storage", enabled=True, profile_id=p["id"], keep_local_copy=False)
    return mid, Path(s.data_dir) / "fs", p["id"]


def svc(c) -> tr.TransferService:
    return c.app_obj.state.transfers


def start(c, direction: str, meeting_id: str | None = None) -> str:
    login(c, "root")
    r = c.post("/api/v1/admin/storage/transfers", json={"direction": direction, "meeting_id": meeting_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def process(c, job_id: str, sweep: bool = True) -> dict:
    import uuid

    c.portal.call(lambda: svc(c).process(uuid.UUID(job_id)))
    if sweep:
        c.portal.call(lambda: svc(c).sweep())
    login(c, "root")
    return c.get(f"/api/v1/admin/storage/transfers/{job_id}").json()


def restart(c) -> None:
    """«Перезапуск сервера»: задания в состоянии «выполняется» возвращаются в очередь."""
    c.portal.call(lambda: svc(c).recover())


def make_due(c) -> None:
    async def go(db):
        await db.execute(update(StorageTransferItem).where(StorageTransferItem.cleanup_at.is_not(None)).values(cleanup_at=utcnow()))
        await db.commit()
    db_run(c, go)


def ext_files(fs: Path) -> list[Path]:
    return sorted(p for p in (fs / "Audio").rglob("*") if p.is_file() and not p.name.endswith(".tmp")) if (fs / "Audio").exists() else []


def local_files(s) -> list[Path]:
    return sorted(p for p in Path(s.recordings_path).rglob("*.wav"))


def fixed(c, s):
    """Хотя бы одна проверенная копия: у каждой записи файл, на который указывает база, существует и совпадает по контрольной сумме (если она известна)."""
    out = []
    for r in recs(c):
        loc = Path(s.recordings_path) / r.path
        ext = Path(s.data_dir) / "fs" / "Audio" / r.path
        target = ext if r.export_status == "exported" else loc
        assert target.is_file(), f"запись указывает на несуществующий файл: {r.path} ({r.export_status})"
        if r.sha256:
            assert sha(target) == r.sha256, f"копия не совпала с контрольной суммой: {r.path}"
        out.append((r, target))
    return out


# ----------------------------------------------------------------------------------------------------- основной путь
def test_transfer_there_and_back_verifies_files_switches_the_db_and_keeps_ids_and_downloads(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        before = {r.id: sha(Path(s.recordings_path) / r.path) for r in recs(c)}
        assert len(before) == 2 and not ext_files(fs)
        job = process(c, start(c, "to_external"))
        assert job["state"] == "done" and job["done"] == 2 and job["failed"] == 0 and job["cleanup_pending"] == 0
        rows = recs(c)
        assert {r.export_status for r in rows} == {"exported"} and all(r.sha256 == before[r.id] for r in rows), "контрольная сумма записана и совпала"
        assert not local_files(s) and len(ext_files(fs)) == 2, "источник удалён только после переключения"
        login(c, "root")
        for r in rows:                                                    # ссылки из истории прежние: тот же id, содержимое то же
            dl = c.get(f"/api/v1/meetings/{mid}/recordings/{r.id}")
            assert dl.status_code == 200 and hashlib.sha256(dl.content).hexdigest() == before[r.id]
        back = process(c, start(c, "to_local"))
        assert back["state"] == "done" and back["done"] == 2
        assert {r.export_status for r in recs(c)} == {"local"} and len(local_files(s)) == 2 and not ext_files(fs)
        for r in recs(c):
            assert c.get(f"/api/v1/meetings/{mid}/recordings/{r.id}").status_code == 200
        acts = [a["action"] for a in c.get("/api/v1/admin/audit").json()]
        assert acts.count("storage.transfer.start") == 2 and "storage.transfer.finish" in acts
        assert c.get("/api/v1/admin/storage/transfers").json()[0]["state"] == "done"


def test_only_admin_can_use_transfer_and_second_job_is_refused_while_one_runs(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        login(c, "alice")
        assert c.get("/api/v1/admin/storage/transfers").status_code == 403
        assert c.post("/api/v1/admin/storage/transfers", json={"direction": "to_external"}).status_code == 403
        assert c.get("/api/v1/admin/storage/stats").status_code == 403
        job = start(c, "to_external", mid)
        assert c.post("/api/v1/admin/storage/transfers", json={"direction": "to_local"}).status_code == 409
        assert process(c, job)["scope"] == "meeting"


def test_transfer_needs_a_configured_external_storage(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        _meeting_with_recordings(c, s)
        login(c, "root")
        r = c.post("/api/v1/admin/storage/transfers", json={"direction": "to_external"})
        assert r.status_code == 409 and "не настроено" in r.text


# ----------------------------------------------------------------------------------------------------- недоступность и пустая точка монтирования
def test_unmounted_folder_is_never_written_to_and_the_job_can_be_resumed(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        away = tmp_path / "fs-away"
        shutil.move(str(fs), str(away))
        fs.mkdir()                                                          # «отвалившаяся шара»: на месте точки монтирования пустой локальный каталог
        job = process(c, start(c, "to_external"))
        assert job["state"] == "failed" and "метки тома" in (job["error"] or ""), job
        assert list(fs.rglob("*")) == [], "в пустую точку монтирования ничего не записано"
        assert {r.export_status for r in recs(c)} == {"local"} and len(local_files(s)) == 2
        fixed(c, s)
        # обычная выгрузка при завершении встречи тоже не пишет в пустой каталог
        assert not any(fs.rglob("*"))
        shutil.rmtree(fs)
        shutil.move(str(away), str(fs))                                    # шара вернулась
        again = c.post(f"/api/v1/admin/storage/transfers/{job['id']}/resume")
        assert again.status_code == 200
        done = process(c, job["id"])
        assert done["state"] == "done" and done["done"] == 2
        assert len(ext_files(fs)) == 2 and not local_files(s)
        fixed(c, s)


def test_export_on_meeting_end_does_not_write_to_an_unmounted_folder(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        login(c, "root")
        fs = Path(s.data_dir) / "fs"
        p = c.post("/api/v1/admin/storages", json={"name": "Файловый сервер", "kind": "local", "config": {"local_path": str(fs)}}).json()
        assert c.post(f"/api/v1/admin/storages/{p['id']}/test").status_code == 200
        put_settings(c, "audio_storage", enabled=True, profile_id=p["id"], keep_local_copy=False)
        shutil.rmtree(fs)
        fs.mkdir()                                                          # том не смонтирован
        _meeting_with_recordings(c, s)
        assert list(fs.rglob("*")) == [], "файлы не ушли в пустую точку монтирования"
        rows = recs(c)
        assert {r.export_status for r in rows} == {"failed"} and len(local_files(s)) == 2, "записи остались локально и будут выгружены повторно"
        fixed(c, s)


def test_no_space_stops_the_job_without_copying_anything(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        monkeypatch.setattr(LocalStorage, "volume", lambda self: (1000, 10))
        monkeypatch.setattr(tr, "RESERVE", 1 << 20)
        job = process(c, start(c, "to_external"))
        assert job["state"] == "failed" and "не хватает места" in job["error"]
        assert not ext_files(fs) and len(local_files(s)) == 2
        fixed(c, s)


def test_checksum_mismatch_removes_the_bad_copy_and_keeps_the_source(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        monkeypatch.setattr(tr, "sha256_storage", lambda *a, **k: "0" * 64)
        job = process(c, start(c, "to_external"))
        assert job["state"] == "done" and job["failed"] == 2 and job["done"] == 0
        assert "контрольная сумма" in job["problems"][0]["error"]
        assert not ext_files(fs), "испорченная копия на назначении удалена"
        assert {r.export_status for r in recs(c)} == {"local"} and len(local_files(s)) == 2
        fixed(c, s)


# ----------------------------------------------------------------------------------------------------- сбои и «перезапуск» в каждой точке
def test_crash_in_the_middle_of_copying_leaves_the_source_and_the_job_resumes(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        real = LocalStorage.copy_in

        def torn(self, rel, src):
            full = self._full(rel)
            full.parent.mkdir(parents=True, exist_ok=True)
            full.with_name(full.name + ".part.tmp").write_bytes(Path(src).read_bytes()[:100])      # недокопированный файл
            raise Crash()

        monkeypatch.setattr(LocalStorage, "copy_in", torn)
        job_id = start(c, "to_external")
        with pytest.raises(Crash):
            process(c, job_id, sweep=False)
        assert {r.export_status for r in recs(c)} == {"local"} and len(local_files(s)) == 2, "источник цел, база указывает на него"
        fixed(c, s)
        monkeypatch.setattr(LocalStorage, "copy_in", real)
        restart(c)
        done = process(c, job_id)
        assert done["state"] == "done" and done["done"] == 2
        assert {r.export_status for r in recs(c)} == {"exported"} and len(ext_files(fs)) == 2 and not local_files(s)
        fixed(c, s)


def test_crash_after_copy_but_before_switching_the_db_keeps_both_and_resume_finishes(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        real = tr.sha256_storage

        def crash_after_verify(storage, rel, size):
            real(storage, rel, size)
            raise Crash()                                                  # копия записана и проверена, база ещё не переключена

        monkeypatch.setattr(tr, "sha256_storage", crash_after_verify)
        job_id = start(c, "to_external")
        with pytest.raises(Crash):
            process(c, job_id, sweep=False)
        assert {r.export_status for r in recs(c)} == {"local"} and len(local_files(s)) == 2 and len(ext_files(fs)) == 1
        fixed(c, s)
        monkeypatch.setattr(tr, "sha256_storage", real)
        restart(c)
        done = process(c, job_id)
        assert done["state"] == "done" and done["done"] == 2 and len(ext_files(fs)) == 2 and not local_files(s)
        fixed(c, s)


def test_crash_after_switching_the_db_but_before_deleting_the_source_keeps_both_copies_and_cleanup_removes_the_extra(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        monkeypatch.setattr(tr, "GRACE_S", 3600)                           # источник не удаляется сразу
        job_id = start(c, "to_external")
        job = process(c, job_id)
        assert job["state"] == "done" and job["cleanup_pending"] == 2
        assert {r.export_status for r in recs(c)} == {"exported"} and len(local_files(s)) == 2 and len(ext_files(fs)) == 2, "до уборки есть обе копии"
        fixed(c, s)
        # «перезапуск»: ожидающая уборка хранится в базе и выполняется, когда подошёл срок
        assert c.portal.call(lambda: svc(c).sweep()) == 0, "срок ещё не наступил"
        make_due(c)
        assert c.portal.call(lambda: svc(c).sweep()) == 2
        assert not local_files(s) and len(ext_files(fs)) == 2
        fixed(c, s)


def test_cleanup_never_removes_the_source_if_the_new_copy_is_not_intact(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        monkeypatch.setattr(tr, "GRACE_S", 3600)
        process(c, start(c, "to_external"))
        victim = ext_files(fs)[0]
        victim.write_bytes(victim.read_bytes()[:10])                       # копию во внешнем хранилище кто-то испортил
        make_due(c)
        removed = c.portal.call(lambda: svc(c).sweep())
        assert removed == 1, "удалён только источник той записи, чья копия цела"
        assert len(local_files(s)) == 1, "источник испорченной копии остался"


def test_crash_while_copying_back_and_after_replace_before_the_switch(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        process(c, start(c, "to_external"))
        assert not local_files(s)
        real_replace = tr.os.replace

        def replace_then_crash(a, b):
            real_replace(a, b)
            raise Crash()                                                  # локальный файл уже на месте, база ещё указывает на внешнее хранилище

        monkeypatch.setattr(tr.os, "replace", replace_then_crash)
        job_id = start(c, "to_local")
        with pytest.raises(Crash):
            process(c, job_id, sweep=False)
        assert {r.export_status for r in recs(c)} == {"exported"} and len(ext_files(fs)) == 2
        fixed(c, s)
        monkeypatch.setattr(tr.os, "replace", real_replace)
        restart(c)
        done = process(c, job_id)
        assert done["state"] == "done" and {r.export_status for r in recs(c)} == {"local"} and len(local_files(s)) == 2 and not ext_files(fs)
        fixed(c, s)


def test_job_survives_restart_and_does_not_copy_finished_files_again(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        calls = []
        real = LocalStorage.copy_in

        def counting(self, rel, src):
            calls.append(rel)
            if len(calls) == 2:
                raise Crash()
            return real(self, rel, src)

        monkeypatch.setattr(LocalStorage, "copy_in", counting)
        job_id = start(c, "to_external")
        with pytest.raises(Crash):
            process(c, job_id, sweep=False)
        restart(c)
        done = process(c, job_id)
        assert done["state"] == "done" and done["done"] == 2
        assert len(calls) == 3 and len(set(calls)) == 2 and calls.count(calls[0]) == 1, "первый файл (уже перенесён) повторно не копировался"
        fixed(c, s)


def test_reverse_job_started_before_cleanup_does_not_lose_the_only_copy(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        monkeypatch.setattr(tr, "GRACE_S", 3600)
        process(c, start(c, "to_external"))
        process(c, start(c, "to_local"))
        assert {r.export_status for r in recs(c)} == {"local"}
        make_due(c)
        c.portal.call(lambda: svc(c).sweep())
        assert len(local_files(s)) == 2 and not ext_files(fs), "последняя команда — «вернуть на диск»: файлы на диске, во внешнем хранилище их нет"
        fixed(c, s)


# ----------------------------------------------------------------------------------------------------- активные файлы
def test_files_in_use_are_skipped_not_moved(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        import uuid

        ps = c.app_obj.state.protocols
        ps.hold(uuid.UUID(mid))                                            # финализация/сведение/выгрузка по встрече ещё идут
        job = process(c, start(c, "to_external"))
        assert job["state"] == "done" and job["skipped"] == 2 and job["done"] == 0 and not ext_files(fs)
        ps.release(uuid.UUID(mid))
        # недавно изменённый файл
        monkeypatch.setattr(tr, "RECENT_S", 3600)
        assert process(c, start(c, "to_external"))["skipped"] == 2
        monkeypatch.setattr(tr, "RECENT_S", 0)
        # встреча «устоялась» не сразу
        monkeypatch.setattr(tr, "SETTLE_S", 3600)
        assert process(c, start(c, "to_external"))["skipped"] == 2
        monkeypatch.setattr(tr, "SETTLE_S", 0)
        # встреча ещё идёт
        async def reopen(db):
            await db.execute(update(Meeting).where(Meeting.id == uuid.UUID(mid)).values(ended_at=None))
            await db.commit()
        db_run(c, reopen)
        assert process(c, start(c, "to_external"))["skipped"] == 2
        assert not ext_files(fs) and len(local_files(s)) == 2
        fixed(c, s)


@needs_ffmpeg
def test_mix_being_built_blocks_transfer_of_the_meeting_files(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)

        async def freeze(db):
            await db.execute(update(Recording).where(Recording.kind == "mix_audio").values(status="processing"))
            await db.commit()
        db_run(c, freeze)
        job = process(c, start(c, "to_external"))
        assert job["done"] == 0 and job["skipped"] >= 2, "пока общая запись собирается, файлы встречи не переносятся"
        assert len(local_files(s)) == 2


# ----------------------------------------------------------------------------------------------------- воспроизведение во время переноса
@needs_ffmpeg
def test_listening_is_not_interrupted_and_the_link_stays_the_same(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        login(c, "alice")
        mix = c.get(f"/api/v1/meetings/{mid}/media").json()["mixes"][0]
        url = f"/api/v1/meetings/{mid}/media/{mix['id']}/stream"
        original = c.get(url).content
        assert hashlib.sha256(original).hexdigest()
        monkeypatch.setattr(tr, "GRACE_S", 3600)
        process(c, start(c, "to_external"))                               # база уже указывает на внешнее хранилище, локальная копия ещё лежит
        login(c, "alice")
        assert c.get(url).content == original, "та же ссылка, тот же файл"
        part = c.get(url, headers={"Range": "bytes=10-99"})
        assert part.status_code == 206 and part.content == original[10:100]
        # файл переехал между проверкой и чтением: ответ дочитывается из нового места, а не обрывается
        rec = next(r for r in recs(c) if r.kind == "mix_audio")

        async def src(db):
            return await c.app_obj.state.protocols.media_source(db, rec)

        size, reader = c.portal.call(lambda: db_run_async(c, src))
        (Path(s.recordings_path) / rec.path).unlink()
        assert b"".join(reader(0, size - 1)) == original
        make_due(c)
        c.portal.call(lambda: svc(c).sweep())
        assert c.get(url).content == original, "после удаления источника ссылка работает по-прежнему"


async def db_run_async(c, fn):
    async with c.app_obj.state.session_maker() as db:
        return await fn(db)


# ----------------------------------------------------------------------------------------------------- показатели хранилища
def test_stats_are_a_background_snapshot_and_show_db_and_real_disk_usage(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        login(c, "root")
        first = c.get("/api/v1/admin/storage/stats").json()
        assert first["measured_at"] is None and first["volumes"] == [], "до первого замера страница ничего не считает"
        c.portal.call(lambda: c.app_obj.state.storage_stats.refresh())
        d = c.get("/api/v1/admin/storage/stats").json()
        vols = {v["id"]: v for v in d["volumes"]}
        assert vols["local"]["state"] == "ok" and vols["local"]["files"]["participant"]["count"] == 2 and vols["local"]["total"] and vols["local"]["free"] is not None
        assert vols["external"]["files"]["participant"]["count"] == 0 and vols["external"]["state"] == "ok"
        assert vols["local"]["disk_bytes"] == vols["local"]["db_bytes"] and not vols["local"]["mismatch"]
        process(c, start(c, "to_external"))
        c.portal.call(lambda: c.app_obj.state.storage_stats.refresh())
        vols = {v["id"]: v for v in c.get("/api/v1/admin/storage/stats").json()["volumes"]}
        assert vols["external"]["files"]["participant"]["count"] == 2 and vols["local"]["files"]["participant"]["count"] == 0
        assert vols["external"]["disk_bytes"] == vols["external"]["db_bytes"] > 0
        # осиротевший файл: в базе его нет, а место занимает — показатели расходятся и это видно
        (Path(s.recordings_path) / "orphan.bin").write_bytes(b"x" * (3 << 20))
        c.portal.call(lambda: c.app_obj.state.storage_stats.refresh())
        loc = {v["id"]: v for v in c.get("/api/v1/admin/storage/stats").json()["volumes"]}["local"]
        assert loc["mismatch"] is True and loc["disk_bytes"] - loc["db_bytes"] >= 3 << 20


def test_stats_keep_old_numbers_marked_stale_when_the_storage_is_unavailable(tmp_path, directory):
    s = make_settings(tmp_path, meeting_mix_enabled=False)
    with running_app(s, directory) as c:
        mid, fs, _ = world(c, s)
        c.portal.call(lambda: c.app_obj.state.storage_stats.refresh())
        login(c, "root")
        ok = {v["id"]: v for v in c.get("/api/v1/admin/storage/stats").json()["volumes"]}["external"]
        assert ok["state"] == "ok" and ok["total"]
        shutil.move(str(fs), str(tmp_path / "gone"))
        c.portal.call(lambda: c.app_obj.state.storage_stats.refresh())
        bad = {v["id"]: v for v in c.get("/api/v1/admin/storage/stats").json()["volumes"]}["external"]
        assert bad["state"] == "unavailable" and bad["stale"] is True and bad["total"] == ok["total"] and bad["last_ok_at"] == ok["last_ok_at"]
        assert c.post("/api/v1/admin/storage/stats/refresh").status_code == 202
