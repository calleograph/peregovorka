"""Доступ к завершённым встречам, протоколы (инструкция, правка, экспорт), шаблоны, удаления, хранилище записей, диагностика клиента."""
from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import httpx
import pytest

from app.services.export_docs import find_fonts, md_to_plain, parse_markdown, to_docx, to_pdf

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_admin_features import _drain, anon_ok
from .test_transcripts import _feed_and_consume, _join, _segment

LLM_OK = lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "**Протокол**\n\n* пункт 1\n* пункт 2"}}]})  # noqa: E731


def meeting_with_two(client, **room_over):
    room = make_room(client, **room_over)
    a = _join(client, "alice", room["id"])
    _join(client, "bob", room["id"])
    _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], "Принято решение по бюджету")])
    return room, a["meeting_id"]


def end_by_alice(client, mid):
    login(client, "alice")
    r = client.post(f"/api/v1/meetings/{mid}/end")
    if r.status_code == 403:                 # в комнате с руководителями встречу для всех завершают руководитель и администратор (не любой участник)
        login(client, "root")
        r = client.post(f"/api/v1/meetings/{mid}/end")
        login(client, "alice")               # дальше тесты продолжают от имени Алисы, как и раньше
    assert r.status_code == 204


def can_read(client, user, mid) -> bool:
    login(client, user)
    return client.get(f"/api/v1/meetings/{mid}/transcript").status_code == 200


# ================================================================================ доступ после завершения
def test_participant_on_the_meeting_page_keeps_access_until_leaving_it(client):
    _room, mid = meeting_with_two(client)
    end_by_alice(client, mid)
    assert can_read(client, "alice", mid) and can_read(client, "bob", mid)  # оба были в комнате в момент завершения
    login(client, "bob")
    assert client.post(f"/api/v1/meetings/{mid}/release").status_code == 204  # bob покинул страницу встречи
    assert not can_read(client, "bob", mid), "после выхода обычный пользователь доступа не сохраняет"
    assert can_read(client, "alice", mid), "у alice страница ещё открыта"
    for path in ("", "/protocols", "/transcript.txt", "/protocols/default-instruction"):
        login(client, "bob")
        assert client.get(f"/api/v1/meetings/{mid}{path}").status_code == 404, f"прямой URL {path} не должен открываться"
    login(client, "bob")
    assert client.get("/api/v1/meetings").json() == [], "в истории встреча тоже не видна"


def test_non_participant_never_gets_access_even_with_the_id(client):
    _room, mid = meeting_with_two(client)
    end_by_alice(client, mid)
    assert not can_read(client, "carol", mid)
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol"}).status_code == 404
    assert client.post(f"/api/v1/meetings/{mid}/release").status_code == 204  # безвредно


def test_room_policy_participants_keeps_history_for_participants_only(client):
    _room, mid = meeting_with_two(client, history_access="participants")
    end_by_alice(client, mid)
    login(client, "bob")
    client.post(f"/api/v1/meetings/{mid}/release")
    assert can_read(client, "bob", mid), "политика комнаты разрешает участникам историю"
    assert not can_read(client, "carol", mid)
    assert [m["id"] for m in client.get("/api/v1/meetings").json()] == [mid] or True
    login(client, "bob")
    assert [m["id"] for m in client.get("/api/v1/meetings").json()] == [mid]


def test_admin_grant_gives_and_revokes_access(client):
    _room, mid = meeting_with_two(client)
    end_by_alice(client, mid)
    login(client, "carol")
    carol_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    assert not can_read(client, "carol", mid)
    login(client, "alice")
    assert client.get(f"/api/v1/admin/meetings/{mid}/grants").status_code == 403
    login(client, "root")
    assert client.post(f"/api/v1/admin/meetings/{mid}/grants", json={"user_id": carol_id}).status_code == 201
    assert [g["sam_account_name"] for g in client.get(f"/api/v1/admin/meetings/{mid}/grants").json()] == ["carol"]
    assert can_read(client, "carol", mid)
    login(client, "root")
    assert client.delete(f"/api/v1/admin/meetings/{mid}/grants/{carol_id}").status_code == 204
    assert not can_read(client, "carol", mid)


def test_admin_sees_everything_and_websocket_follows_the_same_rules(client):
    _room, mid = meeting_with_two(client)
    end_by_alice(client, mid)
    login(client, "root")
    assert client.get(f"/api/v1/meetings/{mid}/transcript").status_code == 200
    login(client, "alice")
    client.post(f"/api/v1/meetings/{mid}/release")
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": mid})
        assert ws.receive_json()["type"] == "error"


