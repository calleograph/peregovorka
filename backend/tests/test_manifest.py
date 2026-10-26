"""Паспорт встречи (manifest.json): подпись и проверка, запись рядом с материалами новой встречи, обновление, отсутствие секретов, устойчивость к ошибкам."""
from __future__ import annotations

import base64
import json
import os
import uuid
from pathlib import Path

from sqlalchemy import select

from app.models import Meeting, Protocol
from app.services import manifest as mf

from .conftest import login, make_settings, put_settings, running_app
from .test_admin_features import _drain, _two_person_meeting

KEY = base64.b64encode(os.urandom(32)).decode()
OTHER = base64.b64encode(os.urandom(32)).decode()


def _doc(**over):
    base = {"format": mf.FORMAT, "version": 1, "instance": mf.instance_id(KEY), "meeting": {"id": str(uuid.uuid4())}, "files": []}
    return mf.sign({**base, **over}, KEY)


# ------------------------------------------------------------------------------------------------ подпись
def test_a_signed_manifest_is_valid_and_any_change_breaks_the_signature():
    d = _doc()
    assert mf.verify(d, KEY)[0] == "valid"
    for tamper in (lambda x: x["meeting"].update(id="other"), lambda x: x.update(files=[{"name": "x"}]), lambda x: x.update(access={"acl": [{"type": "group", "ref": "cn=all"}]})):
        t = json.loads(json.dumps(d))
        tamper(t)
        assert mf.verify(t, KEY)[0] in ("invalid", "foreign"), "правка прав или файлов после подписи обнаруживается"
    again = json.loads(json.dumps(d))
    again["signature"]["value"] = "0" * 64
    assert mf.verify(again, KEY)[0] == "invalid"


def test_foreign_unsigned_and_garbage_manifests_are_not_trusted():
    d = _doc()
    assert mf.verify(d, OTHER)[0] == "foreign", "паспорт другой установки (другой ключ) не подтверждается"
    assert mf.verify({k: v for k, v in d.items() if k != "signature"}, KEY)[0] == "unsigned"
    assert mf.verify({"format": "x"}, KEY)[0] == "invalid" and mf.verify([1], KEY)[0] == "invalid" and mf.verify(None, KEY)[0] == "invalid"
    assert mf.verify(d, "")[0] == "foreign", "без ключа проверить подпись нельзя — доверия нет"


def test_instance_id_is_stable_per_key_and_does_not_reveal_it():
    assert mf.instance_id(KEY) == mf.instance_id(KEY) != mf.instance_id(OTHER) != ""
    assert KEY[:12] not in mf.instance_id(KEY) and mf.instance_id("не-ключ") == ""


def test_known_names_are_classified_and_foreign_files_ignored():
    assert mf.classify("protocol.txt") == ("transcript", "txt") and mf.classify("Иван Иванов.wav") == ("audio_participant", "wav")
    assert mf.classify("Общая запись встречи.m4a") == ("audio_mix", "m4a") and mf.classify("whiteboard.drawio") == ("board", "drawio")
    for other in ("manifest.json", "notes.docx", "a.wav.tmp", "x.exe", ".peregovorka-volume"):
        assert mf.classify(other) is None


# ------------------------------------------------------------------------------------------------ запись у новой встречи
def _profile(c, settings, name="Файловый сервер", sub="fs"):
    login(c, "root")
    r = c.post("/api/v1/admin/storages", json={"name": name, "kind": "local", "config": {"local_path": str(Path(settings.data_dir) / sub)}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _finished(c, settings):
    pid = _profile(c, settings)
    put_settings(c, "storage", enabled=True, profile_id=pid)
    _room, a = _two_person_meeting(c, name="Переговорная Север")
    login(c, "alice")
    assert c.post(f"/api/v1/meetings/{a['meeting_id']}/end").status_code == 204
    _drain(c)
    return uuid.UUID(a["meeting_id"]), Path(settings.data_dir) / "fs"


def _refresh(c, mid):
    async def go():
        async with c.app_obj.state.session_maker() as db:
            return await c.app_obj.state.manifests.refresh(db, mid)
    return c.portal.call(go)


def test_new_meeting_gets_a_signed_manifest_next_to_its_materials(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        mid, root = _finished(c, s)
        assert _refresh(c, mid)["written"] >= 1
        found = list(root.rglob("manifest.json"))
        assert found, "паспорт записан рядом со стенограммой"
        doc = json.loads(found[0].read_text(encoding="utf-8"))
        assert mf.verify(doc, s.app_master_key)[0] == "valid" and doc["instance"] == mf.instance_id(s.app_master_key)
        assert doc["meeting"]["id"] == str(mid) and doc["version"] == 1 and doc["folder"] == "Transcripts"
        assert {p["ad_guid"] is not None for p in doc["participants"]} == {True} and len(doc["participants"]) == 2, "участники — по идентификаторам, не по ФИО"
        f = {x["name"]: x for x in doc["files"]}
        body = (found[0].parent / "protocol.txt").read_bytes()
        import hashlib
        assert f["protocol.txt"]["sha256"] == hashlib.sha256(body).hexdigest() and f["protocol.txt"]["size"] == len(body) and f["protocol.txt"]["kind"] == "transcript"
        text = found[0].read_text(encoding="utf-8")
        for secret in (s.app_master_key, s.livekit_api_secret, s.internal_api_token, "Alice A", "Bob B", "Добрый день"):
            assert secret not in text, "в паспорте нет ключей, паролей, имён и текста встречи"


def test_manifest_is_refreshed_not_duplicated_and_unchanged_files_are_not_rehashed(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        mid, root = _finished(c, s)
        _refresh(c, mid)
        calls = []
        real = mf.hash_file
        monkeypatch.setattr(mf, "hash_file", lambda *a, **k: (calls.append(a[1]), real(*a, **k))[1])
        _refresh(c, mid)
        assert calls == [], "размер не изменился и подписанный паспорт есть — сумма не пересчитывается (нагрузка на диск)"
        assert len(list(root.rglob("manifest.json"))) == len(set(p.parent for p in root.rglob("manifest.json")))
        # новый материал рядом → попадает в паспорт
        d = next(root.rglob("manifest.json")).parent
        (d / "summary.md").write_text("Итог\n", encoding="utf-8")
        _refresh(c, mid)
        names = {x["name"] for x in json.loads((d / "manifest.json").read_text(encoding="utf-8"))["files"]}
        assert "summary.md" in names


def test_marking_and_background_tick_write_the_manifest_and_a_failure_does_not_break_anything(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        mid, root = _finished(c, s)
        for p in root.rglob("manifest.json"):
            p.unlink()

        async def go():
            await c.app_obj.state.manifests.mark(mid)
            async with c.app_obj.state.session_maker() as db:
                return await c.app_obj.state.manifests.tick(db)
        assert c.portal.call(go) == 1 and list(root.rglob("manifest.json"))

        async def nothing():
            async with c.app_obj.state.session_maker() as db:
                await c.app_obj.state.manifests.mark("not-a-uuid")
                return await c.app_obj.state.manifests.tick(db)
        assert c.portal.call(nothing) == 0, "мусорная отметка не роняет цикл"


def test_running_meeting_gets_no_manifest(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        pid = _profile(c, s)
        put_settings(c, "storage", enabled=True, profile_id=pid)
        _room, a = _two_person_meeting(c, name="Идёт")
        assert _refresh(c, uuid.UUID(a["meeting_id"]))["written"] == 0
        assert not list((Path(s.data_dir) / "fs").rglob("manifest.json"))
        assert _refresh(c, uuid.uuid4())["written"] == 0
        assert Meeting and Protocol and select
