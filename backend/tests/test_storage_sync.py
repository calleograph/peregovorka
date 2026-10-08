"""Сверка метаданных базы с реальным хранилищем: пропавшие файлы, неизвестные файлы, недоступное хранилище, защита от «пропало всё», расписание."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import ChatAttachment, Meeting, StorageSyncRun
from app.services.storage import LocalStorage
from app.workers.storage_sync import due, run_if_due

from .conftest import login, make_room, put_settings
from .test_admin_features import _drain
from .test_transcripts import _join

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def prof(client, settings):
    login(client, "root")
    p = client.post("/api/v1/admin/storages", json={"name": "Файловый сервер", "kind": "local", "config": {"local_path": str(Path(settings.data_dir) / "fs")}}).json()
    put_settings(client, "storage", enabled=True, profile_id=p["id"])
    put_settings(client, "audio_storage", enabled=True, profile_id=p["id"], keep_local_copy=False)
    put_settings(client, "chat_files", profile_id=p["id"])
    return Path(settings.data_dir) / "fs"


def finished_meeting(client, settings, name="Планёрка"):
    """Встреча с записью аудио, чатом и вложениями; встреча завершена, материалы выгружены в хранилище."""
    client.app_obj.state.protocols.flush_delay = 0
    room = make_room(client, name=name, record_audio=True)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    pcm = Path(settings.recordings_path) / a["livekit_room"]
    pcm.mkdir(parents=True)
    (pcm / f"{a['identity']}.pcm").write_bytes(b"\x02\x00" * 16000 * 3)
    ids = []
    for fname, data in (("схема.png", PNG), ("отчёт.txt", b"hello")):
        r = client.post(f"/api/v1/meetings/{mid}/chat/attachments", params={"name": fname}, content=data)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    assert client.post(f"/api/v1/meetings/{mid}/chat", json={"text": "материалы", "attachments": ids}).status_code == 201
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    _drain(client)
    return mid, ids


def sync(client, force=False, guard=95):
    rid = client.portal.call(lambda: client.app_obj.state.reconciler.run("manual", "тест", force=force, guard_percent=guard))
    login(client, "root")
    return client.get(f"/api/v1/admin/storage-sync/runs/{rid}").json()


def files(root: Path, sub: str, pattern: str = "*") -> list[Path]:
    return sorted(p for p in (root / sub).rglob(pattern) if p.is_file())


def rec_rows(client):
    login(client, "root")
    return client.get("/api/v1/admin/recordings").json()


def test_clean_storage_reports_everything_present(client, settings):
    fs = prof(client, settings)
    finished_meeting(client, settings)
    rep = sync(client)
    assert rep["status"] == "ok" and rep["missing"] == 0 and rep["orphans"] == 0 and rep["unavailable"] == 0
    assert rep["checked"] >= 4                                   # запись, стенограмма, переписка, 2 вложения
    assert {r["file_state"] for r in rec_rows(client)} == {"ok"}
    assert files(fs, "Audio", "*.wav") and files(fs, "Transcripts", "protocol.txt")


def test_recording_deleted_by_hand_is_detected_and_download_link_disappears(client, settings):
    fs = prof(client, settings)
    mid, _ = finished_meeting(client, settings)
    rec = rec_rows(client)[0]
    assert client.get(f"/api/v1/meetings/{mid}/recordings/{rec['id']}").status_code == 200
    files(fs, "Audio", "*.wav")[0].unlink()                      # администратор файлового сервера удалил файл руками
    rep = sync(client)
    assert rep["missing"] == 1 and rep["status"] == "ok"
    assert any(m["kind"] == "recording" or m["name"].endswith(".wav") for m in rep["details"]["missing"])
    row = rec_rows(client)[0]
    assert row["file_state"] == "missing"
    r = client.get(f"/api/v1/meetings/{mid}/recordings/{rec['id']}")
    assert r.status_code == 410 and r.json()["detail"]["code"] == "file_missing"          # не системная ошибка файловой системы
    assert client.get(f"/api/v1/meetings/{mid}/recordings").json()[0]["file_state"] == "missing"
    audit = [a for a in client.get("/api/v1/admin/audit").json() if a["action"] == "storage.sync"]
    assert audit and audit[0]["details"]["missing"] == 1 and audit[0]["actor"] == "тест"
    # повторная сверка не считает то же самое «новым»
    assert sync(client)["missing"] == 0


def test_file_back_in_storage_restores_state_and_unknown_files_are_reported_not_imported(client, settings):
    fs = prof(client, settings)
    mid, _ = finished_meeting(client, settings)
    wav = files(fs, "Audio", "*.wav")[0]
    saved = wav.read_bytes()
    wav.unlink()
    assert sync(client)["missing"] == 1
    wav.write_bytes(saved)
    stranger = wav.parent / "чужой файл.bin"                     # неизвестный файл рядом: не наш
    stranger.write_bytes(b"1234")
    rep = sync(client)
    assert rep["restored"] == 1 and rep["missing"] == 0 and rep["orphans"] == 1
    assert [o["path"].endswith("чужой файл.bin") for o in rep["details"]["orphans"]] == [True]
    assert rec_rows(client)[0]["file_state"] == "ok"
    assert stranger.exists()                                      # ничего не удаляется и не импортируется
    assert len(rec_rows(client)) == 1


def test_attachment_and_exports_missing_are_hidden_from_users(client, settings):
    fs = prof(client, settings)
    mid, ids = finished_meeting(client, settings)
    att_files = files(fs, "Chat/files")
    assert len(att_files) == 2
    next(p for p in att_files if p.suffix == ".png").unlink()
    files(fs, "Transcripts", "protocol.txt")[0].unlink()
    rep = sync(client)
    assert rep["missing"] == 2
    login(client, "alice")
    msgs = client.get(f"/api/v1/meetings/{mid}/chat").json()["messages"]
    by = {a["name"]: a for m in msgs for a in m["attachments"]}
    assert by["схема.png"]["missing"] is True and by["отчёт.txt"]["missing"] is False
    r = client.get(f"/api/v1/meetings/{mid}/chat/attachments/{by['схема.png']['id']}")
    assert r.status_code == 410 and "удалён" in r.json()["detail"]
    assert client.get(f"/api/v1/meetings/{mid}/chat/attachments/{by['отчёт.txt']['id']}").content == b"hello"
    login(client, "root")
    from app.models import Protocol

    async def transcript_state():
        async with client.app_obj.state.session_maker() as db:
            return (await db.execute(select(Protocol.file_state).where(Protocol.kind == "transcript"))).scalars().all()

    assert client.portal.call(transcript_state) == ["missing"]
    from app.api.meetings import _protocol_dict

    async def shown():
        async with client.app_obj.state.session_maker() as db:
            p = (await db.execute(select(Protocol).where(Protocol.kind == "transcript"))).scalars().one()
            return _protocol_dict(p)

    out = client.portal.call(shown)
    assert out["file_state"] == "missing" and out["location"] is None          # ссылка на выгруженный файл не отдаётся


def test_on_demand_check_marks_missing_without_waiting_for_schedule(client, settings):
    fs = prof(client, settings)
    mid, ids = finished_meeting(client, settings)
    files(fs, "Chat/files")[0].unlink()
    login(client, "alice")
    statuses = [client.get(f"/api/v1/meetings/{mid}/chat/attachments/{i}").status_code for i in ids]
    assert sorted(statuses) == [200, 410]
    login(client, "root")
    async def states():
        async with client.app_obj.state.session_maker() as db:
            return sorted((await db.execute(select(ChatAttachment.file_state))).scalars().all())

    assert client.portal.call(states) == ["missing", "ok"]


def test_unavailable_storage_does_not_declare_files_deleted(client, settings):
    fs = prof(client, settings)
    mid, _ = finished_meeting(client, settings)
    hidden = fs.with_name("fs-offline")
    fs.rename(hidden)                                            # хранилище «отвалилось» (не смонтирован том / нет сети)
    rep = sync(client)
    assert rep["missing"] == 0 and rep["unavailable"] > 0 and rep["status"] in ("partial", "unavailable")
    assert rep["details"]["unavailable_backends"]
    assert {r["file_state"] for r in rec_rows(client)} == {"ok"}
    hidden.rename(fs)                                            # вернулось
    rep = sync(client)
    assert rep["status"] == "ok" and rep["missing"] == 0 and rep["unavailable"] == 0


def test_mass_disappearance_is_treated_as_failure_unless_forced(client, settings):
    fs = prof(client, settings)
    mid, _ = finished_meeting(client, settings)
    be = LocalStorage(str(fs / "Chat" / "files"))

    async def add_many():
        async with client.app_obj.state.session_maker() as db:
            m = await db.get(Meeting, uuid.UUID(mid))
            row = (await db.execute(select(ChatAttachment))).scalars().first()
            for i in range(12):
                a = ChatAttachment(meeting_id=m.id, message_id=row.message_id, uploader_type="user", uploader_id=row.uploader_id, name=f"f{i}.txt", mime="text/plain",
                                   size=3, kind="file", storage_key=f"{mid}/mass-{i}.txt", profile_id=row.profile_id)
                db.add(a)
                be.write_bytes(a.storage_key, b"abc")
            await db.commit()

    client.portal.call(add_many)
    assert sync(client)["missing"] == 0
    for p in (fs / "Chat" / "files").rglob("*"):
        if p.is_file():
            p.unlink()                                           # исчезли ВСЕ вложения разом
    rep = sync(client)
    assert rep["missing"] == 0 and rep["status"] == "partial" and rep["details"]["suspicious"]      # похоже на сбой — ничего не менялось
    assert "сбой" in list(rep["details"]["suspicious"].values())[0]
    rep = sync(client, force=True)                               # администратор подтвердил
    assert rep["missing"] == 14 and not rep["details"]["suspicious"]


def test_manual_run_via_api_background_report_and_single_run_guard(client, settings):
    prof(client, settings)
    finished_meeting(client, settings)
    login(client, "root")
    r = client.post("/api/v1/admin/storage-sync/run", json={})
    assert r.status_code == 202
    client.portal.call(client.app_obj.state.protocols.drain)
    ov = client.get("/api/v1/admin/storage-sync").json()
    assert ov["running"] is None and ov["last"]["trigger"] == "manual" and ov["last"]["status"] == "ok" and ov["last"]["checked"] >= 4
    assert ov["last"]["details"]["per_kind"]["записи"]["checked"] == 1
    assert ov["settings"]["interval_hours"] == 12 and ov["settings"]["enabled"] is True
    # пока идёт одна — вторая не запускается
    async def fake_running():
        async with client.app_obj.state.session_maker() as db:
            db.add(StorageSyncRun(trigger="auto", status="running"))
            await db.commit()

    client.portal.call(fake_running)
    assert client.post("/api/v1/admin/storage-sync/run", json={}).status_code == 409
    login(client, "alice")
    assert client.get("/api/v1/admin/storage-sync").status_code == 403


def test_schedule_runs_only_when_due_and_can_be_disabled(client, settings):
    prof(client, settings)
    finished_meeting(client, settings)
    sm, svc, rec, redis = client.app_obj.state.session_maker, client.app_obj.state.settings_svc, client.app_obj.state.reconciler, client.app_obj.state.redis
    assert client.portal.call(lambda: run_if_due(sm, svc, rec, redis)) is True       # ещё ни разу не запускалась — пора
    assert client.portal.call(lambda: run_if_due(sm, svc, rec, redis)) is False      # только что была — рано
    put_settings(client, "storage_sync", enabled=False)
    assert client.portal.call(lambda: due(sm, svc))[0] is False
    put_settings(client, "storage_sync", enabled=True, interval_hours=1)
    login(client, "root")
    assert client.put("/api/v1/admin/settings/storage_sync", json={"interval_hours": 0}).status_code == 422
    assert client.get("/api/v1/admin/storage-sync").json()["runs"][0]["trigger"] == "auto"