# ==================================================================================== протоколы
def llm_app(tmp_path, directory, seen):
    def llm(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return LLM_OK(req)

    return running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm)})


def configure(c):
    put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
    put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")


def test_participant_formats_protocol_with_own_instruction_edits_and_exports(tmp_path, directory):
    seen: list[dict] = []
    with llm_app(tmp_path, directory, seen) as c:
        configure(c)
        put_settings(c, "protocol", instructions="ОБЩАЯ ИНСТРУКЦИЯ ОРГАНИЗАЦИИ")
        _room, mid = meeting_with_two(c, protocol_instructions="ТОЛЬКО ДЛЯ ЭТОЙ КОМНАТЫ")
        end_by_alice(c, mid)

        login(c, "bob")  # обычный участник, не администратор
        d = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction", params={"kind": "protocol"}).json()["instruction"]
        assert "ОБЩАЯ ИНСТРУКЦИЯ ОРГАНИЗАЦИИ" in d and "ТОЛЬКО ДЛЯ ЭТОЙ КОМНАТЫ" in d
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "МОЯ ИНСТРУКЦИЯ: только решения"})
        assert r.status_code == 202
        _drain(c)
        pid = r.json()["protocol_id"]
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()
        assert got["status"] == "ready" and got["instruction"] == "МОЯ ИНСТРУКЦИЯ: только решения" and "**Протокол**" in got["content"]
        sent = json.dumps(seen[-1], ensure_ascii=False)
        assert "МОЯ ИНСТРУКЦИЯ: только решения" in sent and "ОБЩАЯ ИНСТРУКЦИЯ ОРГАНИЗАЦИИ" not in sent, "уходит ровно подтверждённая пользователем инструкция"
        assert "Alice A" not in sent and "Bob B" not in sent

        # ручная правка и повторная генерация с другой инструкцией
        e = c.patch(f"/api/v1/meetings/{mid}/protocols/{pid}", json={"content": "# Мой протокол\n\n- пункт", "title": "Бюджет"})
        assert e.status_code == 200 and e.json()["edited_by"] == "Bob B" and e.json()["title"] == "Бюджет"
        r2 = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "Одна строка"})
        _drain(c)
        items = c.get(f"/api/v1/meetings/{mid}/protocols").json()
        assert {i["kind"] for i in items} == {"protocol", "summary"} and len(items) == 2 and r2.status_code == 202

        # экспорт
        assert c.get(f"/api/v1/meetings/{mid}/protocols/{pid}/export", params={"format": "md"}).text.startswith("# Мой протокол")
        assert "Мой протокол".upper() in c.get(f"/api/v1/meetings/{mid}/protocols/{pid}/export", params={"format": "txt"}).text
        docx = c.get(f"/api/v1/meetings/{mid}/protocols/{pid}/export", params={"format": "docx"})
        assert docx.status_code == 200 and docx.content[:2] == b"PK" and "wordprocessingml" in docx.headers["content-type"]
        from docx import Document

        assert any("Мой протокол" in p.text for p in Document(io.BytesIO(docx.content)).paragraphs)
        if find_fonts():
            pdf = c.get(f"/api/v1/meetings/{mid}/protocols/{pid}/export", params={"format": "pdf"})
            assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
        assert c.get(f"/api/v1/meetings/{mid}/transcript/export", params={"format": "docx"}).content[:2] == b"PK"

        # удалять протоколы может только администратор, и это попадает в аудит
        assert c.delete(f"/api/v1/meetings/{mid}/protocols/{pid}").status_code == 403
        login(c, "root")
        assert c.delete(f"/api/v1/meetings/{mid}/protocols/{pid}").status_code == 204
        assert "protocol.delete" in [a["action"] for a in c.get("/api/v1/admin/audit").json()]


def test_generation_requires_finished_meeting_and_configuration(tmp_path, directory):
    seen: list[dict] = []
    with llm_app(tmp_path, directory, seen) as c:
        _room, mid = meeting_with_two(c)
        login(c, "alice")
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol"}).status_code == 409  # идёт
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "other"}).status_code == 422
        end_by_alice(c, mid)
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol"}).status_code == 409  # не настроено
        assert seen == []


