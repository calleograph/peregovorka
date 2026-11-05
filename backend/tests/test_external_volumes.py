"""Внешние тома: приложение просит помощника на хосте открыть каталог сервера, а не получает доступ к Docker или root. Защита от записи на локальный диск при потере тома."""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest

from app.services import mounts
from app.services.settings import SettingsError
from app.services.storage import LocalStorage, StorageError
from app.services.updates import Channel
from app.services.volumes import Volumes, volume_id

from .conftest import put_settings  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def fresh_helper(d: Path) -> None:
    (d / "updater").mkdir(exist_ok=True)
    (d / "updater" / "helper.json").write_text(json.dumps({"ts": time.time(), "pid": 1, "uid": 0, "stopped": False}))


def answer(d: Path, *, ok=True, error="", attach=True):
    """Подставной помощник: отвечает на запрос, как updater.sh."""
    async def run():
        req = d / "updater" / "request.txt"
        for _ in range(100):
            if req.exists():
                kv = dict(line.split("=", 1) for line in req.read_text().splitlines())
                req.unlink()
                vid = volume_id(kv["arg"]) if kv["op"] == "attach" else ""
                (d / "updater" / "volume-result.json").write_text(json.dumps({"request_id": kv["id"], "ok": ok, "id": vid, "error": error}))
                return kv
            await asyncio.sleep(0.02)
    return run()


