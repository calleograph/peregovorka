"""Восстановление материалов после переноса хранилища: сканирование, достоверное (по подписанному паспорту) и предположительное (в изолированную область) восстановление,
идемпотентность, права, проверка сумм, защита от выхода за каталог, неизменность исходных файлов, права администратора."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.models import Meeting, MeetingGrant, MeetingParticipant, Protocol, Recording, RestoreJob, Room, RoomAcl, User
from app.services import manifest as mf
from app.services import restore as rs

from .conftest import ADMIN_GROUP, STAFF_GROUP, login, make_room, make_settings, put_settings, running_app

R = "/api/v1/admin/restore"
DIR = "Переговорная Север/2026-10-24, Суббота/10-30"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file() or p.is_symlink()}


def _setup(c, s):
    """Хранилище-профиль «Файловый сервер» для записей и материалов (как после настройки нового сервера)."""
    login(c, "root")
    r = c.post("/api/v1/admin/storages", json={"name": "Файловый сервер", "kind": "local", "config": {"local_path": str(Path(s.data_dir) / "fs")}})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    put_settings(c, "storage", enabled=True, profile_id=pid)
    put_settings(c, "audio_storage", enabled=True, profile_id=pid)
    return Path(s.data_dir) / "fs"


def _users(c):
    """alice и bob входили: у них есть записи сотрудников; возвращает (id, ad_guid)."""
    out = {}
    for u in ("alice", "bob", "carol"):
        login(c, u)

    async def go():
        async with c.app_obj.state.session_maker() as db:
            for row in (await db.execute(select(User))).scalars():
                out[row.sam_account_name] = (str(row.id), row.ad_guid.lower())
    c.portal.call(go)
    return out


def _put(root: Path, folder: str, rel: str, data: bytes) -> bytes:
    p = root / folder / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return data


def _manifest(root: Path, s, folder: str, directory: str, names: list[str], ctx: dict, key: str | None = None, sums: dict | None = None):
    files = []
    for n in names:
        b = (root / folder / directory / n).read_bytes()
        kind, fmt = mf.classify(n)
        files.append({"name": n, "kind": kind, "format": fmt, "size": len(b), "sha256": (sums or {}).get(n, _sha(b))})
    doc = mf.sign(mf.build(ctx, instance=mf.instance_id(key or s.app_master_key), folder=folder, directory=directory, files=files, now=datetime.now(timezone.utc)), key or s.app_master_key)
    (root / folder / directory / "manifest.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def _ctx(mid, room, users, participants=("alice", "bob"), extra_people=(), grants=()):
    started = datetime(2026, 10, 24, 7, 30, tzinfo=timezone.utc)
    return {"meeting": {"id": mid, "started_at": mf.iso(started), "ended_at": mf.iso(started + timedelta(hours=1)), "end_reason": "manual", "transcription": True, "record_audio": True},
            "room": room, "access": {"acl": [{"type": "group", "ref": STAFF_GROUP}], "moderators": [], "grants": [{"user_id": users[g][0], "ad_guid": users[g][1]} for g in grants]},
            "participants": [{"user_id": users[p][0], "ad_guid": users[p][1], "joined_at": mf.iso(started), "left_at": mf.iso(started + timedelta(hours=1))} for p in participants] + list(extra_people),
            "retention": {"text_days": 365, "audio_days": 30}}


def _job(c, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"{R}/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("задание не завершилось")


def _scan(c, **body):
    login(c, "root")
    r = c.post(f"{R}/scan", json=body)
    assert r.status_code == 202, r.text
    j = _job(c, r.json()["id"])
    assert j["status"] == "done", j
    return j


def _apply(c, scan, ids=None):
    r = c.post(f"{R}/apply", json={"scan_id": scan["id"], "items": ids or [i["id"] for i in scan["result"]["items"]], "confirm": True})
    assert r.status_code == 202, r.text
    return _job(c, r.json()["id"])


def _count(c, model, *cond):
    async def go():
        async with c.app_obj.state.session_maker() as db:
            return (await db.execute(select(func.count()).select_from(model).where(*cond))).scalar_one()
    return c.portal.call(go)


def _old_meeting(c, s, root, room_id, users, name="Переговорная Север", **ctx_kw):
    """Старая встреча на диске: стенограмма, протокол, запись участника, общая запись, доска + паспорта (как у выпуска 0.40)."""
    mid = str(uuid.uuid4())
    wav = _put(root, "Audio", f"{DIR}/Иван Иванов.wav", b"RIFF" + os.urandom(2000))
    m4a = _put(root, "Audio", f"{DIR}/Общая запись встречи.m4a", os.urandom(3000))
    _put(root, "Transcripts", f"{DIR}/protocol.txt", "Участвовали: Alice A, Bob B\n[00:01] Alice A: Начинаем\n".encode())
    _put(root, "Protocols", f"{DIR}/official-protocol.md", "# Протокол\nРешили: утвердить бюджет.\n".encode())
    _put(root, "Protocols", f"{DIR}/summary.md", "Итог встречи\n".encode())
    _put(root, "Boards", f"{DIR}/whiteboard.drawio", b'<mxfile><diagram><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="2" value="A" parent="1"/></root></mxGraphModel></diagram></mxfile>')
    _put(root, "Chat", f"{DIR}/chat.txt", "[10:31] Alice A: привет\n".encode())
    ctx = _ctx(mid, {"id": room_id, "slug": "north", "name": name, "lifetime": "permanent", "history_access": "participants"}, users, **ctx_kw)
    docs = {f: _manifest(root, s, f, DIR, names, ctx) for f, names in (("Audio", ["Иван Иванов.wav", "Общая запись встречи.m4a"]), ("Transcripts", ["protocol.txt"]),
                                                                        ("Protocols", ["official-protocol.md", "summary.md"]), ("Boards", ["whiteboard.drawio"]), ("Chat", ["chat.txt"]))}
    return mid, ctx, docs, {"wav": wav, "m4a": m4a}


# ------------------------------------------------------------------------------------------------ достоверное восстановление
def test_verified_meeting_is_restored_with_room_participants_and_files_and_access_follows_the_room_policy(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        users = _users(c)
        room = make_room(c, name="Переговорная Север", history_access="participants")
        root = _setup(c, s)
        mid, _ctx_, _docs, files = _old_meeting(c, s, root, room["id"], users, extra_people=[{"user_id": str(uuid.uuid4()), "ad_guid": str(uuid.uuid4()), "joined_at": None, "left_at": None}])
        before = _tree(root)
        scan = _scan(c)
        items = scan["result"]["items"]
        assert len(items) == 1 and items[0]["trust"] == "valid" and items[0]["mode"] == "manifest" and items[0]["status"] == "new"
        assert items[0]["meeting_id"] == mid and items[0]["people"] == {"found": 2, "missing": 1}
        kinds = {f["kind"] for f in items[0]["files"]}
        assert {"audio_participant", "audio_mix", "transcript", "protocol", "summary", "board", "chat"} <= kinds
        job = _apply(c, scan)
        rep = job["result"]["report"][0]
        assert job["status"] == "done" and rep["outcome"] == "restored" and rep["meeting_id"] == mid
        assert any("Участники и разрешения: восстановлено 2" in x and "не найдено учётных записей: 1" in x for x in rep["lines"])
        assert any("chat.txt" in x and "не восстанавливается" in x for x in rep["lines"]), "что не восстановлено — названо прямо"
        # база: встреча со своим идентификатором в своей комнате, участники только существующие, записи и документы привязаны
        assert _count(c, Meeting, Meeting.id == uuid.UUID(mid), Meeting.room_id == uuid.UUID(room["id"])) == 1
        assert _count(c, MeetingParticipant, MeetingParticipant.meeting_id == uuid.UUID(mid)) == 2
        assert _count(c, Recording, Recording.meeting_id == uuid.UUID(mid)) == 2
        assert _count(c, Protocol, Protocol.meeting_id == uuid.UUID(mid), Protocol.status == "ready") == 3
        # права определяет политика комнаты: участник видит, посторонний — нет, администратор — да
        login(c, "alice")
        assert c.get(f"/api/v1/meetings/{mid}").status_code == 200
        login(c, "carol")
        assert c.get(f"/api/v1/meetings/{mid}").status_code in (403, 404)
        login(c, "alice")
        media = c.get(f"/api/v1/meetings/{mid}/media").json()
        assert len(media["mixes"]) == 1 and media["participants"] == [], "участник видит общую запись, записи отдельных участников — только администратору"
        mix = c.get(f"/api/v1/meetings/{mid}/media/{media['mixes'][0]['id']}/stream")
        assert mix.status_code == 200 and mix.content == files["m4a"], "общая запись воспроизводится из восстановленного хранилища"
        login(c, "root")
        parts = c.get(f"/api/v1/meetings/{mid}/media").json()["participants"]
        assert [p["name"] for p in parts] == ["Иван Иванов.wav"]
        wav = c.get(f"/api/v1/meetings/{mid}/media/{parts[0]['id']}/stream")
        assert wav.status_code == 200 and wav.content == files["wav"]
        assert _tree(root) == {k: v for k, v in before.items()}, "исходные файлы не изменены, не переименованы и не удалены"


def test_applying_twice_does_not_duplicate_anything(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        users = _users(c)
        room = make_room(c, name="Переговорная Север")
        root = _setup(c, s)
        mid, *_ = _old_meeting(c, s, root, room["id"], users)
        first = _apply(c, _scan(c))
        counts = (_count(c, Meeting), _count(c, Recording), _count(c, Protocol), _count(c, MeetingParticipant))
        scan2 = _scan(c)
        assert scan2["result"]["items"][0]["status"] == "present", "после восстановления сканирование показывает «уже на месте»"
        second = _apply(c, scan2)
        assert (_count(c, Meeting), _count(c, Recording), _count(c, Protocol), _count(c, MeetingParticipant)) == counts
        assert first["result"]["counts"]["restored"] == 1 and second["result"]["report"][0]["lines"], "повтор отчитывается, а не молчит"


def test_missing_room_is_created_disabled_with_rights_from_the_signed_manifest(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        users = _users(c)
        root = _setup(c, s)
        new_room = str(uuid.uuid4())
        mid, ctx, docs, _ = _old_meeting(c, s, root, new_room, users, name="Старая переговорная")
        ctx["access"]["acl"] += [{"type": "user", "ref": "не-guid"}, {"type": "group", "ref": ""}, {"type": "root", "ref": "x"}]      # мусор в правах отбрасывается
        for f in ("Audio", "Transcripts", "Protocols", "Boards", "Chat"):
            names = [n for n in os.listdir(root / f / DIR) if mf.classify(n)]
            _manifest(root, s, f, DIR, names, ctx)
        _apply(c, _scan(c))

        async def go():
            async with c.app_obj.state.session_maker() as db:
                r = await db.get(Room, uuid.UUID(new_room))
                acl = [(a.subject_type, a.subject_ref) for a in (await db.execute(select(RoomAcl).where(RoomAcl.room_id == r.id))).scalars()]
                return r.is_enabled, r.name, acl
        enabled, name, acl = c.portal.call(go)
        assert enabled is False and name == "Старая переговорная", "комната создана выключенной: вход в неё администратор включит сам после проверки"
        assert acl == [("group", STAFF_GROUP)], "из паспорта принято только то, что похоже на настоящее право"


# ------------------------------------------------------------------------------------------------ недоверие и изоляция
def test_tampered_foreign_unsigned_and_manifestless_folders_go_to_the_isolated_area_without_rights(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    other = base64_key()
    with running_app(s, directory) as c:
        users = _users(c)
        room = make_room(c, name="Переговорная Север", history_access="participants")
        root = _setup(c, s)
        # 1) подпись чужой установки
        mid1, ctx, *_ = _old_meeting(c, s, root, room["id"], users)
        for f in ("Audio", "Transcripts", "Protocols", "Boards", "Chat"):
            names = [n for n in os.listdir(root / f / DIR) if mf.classify(n)]
            _manifest(root, s, f, DIR, names, ctx, key=other)
        # 2) каталог без паспорта
        _put(root, "Transcripts", "Бухгалтерия/2026-09-01, Вторник/09-00/protocol.txt", "Старый разговор\n".encode())
        # 3) паспорт изменили после подписи: добавили себе права
        _put(root, "Transcripts", "Юристы/2026-09-02, Среда/10-00/protocol.txt", "Секретно\n".encode())
        _manifest(root, s, "Transcripts", "Юристы/2026-09-02, Среда/10-00", ["protocol.txt"], _ctx(str(uuid.uuid4()), {"id": room["id"], "slug": "x", "name": "Юристы", "lifetime": "permanent", "history_access": "participants"}, users))
        mpath = root / "Transcripts/Юристы/2026-09-02, Среда/10-00/manifest.json"
        doc = json.loads(mpath.read_text(encoding="utf-8"))
        doc["participants"].append({"user_id": users["carol"][0], "ad_guid": users["carol"][1]})
        mpath.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        scan = _scan(c)
        by = {i["dir"]: i for i in scan["result"]["items"]}
        assert by[DIR]["trust"] == "foreign" and by["Бухгалтерия/2026-09-01, Вторник/09-00"]["trust"] == "none" and by["Юристы/2026-09-02, Среда/10-00"]["trust"] == "invalid"
        assert all("Предположительно" in i["how"] for i in by.values()), "в отчёте предположения отделены от достоверного"
        job = _apply(c, scan)
        assert job["result"]["counts"] == {"restored": 0, "isolated": 3, "skipped": 0, "failed": 0}
        ids = [r["meeting_id"] for r in job["result"]["report"]]
        assert mid1 not in ids, "идентификатор встречи из неподтверждённого файла не принимается"
        # изолированная комната: выключена, только администратор; никто из «участников паспорта» доступа не получил
        async def go():
            async with c.app_obj.state.session_maker() as db:
                iso = (await db.execute(select(Room).where(Room.slug == rs.ISOLATED_SLUG))).scalar_one()
                return iso.is_enabled, iso.history_access, (await db.execute(select(func.count()).select_from(MeetingParticipant).where(MeetingParticipant.meeting_id.in_([uuid.UUID(i) for i in ids])))).scalar_one()
        enabled, hist, people = c.portal.call(go)
        assert enabled is False and hist == "admin" and people == 0
        for who in ("alice", "bob", "carol"):
            login(c, who)
            for i in ids:
                assert c.get(f"/api/v1/meetings/{i}").status_code in (403, 404), who
        login(c, "root")
        for i in ids:
            assert c.get(f"/api/v1/meetings/{i}").status_code == 200
        assert _count(c, MeetingGrant) == 0


def base64_key():
    import base64
    return base64.b64encode(os.urandom(32)).decode()


def test_corrupted_file_is_skipped_and_reported_while_the_rest_is_restored(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        users = _users(c)
        room = make_room(c, name="Переговорная Север")
        root = _setup(c, s)
        mid, *_ = _old_meeting(c, s, root, room["id"], users)
        wav = root / "Audio" / DIR / "Иван Иванов.wav"
        wav.write_bytes(wav.read_bytes()[:-5] + b"XXXXX")                         # тот же размер, другое содержимое
        scan = _scan(c, deep=True)
        item = scan["result"]["items"][0]
        assert any("Иван Иванов.wav" in p and "сумма" in p for p in item["problems"]) and {f["state"] for f in item["files"] if f["name"] == "Иван Иванов.wav"} == {"mismatch"}
        job = _apply(c, scan)
        lines = " ".join(job["result"]["report"][0]["lines"])
        assert "Иван Иванов.wav: контрольная сумма не совпадает" in lines
        assert _count(c, Recording, Recording.meeting_id == uuid.UUID(mid)) == 1, "повреждённая запись не подключена, общая — на месте"


def test_files_listed_in_the_manifest_but_missing_on_disk_are_reported(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        users = _users(c)
        room = make_room(c, name="Переговорная Север")
        root = _setup(c, s)
        _old_meeting(c, s, root, room["id"], users)
        (root / "Audio" / DIR / "Иван Иванов.wav").unlink()
        item = _scan(c)["result"]["items"][0]
        assert any("Иван Иванов.wav" in p and "нет" in p for p in item["problems"])


# ------------------------------------------------------------------------------------------------ безопасность
def test_symbolic_links_cannot_lead_the_scan_outside_the_storage(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        _users(c)
        root = _setup(c, s)
        outside = tmp_path / "outside" / "Секрет/2026-01-01, Четверг/10-00"
        outside.mkdir(parents=True)
        (outside / "protocol.txt").write_text("не из хранилища\n", encoding="utf-8")
        (root / "Transcripts").mkdir(parents=True, exist_ok=True)
        os.symlink(tmp_path / "outside" / "Секрет", root / "Transcripts" / "Ссылка")
        os.symlink(outside / "protocol.txt", root / "Transcripts" / "protocol.txt")
        scan = _scan(c)
        assert scan["result"]["items"] == [] or all("не из хранилища" not in json.dumps(i, ensure_ascii=False) for i in scan["result"]["items"])
        assert not any("Секрет" in i["dir"] for i in scan["result"]["items"]), "обход по символьной ссылке за пределы хранилища не выполняется"


def test_subpath_cannot_escape_and_only_admins_can_use_the_tool(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        _users(c)
        _setup(c, s)
        login(c, "root")
        for bad in ("../x", "a/../../b", "a\\b"):
            assert c.post(f"{R}/scan", json={"subpath": bad}).status_code == 422, bad
        login(c, "alice")
        assert c.get(R).status_code == 403 and c.post(f"{R}/scan", json={}).status_code == 403
        assert c.post(f"{R}/apply", json={"scan_id": str(uuid.uuid4()), "items": ["x"], "confirm": True}).status_code == 403
        assert c.post(f"{R}/jobs/{uuid.uuid4()}/cancel").status_code == 403


def test_apply_needs_confirmation_and_only_items_of_the_scan(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        users = _users(c)
        room = make_room(c, name="Переговорная Север")
        root = _setup(c, s)
        _old_meeting(c, s, root, room["id"], users)
        scan = _scan(c)
        iid = scan["result"]["items"][0]["id"]
        assert c.post(f"{R}/apply", json={"scan_id": scan["id"], "items": [iid]}).status_code == 422
        assert c.post(f"{R}/apply", json={"scan_id": scan["id"], "items": ["подставленный"], "confirm": True}).json()["detail"]["code"] == "bad_items"
        assert c.post(f"{R}/apply", json={"scan_id": str(uuid.uuid4()), "items": [iid], "confirm": True}).json()["detail"]["code"] == "no_scan"
        assert _count(c, Meeting, Meeting.end_reason == "restored") == 0, "без подтверждения ничего не создано"


def test_only_one_job_runs_at_a_time_and_a_dead_job_does_not_block_forever(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        _users(c)
        _setup(c, s)
        login(c, "root")

        async def fake_running(age_min):
            async with c.app_obj.state.session_maker() as db:
                db.add(RestoreJob(kind="scan", status="running", params={}, progress={}, started_by_name="тест", heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=age_min)))
                await db.commit()
            await c.app_obj.state.redis.set(rs.LOCK, "1", ex=600)
        c.portal.call(lambda: fake_running(1))
        r = c.post(f"{R}/scan", json={})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "busy"
        c.portal.call(lambda: c.app_obj.state.redis.delete(rs.LOCK))
        async def age_it():
            async with c.app_obj.state.session_maker() as db:
                for j in (await db.execute(select(RestoreJob))).scalars():
                    j.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=30)
                await db.commit()
            await c.app_obj.state.redis.set(rs.LOCK, "1", ex=600)
        c.portal.call(age_it)
        r2 = c.post(f"{R}/scan", json={})
        assert r2.status_code == 202, "прерванное (давно без сердцебиения) задание не блокирует новое"
        assert _job(c, r2.json()["id"])["status"] == "done"
        statuses = [j["status"] for j in c.get(R).json()["jobs"]]
        assert "failed" in statuses, "оборванное задание помечено как прерванное"


def test_cancel_stops_a_running_scan(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        _users(c)
        root = _setup(c, s)
        for i in range(120):                                   # достаточно каталогов, чтобы сканирование не закончилось мгновенно
            _put(root, "Transcripts", f"Комната {i}/2026-01-01, Четверг/10-00/protocol.txt", b"x")
        login(c, "root")
        r = c.post(f"{R}/scan", json={})
        jid = r.json()["id"]
        assert c.post(f"{R}/jobs/{jid}/cancel").status_code == 200
        j = _job(c, jid)
        assert j["status"] in ("cancelled", "done")          # успело закончиться до проверки отмены — тоже допустимо; главное — отмена не ломает задание
        assert j["error"] is None


def test_restored_isolated_meeting_is_listed_for_admins_with_its_origin(tmp_path, directory):
    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        _users(c)
        root = _setup(c, s)
        _put(root, "Transcripts", "Бухгалтерия/2026-09-01, Вторник/09-00/protocol.txt", "Старый разговор\n".encode())
        _put(root, "Audio", "Бухгалтерия/2026-09-01, Вторник/09-00/Пётр.wav", b"RIFF" + b"\x00" * 100)
        job = _apply(c, _scan(c))
        rep = job["result"]["report"][0]
        assert rep["outcome"] == "isolated" and any("изолированную" in x for x in rep["lines"])
        login(c, "root")
        titles = [p["title"] for p in _protocols(c)]
        assert "Восстановлено: Бухгалтерия/2026-09-01, Вторник/09-00" in titles
        again = _apply(c, _scan(c))
        assert _count(c, Meeting, Meeting.end_reason == "restored_unverified") == 1, "повтор по той же папке вторую встречу не создаёт"
        assert again["result"]["counts"]["isolated"] == 1


def _protocols(c):
    async def go():
        async with c.app_obj.state.session_maker() as db:
            return [{"title": p.title} for p in (await db.execute(select(Protocol))).scalars()]
    return c.portal.call(go)