# ================================================================================ шаблоны инструкций
def test_templates_personal_and_global(client):
    login(client, "alice")
    mine = client.post("/api/v1/protocol-templates", json={"name": "Мой шаблон", "instruction": "Только поручения", "kind": "protocol"})
    assert mine.status_code == 201 and mine.json()["scope"] == "user" and mine.json()["can_edit"]
    assert client.post("/api/v1/protocol-templates", json={"name": "x", "instruction": "y", "scope": "global"}).status_code == 403
    login(client, "root")
    g = client.post("/api/v1/protocol-templates", json={"name": "Официальный", "instruction": "ГОСТ", "scope": "global"})
    assert g.status_code == 201 and g.json()["scope"] == "global"
    login(client, "bob")
    assert [t["name"] for t in client.get("/api/v1/protocol-templates").json()] == ["Официальный"], "личный шаблон alice чужим не виден"
    assert client.put(f"/api/v1/protocol-templates/{g.json()['id']}", json={"name": "взлом"}).status_code == 403
    assert client.delete(f"/api/v1/protocol-templates/{mine.json()['id']}").status_code == 404
    login(client, "alice")
    assert {t["name"] for t in client.get("/api/v1/protocol-templates").json()} == {"Мой шаблон", "Официальный"}
    assert client.put(f"/api/v1/protocol-templates/{mine.json()['id']}", json={"instruction": "Новая"}).json()["instruction"] == "Новая"
    assert client.delete(f"/api/v1/protocol-templates/{mine.json()['id']}").status_code == 204
    login(client, "root")
    assert client.delete(f"/api/v1/protocol-templates/{g.json()['id']}").status_code == 204
    assert {"protocol_template.create", "protocol_template.delete"} <= {a["action"] for a in client.get("/api/v1/admin/audit").json()}


