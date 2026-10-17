from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import AuditLog, ConfigBackupRun
from app.services.auto_backup import NAME_RE, is_due, rotation_victims

from .conftest import login

API = "/api/v1/admin/auto-backup"
TZ = ZoneInfo("UTC")


def _setup(client, settings, **over):
    login(client, "root")
    p = client.post("/api/v1/admin/storages", json={"name": "Копии", "kind": "local", "config": {"local_path": str(Path(settings.data_dir) / "bk")}}).json()
    key = client.post(f"{API}/key", json={"password": "root-pass"}).json()["key"]
    st = client.put(f"{API}/settings", json={"enabled": True, "profile_id": p["id"], **over})
    assert st.status_code == 200, st.text
    return p, key


def test_rotation_keeps_newest_and_ignores_foreign_files():
    names = [f"peregovorka-config-202610{d:02d}-020000.pgcfg" for d in range(1, 6)] + ["notes.txt", "peregovorka-config-20261011-020000.pgcfg.sha256"]
    assert rotation_victims(names, 2) == ["peregovorka-config-20261003-020000.pgcfg", "peregovorka-config-20261002-020000.pgcfg", "peregovorka-config-20261001-020000.pgcfg"]
    assert rotation_victims(names, 10) == []
    assert not NAME_RE.match("../etc/passwd") and not NAME_RE.match("peregovorka-config-20261001-020000.pgcfg/..")


def _run(status, started):
    return ConfigBackupRun(kind="backup", trigger="auto", status=status, started_at=started)


def test_schedule_rules():
    now = datetime(2026, 10, 17, 3, 0, tzinfo=TZ)
    assert not is_due(now, "04:00", 1, None, TZ)                                          # время ещё не наступило
    assert is_due(now, "02:00", 1, None, TZ)                                              # ни разу не запускалась
    assert not is_due(now, "02:00", 1, _run("ok", now - timedelta(hours=1)), TZ)           # сегодня уже была
    assert is_due(now, "02:00", 1, _run("ok", now - timedelta(days=1)), TZ)
    assert not is_due(now, "02:00", 7, _run("ok", now - timedelta(days=3)), TZ)            # раз в неделю
    assert not is_due(now, "02:00", 1, _run("failed", now - timedelta(minutes=10)), TZ)    # повтор неудачи — не чаще раза в час
    assert is_due(now, "02:00", 1, _run("failed", now - timedelta(hours=2)), TZ)
    assert not is_due(now, "02:00", 1, _run("running", now - timedelta(minutes=5)), TZ)
    assert is_due(now, "02:00", 1, _run("running", now - timedelta(hours=3)), TZ)          # зависшее задание


def test_cannot_enable_without_key_or_storage(client):
    login(client, "root")
    assert client.put(f"{API}/settings", json={"enabled": True}).status_code == 422
    assert client.put(f"{API}/settings", json={"archive_key": "x"}).status_code == 422
    client.post(f"{API}/key", json={"password": "root-pass"})
    assert client.put(f"{API}/settings", json={"enabled": True}).status_code == 422       # ключ есть, хранилища нет
    assert client.post(f"{API}/key", json={"password": "wrong"}).status_code == 403


def test_backup_roundtrip_encrypted_verified_rotated_and_audited(client, settings):
    p, key = _setup(client, settings, keep=2)
    assert "archive_key" not in client.get(API).json()["settings"] and client.get(API).json()["settings"]["archive_key_set"] is True
    names = []
    for _ in range(3):
        r = client.post(f"{API}/run")
        assert r.status_code == 200 and r.json()["status"] == "ok", r.text
        names.append(r.json()["file_name"])
        # имена содержат секунды: повторный запуск в ту же секунду дал бы то же имя
        import time
        time.sleep(1.1)
    folder = Path(settings.data_dir) / "bk" / "ConfigBackups"
    files = sorted(f.name for f in folder.iterdir())
    assert not [f for f in files if f.endswith(".partial")]
    assert len([f for f in files if f.endswith(".pgcfg")]) == 2 and len([f for f in files if f.endswith(".sha256")]) == 2   # самая старая удалена вместе с суммой
    last = names[-1]
    blob = (folder / last).read_bytes()
    assert hashlib.sha256(blob).hexdigest() in (folder / f"{last}.sha256").read_text()
    assert key.encode() not in blob and b"peregovorka-config" not in blob[64:], "в хранилище только шифртекст"
    ver = client.post(f"{API}/verify", json={}).json()
    assert ver["status"] == "ok" and ver["file_name"] == last
    # повреждение файла обнаруживается проверкой
    (folder / last).write_bytes(blob[:-5] + b"xxxxx")
    bad = client.post(f"{API}/verify", json={"name": last}).json()
    assert bad["status"] == "failed" and "повреждена" in bad["message"]
    # скачивание: только с паролем, пишется в аудит
    assert client.post(f"{API}/download", json={"name": last, "password": "wrong"}).status_code == 403
    assert client.post(f"{API}/download", json={"name": "../../etc/passwd", "password": "root-pass"}).status_code == 404
    (folder / last).write_bytes(blob)
    d = client.post(f"{API}/download", json={"name": last, "password": "root-pass"})
    assert d.status_code == 200 and d.content == blob

    st = client.get(API).json()
    assert st["last_ok"]["file_name"] == last and st["stale"] is False and len(st["runs"]) >= 5

    async def rows():
        async with client.app_obj.state.session_maker() as db:
            return (await db.execute(select(AuditLog).where(AuditLog.action.like("backup.%")))).scalars().all()
    acts = [(r.action, r.result) for r in client.portal.call(rows)]
    for a in ("backup.create", "backup.verify", "backup.download", "backup.key_create", "backup.settings"):
        assert any(x[0] == a for x in acts), a
    assert ("backup.verify", "failure") in acts
    assert key not in str([r.details for r in client.portal.call(rows)])


def test_failed_backup_is_recorded_and_flags_stale(client, settings):
    p, key = _setup(client, settings)
    client.delete  # хранилище используется — удалять не будем; ломаем путь иначе
    from app.models import StorageProfile

    async def break_it():
        async with client.app_obj.state.session_maker() as db:
            row = await db.get(StorageProfile, __import__("uuid").UUID(p["id"]))
            row.config = {"local_path": "/proc/forbidden-outside-data-dir"}
            await db.commit()
    client.portal.call(break_it)
    r = client.post(f"{API}/run").json()
    assert r["status"] == "failed" and r["message"]
    st = client.get(API).json()
    assert st["stale"] is True and st["last_ok"] is None


def test_backup_check_in_setup_checklist(client, settings):
    login(client, "root")
    chk = client.post("/api/v1/admin/setup/check").json()
    row = next(c for c in chk["checks"] if c["id"] == "backup")
    assert row["status"] == "warn"                                                         # автокопирование выключено
    _setup(client, settings)
    assert client.post(f"{API}/run").json()["status"] == "ok"
    chk = client.post("/api/v1/admin/setup/check").json()
    assert next(c for c in chk["checks"] if c["id"] == "backup")["status"] == "ok"


def test_regular_user_has_no_access(client):
    login(client, "alice")
    assert client.get(API).status_code == 403
    assert client.post(f"{API}/download", json={"name": "x", "password": "x"}).status_code == 403
