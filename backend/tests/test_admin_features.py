"""Админка и протоколы: настройки/секреты, пользователи, экспорт в хранилище, краткий протокол с обезличиванием,
запись/пауза, сроки хранения."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.models import AppSetting, AuditLog, Meeting, MeetingChatMessage, Protocol, Recording, Room, TranscriptSegment, utcnow
from app.workers.retention import run_retention_once

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_transcripts import _feed_and_consume, _join, _segment


def anon_ok(req: httpx.Request) -> httpx.Response:
    text = json.loads(req.content)["text"]
    return httpx.Response(200, json={"ok": True, "text": text.replace("Alice A", "[ФИО_1]").replace("Bob B", "[ФИО_2]"),
                                     "report": {"total_replaced": 2}, "verification": {"clean": True}})


# -------------------------------------------------------------------------------- настройки
def test_settings_require_admin_and_never_expose_secrets(client):
    login(client, "alice")
    assert client.get("/api/v1/admin/settings").status_code == 403
    assert client.put("/api/v1/admin/settings/llm", json={"api_key": "x"}).status_code == 403

    out = put_settings(client, "llm", enabled=True, type="openai", model="gpt-x", api_key="sk-very-secret")
    assert out["api_key_set"] is True and "api_key" not in out
    assert "sk-very-secret" not in client.get("/api/v1/admin/settings").text

    async def _rows():
        async with client.app_obj.state.session_maker() as db:
            row = (await db.execute(select(AppSetting).where(AppSetting.key == "llm.api_key"))).scalar_one()
            audit = (await db.execute(select(AuditLog).where(AuditLog.action == "settings.llm.update"))).scalars().all()
            return row, audit

    row, audit = client.portal.call(_rows)
    assert row.is_secret and "sk-very-secret" not in row.value, "секрет в БД только зашифрованным"
    assert "sk-very-secret" not in json.dumps([a.details for a in audit]) and audit[0].details["protected_changed"] == ["api_key"]

    # отсутствие поля — не менять; пустая строка — очистить
    assert put_settings(client, "llm", model="gpt-y")["api_key_set"] is True
    assert put_settings(client, "llm", api_key="", enabled=False)["api_key_set"] is False


def test_settings_validation_errors_are_422(client):
    login(client, "root")
    assert client.put("/api/v1/admin/settings/anonymizer", json={"enabled": True, "base_url": "http://plain.test"}).status_code == 422
    assert client.put("/api/v1/admin/settings/storage", json={"enabled": True, "mode": "smb"}).status_code == 422
    assert client.put("/api/v1/admin/settings/general", json={"timezone": "Mars/Base"}).status_code == 422
    assert client.put("/api/v1/admin/settings/llm", json={"bogus": 1}).status_code == 422
    assert client.put("/api/v1/admin/settings/nope", json={}).status_code == 404


def test_screen_profile_reaches_clients_on_join(client):
    put_settings(client, "screen", profile="motion", share_audio=True)
    room = make_room(client)
    login(client, "alice")
    body = client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()
    assert {k: body["client"][k] for k in ("screen_profile", "screen_share_audio", "one_sharer_at_a_time", "can_moderate", "is_guest", "mute_on_join", "welcome_message")} == {
        "screen_profile": "motion", "screen_share_audio": True, "one_sharer_at_a_time": False, "can_moderate": False, "is_guest": False, "mute_on_join": False, "welcome_message": None}


# --------------------------------------------------------------------------- пользователи
def test_deactivated_user_loses_session_and_cannot_login(client):
    login(client, "bob")
    bob_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    login(client, "root")
    assert client.patch(f"/api/v1/admin/users/{bob_id}", json={"is_active": False}).status_code == 200
    root_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    assert client.patch(f"/api/v1/admin/users/{root_id}", json={"is_active": False}).status_code == 409
    login(client, "alice")
    assert client.get("/api/v1/admin/users").status_code == 403

    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"login": "bob", "password": "bob-pass"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "account_disabled_local"
    login(client, "root")
    client.patch(f"/api/v1/admin/users/{bob_id}", json={"is_active": True})
    login(client, "bob")


def test_active_session_is_cut_when_user_is_deactivated(client):
    login(client, "bob")
    bob_cookies = dict(client.cookies)
    bob_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    login(client, "root")
    client.patch(f"/api/v1/admin/users/{bob_id}", json={"is_active": False})
    client.cookies.clear()
    client.cookies.update(bob_cookies)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_directory_search_for_acl_is_admin_only(client):
    login(client, "alice")
    assert client.get("/api/v1/admin/directory/search", params={"kind": "group", "q": "staff"}).status_code == 403
    login(client, "root")
    groups = client.get("/api/v1/admin/directory/search", params={"kind": "group", "q": "staff"}).json()
    assert groups and groups[0]["ref"].startswith("cn=staff")
    users = client.get("/api/v1/admin/directory/search", params={"kind": "user", "q": "ali"}).json()
    assert users[0]["name"] == "Alice A"


def test_system_status_for_admin(client):
    login(client, "root")
    s = client.get("/api/v1/admin/system").json()
    assert s["version"] and s["checks"]["postgres"]["ok"] and s["checks"]["redis"]["ok"] and s["master_key_ok"] is True
    assert s["checks"]["livekit"]["ok"] is False and s["checks"]["asr"]["ok"] is False  # в тесте их нет
    assert "meetings" in s["counts"]


# --------------------------------------------------------------------------------- запись
def test_transcription_and_audio_recording_are_toggled_separately_and_tell_asr(client):
    room = make_room(client, record_audio=True)
    a = _join(client, "alice", room["id"])
    app = client.app_obj
    login(client, "alice")
    mid = a["meeting_id"]
    assert a["transcription"] is True and a["recording"] is False  # транскрибация идёт с начала; аудио пишется только вручную или по «автозаписи»

    def asr():
        return json.loads(client.portal.call(lambda: app.state.redis.hget("asr:sessions", mid)))

    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": mid})
        assert ws.receive_json()["type"] == "subscribed"
        assert client.post(f"/api/v1/meetings/{mid}/transcription", json={"enabled": False}).json() == {"enabled": False}
        assert ws.receive_json() == {"type": "transcription_changed", "enabled": False}
        assert asr()["transcribe"] is False and asr()["record_audio"] is False        # запись аудио транскрибацией не включается
        assert client.post(f"/api/v1/meetings/{mid}/recording", json={"enabled": True}).json() == {"enabled": True}
        assert ws.receive_json() == {"type": "recording_changed", "enabled": True}
        assert asr()["transcribe"] is False and asr()["record_audio"] is True          # и останов транскрибации не выключает запись
        client.post(f"/api/v1/meetings/{mid}/transcription", json={"enabled": True})
        assert asr()["transcribe"] is True and asr()["record_audio"] is True
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/recording", json={"enabled": False}).status_code == 404
    assert client.post(f"/api/v1/meetings/{mid}/transcription", json={"enabled": False}).status_code in (403, 404)
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/recording", json={"enabled": "yes"}).status_code in (404, 422)
    assert client.post(f"/api/v1/meetings/{mid}/transcription", json={"enabled": "yes"}).status_code == 422


def test_audio_recording_cannot_be_enabled_when_room_forbids_it_and_transcription_when_room_disables_it(client):
    room = make_room(client, record_audio=False)
    a = _join(client, "alice", room["id"])
    r = client.post(f"/api/v1/meetings/{a['meeting_id']}/recording", json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "recording_forbidden"
    room2 = make_room(client, transcription_enabled=False)
    b = _join(client, "alice", room2["id"])
    assert b["transcription"] is False
    r = client.post(f"/api/v1/meetings/{b['meeting_id']}/transcription", json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "transcription_forbidden"


# ------------------------------------------------------------------- экспорт и протоколы
def _two_person_meeting(client, **room_over):
    room = make_room(client, **room_over)
    a = _join(client, "alice", room["id"])
    b = _join(client, "bob", room["id"])
    _feed_and_consume(client, [
        _segment(a["meeting_id"], a["identity"], "Добрый день, Иван Петрович просил отчёт", offset=0),
        _segment(a["meeting_id"], b["identity"], "Отчёт будет к пятнице", offset=5),
    ])
    return room, a


def _drain(client):
    client.portal.call(client.app_obj.state.protocols.drain)


def test_finished_meeting_is_exported_with_structure_and_participants_header(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        put_settings(c, "storage", enabled=True, mode="local", local_path=str(tmp_path / "out"))
        _room, a = _two_person_meeting(c, name="Переговорка Север")
        login(c, "alice")
        assert c.post(f"/api/v1/meetings/{a['meeting_id']}/end").status_code == 204
        _drain(c)
        files = list((tmp_path / "out").rglob("protocol.txt"))
        assert len(files) == 1
        rel = files[0].relative_to(tmp_path / "out").parts
        assert rel[0] == "Переговорка Север" and ", " in rel[1] and len(rel[2]) == 5 and rel[2][2] == "-"  # комната/дата, день/ЧЧ-ММ
        text = files[0].read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[0] == "Переговорка: Переговорка Север" and any(l == "Участвовали: Alice A, Bob B" for l in lines)
        assert "Alice A: Добрый день" in text and "Bob B: Отчёт будет к пятнице" in text
        assert text.index("Alice A:") < text.index("Bob B:")


def test_two_meetings_same_minute_do_not_overwrite(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        put_settings(c, "storage", enabled=True, mode="local", local_path=str(tmp_path / "out"))
        room = make_room(c, name="Одна")
        for _ in range(2):
            a = _join(c, "alice", room["id"])
            login(c, "alice")
            c.post(f"/api/v1/meetings/{a['meeting_id']}/end")
            _drain(c)
        assert len(list((tmp_path / "out").rglob("protocol.txt"))) == 2


def test_storage_test_endpoint(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory) as c:
        login(c, "root")
        assert c.post("/api/v1/admin/settings/storage/test").json()["ok"] is False  # выключено
        put_settings(c, "storage", enabled=True, local_path=str(tmp_path / "t"))
        r = c.post("/api/v1/admin/settings/storage/test").json()
        assert r["ok"] is True, r


def test_summary_goes_through_anonymizer_and_llm_only_sees_masked_text(tmp_path, directory):
    llm_seen: list[dict] = []

    def llm(req: httpx.Request) -> httpx.Response:
        llm_seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Решение: [ФИО_2] подготовит отчёт к пятнице."}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 8}})

    s = make_settings(tmp_path)
    with running_app(s, directory, transports={"anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm)}) as c:
        put_settings(c, "storage", enabled=True, local_path=str(tmp_path / "out"))
        put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
        put_settings(c, "llm", enabled=True, type="openai_compatible", base_url="https://llm.test/v1", model="m", api_key="k")
        put_settings(c, "protocol", instructions="ГЛОБАЛЬНАЯ ИНСТРУКЦИЯ")
        room, a = _two_person_meeting(c, protocol_instructions="ИНСТРУКЦИЯ КОМНАТЫ")
        login(c, "alice")
        assert c.post(f"/api/v1/meetings/{a['meeting_id']}/protocols", json={"kind": "protocol"}).status_code == 409  # встреча идёт
        c.post(f"/api/v1/meetings/{a['meeting_id']}/end")
        _drain(c)
        r = c.post(f"/api/v1/meetings/{a['meeting_id']}/protocols", json={"kind": "protocol"})
        assert r.status_code == 202
        _drain(c)
        pid = r.json()["protocol_id"]
        got = c.get(f"/api/v1/meetings/{a['meeting_id']}/protocols/{pid}").json()
        assert got["status"] == "ready" and "подготовит отчёт" in got["content"] and got["model"] == "m"

        sent = json.dumps(llm_seen[0], ensure_ascii=False)
        assert "Alice A" not in sent and "Bob B" not in sent, "имена не должны уходить в LLM"
        assert "[ФИО_1]" in sent and "ГЛОБАЛЬНАЯ ИНСТРУКЦИЯ" in sent and "ИНСТРУКЦИЯ КОМНАТЫ" in sent
        assert list((tmp_path / "out").rglob("official-protocol.md")), "краткий протокол выгружен рядом со стенограммой"
        assert len(c.get(f"/api/v1/meetings/{a['meeting_id']}/protocols").json()) == 1
        login(c, "carol")
        assert c.get(f"/api/v1/meetings/{a['meeting_id']}/protocols/{pid}").status_code == 404


def test_summary_fails_closed_when_anonymizer_rejects(tmp_path, directory):
    llm_calls = []

    def dirty(req):
        return httpx.Response(200, json={"ok": True, "text": "x", "verification": {"clean": False, "leaked_values_count": 2}})

    def llm(req):
        llm_calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "не должно вызываться"}}]})

    with running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(dirty), "llm": httpx.MockTransport(llm)}) as c:
        put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
        put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")
        _room, a = _two_person_meeting(c)
        login(c, "alice")
        c.post(f"/api/v1/meetings/{a['meeting_id']}/end")
        _drain(c)
        pid = c.post(f"/api/v1/meetings/{a['meeting_id']}/protocols", json={"kind": "protocol"}).json()["protocol_id"]
        _drain(c)
        got = c.get(f"/api/v1/meetings/{a['meeting_id']}/protocols/{pid}").json()
        assert got["status"] == "failed" and "verification.clean" in got["error"]
        assert llm_calls == [], "при отказе обезличивания данные в LLM не отправляются"


def test_summary_requires_configured_services_and_auto_generate(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory, transports={
            "anonymizer": httpx.MockTransport(anon_ok),
            "llm": httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "Авто-протокол"}}]}))}) as c:
        _room, a = _two_person_meeting(c)
        login(c, "alice")
        c.post(f"/api/v1/meetings/{a['meeting_id']}/end")
        assert c.post(f"/api/v1/meetings/{a['meeting_id']}/protocols", json={"kind": "protocol"}).status_code == 409  # не настроено
        put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
        put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")
        put_settings(c, "protocol", auto_generate=True)
        room = make_room(c, name="Авто")
        a2 = _join(c, "alice", room["id"])
        _feed_and_consume(c, [_segment(a2["meeting_id"], a2["identity"], "Текст для авто-протокола")])
        c.post(f"/api/v1/meetings/{a2['meeting_id']}/end")
        _drain(c)
        items = c.get(f"/api/v1/meetings/{a2['meeting_id']}/protocols").json()
        assert len(items) == 1 and items[0]["status"] == "ready" and items[0]["created_by"] == "auto"


def test_transcript_txt_download(client):
    _room, a = _two_person_meeting(client)
    login(client, "bob")
    r = client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript.txt")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "Участвовали: Alice A, Bob B" in r.text and "Bob B: Отчёт будет к пятнице" in r.text
    login(client, "carol")
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript.txt").status_code == 404


def test_admin_can_force_end_and_list_active_meetings(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    login(client, "root")
    assert [m["id"] for m in client.get("/api/v1/admin/meetings", params={"active": True}).json()] == [a["meeting_id"]]
    assert client.post(f"/api/v1/admin/meetings/{a['meeting_id']}/end").status_code == 204
    assert client.get("/api/v1/admin/meetings", params={"active": True}).json() == []
    login(client, "alice")
    assert client.post(f"/api/v1/admin/meetings/{a['meeting_id']}/end").status_code == 403


# --------------------------------------------------------------------------- сроки хранения
def test_retention_removes_expired_text_and_audio_but_keeps_the_rest(client):
    app = client.app_obj
    keep = make_room(client, name="Бессрочно", text_retention_days=None, audio_retention_days=None)
    expire = make_room(client, name="Месяц", text_retention_days=30, audio_retention_days=7)
    rec_dir = app.state.settings.recordings_path

    async def _seed():
        from pathlib import Path
        old = utcnow() - timedelta(days=40)
        out = {}
        async with app.state.session_maker() as db:
            for tag, room in (("keep", keep), ("expire", expire)):
                m = Meeting(room_id=uuid.UUID(room["id"]), livekit_room=f"m-{uuid.uuid4().hex}", started_at=old,
                            ended_at=old + timedelta(hours=1), end_reason="manual")
                db.add(m)
                await db.flush()
                db.add(TranscriptSegment(segment_uid=uuid.uuid4(), meeting_id=m.id, room_id=m.room_id, participant_identity="u-x",
                                         started_at=old, ended_at=old, text="старое"))
                db.add(Protocol(meeting_id=m.id, kind="summary", status="ready", content="итог"))
                db.add(MeetingChatMessage(meeting_id=m.id, author_name="Alice", text="старый чат"))
                f = Path(rec_dir) / tag / "a.wav"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"RIFF")
                db.add(Recording(meeting_id=m.id, room_id=m.room_id, participant_identity="u-x", path=f"{tag}/a.wav",
                                 size_bytes=4, created_at=old))
                out[tag] = m.id
            await db.commit()
        return out

    ids = client.portal.call(_seed)
    stats = client.portal.call(lambda: run_retention_once(app.state.session_maker, app.state.protocols))
    assert stats == {"segments": 1, "protocols": 1, "recordings": 1, "chat": 1, "whiteboards": 0}

    async def _left():
        async with app.state.session_maker() as db:
            segs = (await db.execute(select(TranscriptSegment.meeting_id))).scalars().all()
            meets = (await db.execute(select(Meeting.id))).scalars().all()
            recs = (await db.execute(select(Recording.path))).scalars().all()
            return segs, meets, recs

    segs, meets, recs = client.portal.call(_left)
    assert segs == [ids["keep"]] and set(meets) == set(ids.values()) and recs == ["keep/a.wav"]
    import os
    assert os.path.exists(os.path.join(rec_dir, "keep", "a.wav")) and not os.path.exists(os.path.join(rec_dir, "expire", "a.wav"))
    assert client.portal.call(lambda: run_retention_once(app.state.session_maker, app.state.protocols)) == {"segments": 0, "protocols": 0, "recordings": 0, "chat": 0, "whiteboards": 0}
    assert Room  # импорт используется в сидировании


def test_audio_recording_is_finalized_registered_and_admin_only(tmp_path, directory):
    from pathlib import Path

    s = make_settings(tmp_path, data_dir=str(tmp_path / "data"))
    with running_app(s, directory) as c:
        c.app_obj.state.protocols.flush_delay = 0
        room = make_room(c, name="Запись", record_audio=True)
        a = _join(c, "alice", room["id"])
        pcm_dir = Path(s.recordings_path) / a["livekit_room"]
        pcm_dir.mkdir(parents=True)
        (pcm_dir / f"{a['identity']}.pcm").write_bytes(b"\x02\x00" * 16000 * 3)  # 3 с звука, как пишет ASR
        login(c, "alice")
        assert c.post(f"/api/v1/meetings/{a['meeting_id']}/end").status_code == 204
        _drain(c)
        assert not pcm_dir.exists(), "сырой PCM преобразован и удалён"

        assert c.get(f"/api/v1/meetings/{a['meeting_id']}/recordings").status_code == 403  # обычному пользователю — нельзя
        login(c, "root")
        recs = c.get(f"/api/v1/meetings/{a['meeting_id']}/recordings").json()
        assert len(recs) == 1 and recs[0]["duration_s"] == 3 and recs[0]["name"] == "Alice A.wav"
        dl = c.get(f"/api/v1/meetings/{a['meeting_id']}/recordings/{recs[0]['id']}")
        assert dl.status_code == 200 and dl.content[:4] == b"RIFF" and len(dl.content) == 44 + 96000
        assert any(r["room"] == "Запись" for r in c.get("/api/v1/admin/recordings").json())
        login(c, "alice")
        assert c.get(f"/api/v1/meetings/{a['meeting_id']}/recordings/{recs[0]['id']}").status_code == 403


def test_storage_directory_is_validated_on_save_and_not_stored_when_invalid(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory) as c:
        login(c, "root")
        bad = str(tmp_path.parent / "outside")
        r = c.put("/api/v1/admin/settings/storage", json={"enabled": True, "local_path": bad})
        assert r.status_code == 422 and "внутри" in r.json()["detail"]
        assert c.get("/api/v1/admin/settings/storage").json()["enabled"] is False, "невалидная настройка не сохраняется"