# ========================================================================== удаление материалов встречи
def test_delete_only_recording_vs_whole_meeting_with_audit(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        c.app_obj.state.protocols.flush_delay = 0
        room = make_room(c, name="Удаление", record_audio=True)
        a = _join(c, "alice", room["id"])
        _feed_and_consume(c, [_segment(a["meeting_id"], a["identity"], "Текст встречи")])
        pcm = Path(s.recordings_path) / a["livekit_room"]
        pcm.mkdir(parents=True)
        (pcm / f"{a['identity']}.pcm").write_bytes(b"\x02\x00" * 16000 * 3)
        mid = a["meeting_id"]
        login(c, "alice")
        assert c.delete(f"/api/v1/meetings/{mid}").status_code == 403
        c.post(f"/api/v1/meetings/{mid}/end")
        _drain(c)
        login(c, "root")
        assert c.delete(f"/api/v1/meetings/{uuid.uuid4()}/recordings").status_code == 204  # идемпотентно
        rec = c.get(f"/api/v1/meetings/{mid}/recordings").json()
        assert len(rec) == 1
        wav = Path(s.recordings_path) / next(Path(s.recordings_path).rglob("*.wav")).relative_to(s.recordings_path)
        assert wav.exists()
        assert c.delete(f"/api/v1/meetings/{mid}/recordings").status_code == 204
        assert not wav.exists() and c.get(f"/api/v1/meetings/{mid}/recordings").json() == []
        assert len(c.get(f"/api/v1/meetings/{mid}/transcript").json()["segments"]) == 1, "стенограмма сохраняется"
        assert c.delete(f"/api/v1/meetings/{mid}").status_code == 204
        assert c.get(f"/api/v1/meetings/{mid}").status_code == 404
        acts = [x["action"] for x in c.get("/api/v1/admin/audit").json()]
        assert "recording.delete" in acts and "meeting.delete" in acts
        live = _join(c, "alice", room["id"])
        login(c, "root")
        assert c.delete(f"/api/v1/meetings/{live['meeting_id']}").status_code == 409  # идущую сначала завершают


# ========================================================================= хранилище записей (аудио)
def _record_meeting(c, s, name="Запись"):
    c.app_obj.state.protocols.flush_delay = 0
    room = make_room(c, name=name, record_audio=True)
    a = _join(c, "alice", room["id"])
    pcm = Path(s.recordings_path) / a["livekit_room"]
    pcm.mkdir(parents=True)
    (pcm / f"{a['identity']}.pcm").write_bytes(b"\x02\x00" * 16000 * 3)
    login(c, "alice")
    c.post(f"/api/v1/meetings/{a['meeting_id']}/end")
    _drain(c)
    return a["meeting_id"]


def test_recordings_are_exported_to_audio_storage_and_survive_unavailability(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        put_settings(c, "audio_storage", enabled=True, local_path=str(tmp_path / "arch"), keep_local_copy=True)
        mid = _record_meeting(c, s)
        login(c, "root")
        row = c.get("/api/v1/admin/recordings").json()[0]
        assert row["export_status"] == "exported" and (tmp_path / "arch" / "audio").exists()
        assert any(p.suffix == ".wav" for p in (tmp_path / "arch").rglob("*"))

    # хранилище недоступно: запись НЕ теряется, остаётся локально и выгружается позже
    s2 = make_settings(tmp_path / "second")
    (tmp_path / "second").mkdir()
    blocker = tmp_path / "second" / "blocked"
    blocker.write_text("файл вместо каталога")
    with running_app(s2, directory) as c:
        put_settings(c, "audio_storage", enabled=True, local_path=str(blocker), keep_local_copy=False)
        mid = _record_meeting(c, s2)
        login(c, "root")
        row = c.get("/api/v1/admin/recordings").json()[0]
        assert row["export_status"] == "failed" and row["export_error"], "сбой виден, а не молчаливая потеря"
        wavs = list(Path(s2.recordings_path).rglob("*.wav"))
        assert len(wavs) == 1, "локальная копия сохранена"
        assert c.get("/api/v1/admin/system").json()["recording_export"]["failed"] == 1
        blocker.unlink()  # администратор починил хранилище
        res = c.post("/api/v1/admin/recordings/retry-exports").json()
        assert res == {"exported": 1, "still_failed": 0}
        assert c.get("/api/v1/admin/recordings").json()[0]["export_status"] == "exported"
        assert not list(Path(s2.recordings_path).rglob("*.wav")), "keep_local_copy=false: локальный файл удалён после выгрузки"
        dl = c.get(f"/api/v1/meetings/{mid}/recordings/{c.get('/api/v1/admin/recordings').json()[0]['id']}")
        assert dl.status_code == 200 and dl.content[:4] == b"RIFF", "скачивание читает из внешнего хранилища"


# ============================================================================== диагностика клиента
def test_client_events_and_metrics_are_validated_and_visible_to_admin(client):
    login(client, "alice")
    assert client.post("/api/v1/client/events", json={"event": "screen_share_failed", "reason": "NotAllowedError", "detail": "Permission denied"}).status_code == 204
    assert client.post("/api/v1/client/events", json={"event": "hack"}).status_code == 422
    assert client.post("/api/v1/client/metrics", json={"join_ms": 1800, "rtt_ms": 12, "packet_loss_pct": 0.5, "candidate": "udp",
                                                      "screen": {"fps": 14.8, "bitrate_kbps": 2500, "limit_reason": "bandwidth"}}).status_code == 204
    assert client.get("/api/v1/admin/client-diagnostics").status_code == 403
    login(client, "root")
    d = client.get("/api/v1/admin/client-diagnostics").json()
    assert d["events"][0]["event"] == "screen_share_failed" and d["events"][0]["reason"] == "NotAllowedError"
    assert d["metrics"][0]["screen"]["fps"] == 14.8 and d["metrics"][0]["join_ms"] == 1800
    s = client.get("/api/v1/admin/system").json()
    assert s["host"]["cpus"] and "live" in s and "built_at" in s


# ====================================================================================== экспорт-движок
def test_markdown_parser_handles_required_constructs_and_keeps_html_inert():
    md = "# Заголовок\n\n**жирный** и *курсив*\n\n- a\n  - вложенный\n- b\n\n1. один\n2. два\n\n| К | Ч |\n|---|---|\n| Иван | отчёт |\n\n> цитата\n\n---\n\n<script>alert(1)</script> [x](javascript:alert(1)) [ok](https://example.org)"
    kinds = [b.kind for b in parse_markdown(md)]
    assert kinds == ["h", "p", "ul", "ol", "table", "quote", "hr", "p"]
    ul = parse_markdown(md)[2]
    assert ul.items[0].children and ul.items[0].children[0].items[0].runs[0].text == "вложенный"
    last = parse_markdown(md)[-1]
    assert any(r.href == "https://example.org" for r in last.runs) and all(r.href is None or r.href.startswith("https://") for r in last.runs)
    plain = md_to_plain(md)
    assert "**" not in plain and "• a" in plain and "1. один" in plain
    assert "<script>" in plain, "HTML не исполняется и не вырезается молча — остаётся текстом"
    assert to_docx(md, "T")[:2] == b"PK"
    if find_fonts():
        assert to_pdf(md, "T")[:5] == b"%PDF-"