def test_volume_id_matches_the_shell_helper():
    out = subprocess.run(["bash", "-c", 'source scripts/lib/vollib.sh; vol_id /mnt/qnap-nfs; vol_id /srv/Диск_1 2>/dev/null; vol_id /mnt/A.b'], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert out[0] == volume_id("/mnt/qnap-nfs") and out[-1] == volume_id("/mnt/A.b")


def test_attach_asks_the_helper_and_returns_the_container_path(tmp_path):
    fresh_helper(tmp_path)
    v = Volumes(str(tmp_path))

    async def go():
        a, b = await asyncio.gather(v.attach("/mnt/qnap-nfs"), answer(tmp_path))
        return a, b
    path, req = asyncio.run(go())
    assert path == f"/data/ext/{volume_id('/mnt/qnap-nfs')}" and req["action"] == "volume" and req["op"] == "attach" and req["arg"] == "/mnt/qnap-nfs"


def test_helper_refusal_reaches_the_admin_and_missing_helper_gives_the_command(tmp_path):
    fresh_helper(tmp_path)
    v = Volumes(str(tmp_path))

    async def go():
        await asyncio.gather(v.attach("/mnt/x"), answer(tmp_path, ok=False, error="Каталог /mnt/x на сервере не найден"))
    with pytest.raises(SettingsError, match="не найден"):
        asyncio.run(go())
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(SettingsError, match="sudo scripts/volumes.sh attach /mnt/y"):
        asyncio.run(Volumes(str(other)).attach("/mnt/y"))


@pytest.mark.parametrize("bad", ["relative", "/mnt/a b", "/mnt/../etc", "/mnt/$(id)", "/mnt/a\nop=attach", ""])
def test_path_is_checked_before_it_reaches_the_request_file(tmp_path, bad):
    fresh_helper(tmp_path)
    with pytest.raises(SettingsError):
        asyncio.run(Volumes(str(tmp_path)).attach(bad))
    assert not (tmp_path / "updater" / "request.txt").exists()


def test_channel_refuses_injection_into_the_request(tmp_path):
    c = Channel(str(tmp_path))
    for op, arg in (("attach", "/mnt/a\naction=update"), ("rm", "/mnt/a"), ("attach", None)):
        with pytest.raises(ValueError):
            c.request("volume", by="x", op=op, arg=arg)


@pytest.mark.parametrize("path,reason", [("/etc", "внутри"), ("/mnt", "внутри"), ("/var/lib/docker", "внутри"), ("/mnt/a/../b", "«..»"), ("/mnt/a//b", "«..»"), ("/mnt/a b", "латиница"), ("relative", "абсолютный"), ("/mnt/$(id)", "латиница")])
def test_shell_helper_refuses_paths(tmp_path, path, reason):
    (tmp_path / "ok").mkdir()
    r = subprocess.run(["bash", "-c", 'DATA_ROOT=/tmp/none; REPO_ROOT=/tmp/none2; source scripts/lib/vollib.sh; vol_path_error "$1"', "_", path], cwd=ROOT, capture_output=True, text=True)
    assert reason in r.stdout or "не найден" in r.stdout, r.stdout


def test_shell_helper_refuses_symlinks_and_own_directories(tmp_path):
    real, link = tmp_path / "mnt" / "real", tmp_path / "mnt" / "link"
    real.mkdir(parents=True)
    link.symlink_to(real)
    run = lambda p, data="/tmp/none": subprocess.run(["bash", "-c", f'DATA_ROOT={data}; REPO_ROOT=/tmp/none2; VOLUME_ALLOWED_ROOTS={tmp_path}/mnt; source scripts/lib/vollib.sh; vol_path_error "$1"', "_", str(p)],      # noqa: E731
                                                     cwd=ROOT, capture_output=True, text=True).stdout
    assert run(real) == ""
    assert "символьную ссылку" in run(link)
    assert "принадлежит приложению" in run(real, data=str(tmp_path / "mnt"))


def test_alive_does_not_hang_on_a_stuck_filesystem(monkeypatch):
    class Stuck:
        def poll(self): return None
        def kill(self): pass
    monkeypatch.setattr(mounts.subprocess, "Popen", lambda *a, **k: Stuck())
    t = time.monotonic()
    assert mounts.alive("/mnt/x", timeout=0.1) is False and time.monotonic() - t < 1


def test_writes_stop_when_the_mount_is_gone_or_the_source_changed(tmp_path, monkeypatch):
    s = LocalStorage(str(tmp_path), "m", {"mountpoint": "/data/ext/v", "fstype": "nfs4", "source": "nas:/share"})
    (tmp_path / ".peregovorka-volume").write_text("m")
    from app.services import storage as st
    (tmp_path / st.MARKER).write_text("m")
    now = {"mountpoint": "/data/ext/v", "fstype": "nfs4", "source": "nas:/share", "on_root": False}
    monkeypatch.setattr(mounts, "describe", lambda p: now)
    s.write_bytes("Audio/a.bin", b"1")
    assert (tmp_path / "Audio/a.bin").read_bytes() == b"1"
    for changed in ({**now, "source": "other:/share"}, {**now, "mountpoint": "/data/ext", "fstype": "ext4"}, {**now, "on_root": True}):
        monkeypatch.setattr(mounts, "describe", lambda p, c=changed: c)
        with pytest.raises(StorageError, match="не смонтирован"):
            s.write_bytes("Audio/b.bin", b"2")
    assert not (tmp_path / "Audio/b.bin").exists(), "на локальный диск ничего не записано"
    monkeypatch.setattr(mounts, "describe", lambda p: now)
    monkeypatch.setattr(mounts, "alive", lambda p: False)
    with pytest.raises(StorageError, match="не отвечает"):
        s.read_bytes("Audio/a.bin")


def test_paths_inside_data_keep_working_without_the_helper(client):
    login_admin(client)
    r = client.post("/api/v1/admin/storages", json={"name": "Старое", "kind": "local", "config": {"local_path": "/tmp/x"}})
    assert r.status_code == 422 and "внутри" in r.json()["detail"], "прежняя проверка /data на месте"
    data = client.app_obj.state.settings.data_dir
    r = client.post("/api/v1/admin/storages", json={"name": "Каталог", "kind": "local", "config": {"local_path": f"{data}/exports"}})
    assert r.status_code == 201 and r.json()["host_path"] is None and r.json()["volume"] is None


def test_external_volume_profile_is_saved_with_both_paths_and_shows_the_state(client):
    login_admin(client)
    d = Path(client.app_obj.state.settings.data_dir)
    fresh_helper(d)
    hp = "/mnt/qnap-nfs"
    vid = volume_id(hp)
    (d / "updater" / "volumes.json").write_text(json.dumps({"items": [{"id": vid, "host_path": hp, "container_path": f"{d}/ext/{vid}", "state": "attached", "free": 2 * 1024**4, "total": 2 * 1024**4, "fstype": "nfs4"}]}))
    (d / "ext" / vid).mkdir(parents=True)
    r = client.post("/api/v1/admin/storages", json={"name": "QNAP", "kind": "local", "config": {"external_volume": True, "host_path": hp + "/"}})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["address"] == hp and j["host_path"] == hp and j["volume"]["state"] == "attached" and j["config"]["local_path"] == f"{d}/ext/{vid}"
    (d / "updater" / "volumes.json").write_text(json.dumps({"items": [{"id": vid, "host_path": hp, "state": "unreachable"}]}))
    assert client.get("/api/v1/admin/storages").json()["items"][0]["volume"]["state"] == "unreachable"


def login_admin(client):
    from .conftest import login
    login(client, "root")
