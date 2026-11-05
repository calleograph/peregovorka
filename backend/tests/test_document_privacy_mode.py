"""Режим обезличивания одного документа: выбор организатора не меняет настройки комнаты и сервера, не влияет на другие документы и встречи, обязательное обезличивание снимает только администратор."""
from __future__ import annotations

import json
import uuid

import httpx

from app.models import Room

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_admin_features import _drain, _two_person_meeting, anon_ok
from .test_transcripts import _feed_and_consume, _join, _segment


def _app(tmp_path, directory, seen):
    def llm(req: httpx.Request) -> httpx.Response:
        seen.append(json.dumps(json.loads(req.content), ensure_ascii=False))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Решение принято."}}], "usage": {"prompt_tokens": 10, "completion_tokens": 8}})
    return running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm)})


def _llm(c):
    put_settings(c, "llm", enabled=True, type="openai_compatible", base_url="https://llm.test/v1", model="m", api_key="k")


def _doc(c, mid, **body):
    r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", **body})
    assert r.status_code == 202, r.text
    _drain(c)
    return c.get(f"/api/v1/meetings/{mid}/protocols/{r.json()['protocol_id']}").json()


def _ended(c, **room):
    _room, a = _two_person_meeting(c, **room)
    mid = a["meeting_id"]
    login(c, "alice")
    c.post(f"/api/v1/meetings/{mid}/end")
    _drain(c)
    return _room, mid


def _plan(c, mid, **q):
    return c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction", params={"kind": "protocol", **q}).json()["plan"]


def test_choice_applies_to_one_document_and_changes_no_settings(tmp_path, directory):
    seen: list[str] = []
    with _app(tmp_path, directory, seen) as c:
        put_settings(c, "anonymizer", enabled=True)
        _llm(c)
        room, mid = _ended(c, anonymize_mode="on")
        assert _doc(c, mid)["generation"]["anonymized"] is True and "Alice A" not in seen[-1]

        got = _doc(c, mid, anonymize="off")
        assert "Alice A" in seen[-1], "без обезличивания модель получает настоящие имена"
        assert got["generation"]["anonymized"] is False and got["generation"]["anonymize_choice"] == "off"
        plan = _plan(c, mid)
        assert plan["anonymize"] is True and plan["anonymize_default"] is True, "настройка комнаты не изменилась"
        assert plan["anonymize_last"] == "off", "при повторном открытии окно знает прошлый выбор"
        assert _plan(c, mid, anonymize="off")["anonymize"] is False

        async def mode():
            async with c.app_obj.state.session_maker() as db:
                return (await db.get(Room, uuid.UUID(room["id"]))).anonymize_mode
        assert c.portal.call(mode) == "on"

        _room2, other = _ended(c, anonymize_mode="on")
        assert _doc(c, other)["generation"]["anonymized"] is True and "Alice A" not in seen[-1], "другая встреча идёт по своим настройкам"
        assert _doc(c, mid)["generation"]["anonymized"] is True, "выбор не прилипает к встрече: без него снова действуют настройки"


def test_room_without_anonymization_can_mask_one_document(tmp_path, directory):
    seen: list[str] = []
    with _app(tmp_path, directory, seen) as c:
        put_settings(c, "anonymizer", enabled=True)
        _llm(c)
        _room, mid = _ended(c)
        assert _doc(c, mid)["generation"]["anonymized"] is False and "Alice A" in seen[-1]
        assert _doc(c, mid, anonymize="on")["generation"]["anonymized"] is True and "Alice A" not in seen[-1]


def test_required_anonymization_is_lifted_only_by_the_server_admin(tmp_path, directory):
    seen: list[str] = []
    with _app(tmp_path, directory, seen) as c:
        put_settings(c, "anonymizer", required=True)
        _llm(c)
        _room, mid = _ended(c)
        assert _plan(c, mid)["anonymize_can_off"] is False
        n = len(seen)
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "anonymize": "off"})
        assert r.status_code == 403 and "обязательно" in r.json()["detail"] and len(seen) == n
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "anonymize": "maybe"}).status_code == 422
        put_settings(c, "anonymizer", required=False, enabled=False)
        login(c, "alice")
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "anonymize": "on"}).status_code == 403, "выключенное администратором включить нельзя"


def test_map_is_built_on_masked_text_in_both_modes(tmp_path, directory):
    from .test_conv_map import topics_llm

    seen: list[str] = []

    def llm(req: httpx.Request) -> httpx.Response:
        seen.append(json.dumps(json.loads(req.content), ensure_ascii=False))
        return topics_llm(req)

    with running_app(make_settings(tmp_path), directory, transports={"llm": httpx.MockTransport(llm)}) as c:
        put_settings(c, "anonymizer", enabled=True)
        _llm(c)
        room = make_room(c, anonymize_mode="on")
        a = _join(c, "alice", room["id"])
        _join(c, "bob", room["id"])
        mid = a["meeting_id"]
        _feed_and_consume(c, [_segment(mid, a["identity"], t, offset=i * 60 + 5, dur=40) for i, t in enumerate(("Принято решение по бюджету", "Вернёмся к вопросу позже"))])
        login(c, "alice")
        assert c.post(f"/api/v1/meetings/{mid}/end").status_code == 204
        _drain(c)
        st = c.get(f"/api/v1/meetings/{mid}/map").json()
        assert st["plan"]["anonymize_default"] is True and st["plan"]["anonymize_can_off"] is True
        assert c.post(f"/api/v1/meetings/{mid}/map").status_code == 202
        _drain(c)
        st = c.get(f"/api/v1/meetings/{mid}/map").json()
        assert seen and not any("Alice A" in x or "Bob B" in x for x in seen), "в модель ушёл только обезличенный текст"
        assert st["status"] == "ready" and st["meta"].get("anonymized") is True, st.get("error")
        body = json.dumps(st["data"], ensure_ascii=False)
        assert st["data"]["topics"] and st["data"]["speakers"], "структура карты на месте: темы, участники, время"
        assert "Alice A" not in body and "Bob B" not in body, "на карте единые обозначения"
        assert [s["name"] for s in st["data"]["speakers"]] != ["Alice A", "Bob B"]
        n = len(seen)
        assert c.post(f"/api/v1/meetings/{mid}/map", json={"anonymize": "off"}).status_code == 202
        _drain(c)
        st = c.get(f"/api/v1/meetings/{mid}/map").json()
        assert any("Alice A" in x for x in seen[n:]), "выбран режим без обезличивания"
        assert "Alice A" in json.dumps(st["data"], ensure_ascii=False)
        assert st["meta"].get("anonymize_choice") == "off" and st["meta"].get("anonymized") is False
        assert st["plan"]["anonymize_last"] == "off", "при повторном открытии выбор сохранён"
        assert c.post(f"/api/v1/meetings/{mid}/map", json={"anonymize": "maybe"}).status_code == 422


def test_map_lines_keep_order_time_links_and_one_alias_per_person():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from zoneinfo import ZoneInfo

    from app.privacy.engine import Anonymizer, Policy, PrivacyError
    from app.services.conv_map import build_lines, mask_lines

    t0 = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
    rows = [SimpleNamespace(id=i + 10, started_at=t0.replace(second=i * 5), ended_at=t0.replace(second=i * 5 + 3), text=t, speaker_name=who, user=None, participant=None)
            for i, (who, t) in enumerate((("Мишунин Алексей Петрович", "Предлагаю перенести запуск."), ("Сидорова Анна", "Мишунин, подготовьте план."), ("Мишунин Алексей Петрович", "Хорошо, план будет к пятнице.")))]
    import app.services.conv_map as cm
    orig = cm.author_name
    cm.author_name = lambda r: r.speaker_name
    try:
        lines = build_lines(rows, t0, ZoneInfo("UTC"))
        anon = Anonymizer(Policy.from_profile("basic"), ["Мишунин Алексей Петрович", "Сидорова Анна"])
        head, masked, res = mask_lines(anon, ["Участвовали: Мишунин Алексей Петрович, Сидорова Анна"], lines)
    finally:
        cm.author_name = orig
    assert [m.seg_id for m in masked] == [10, 11, 12] and [m.rel for m in masked] == [m.rel for m in lines]
    assert masked[0].line.speaker == masked[2].line.speaker == "Алексей М." and masked[1].line.speaker == "Анна С."
    assert "Мишунин" not in " ".join(m.line.render() for m in masked) + " ".join(head), "ни в репликах, ни в шапке"
    assert "Алексей М." in masked[1].line.text and res.replacements

    class Broken:
        def process(self, text):
            return SimpleNamespace(text=text.split("\n")[0])
    import pytest
    with pytest.raises(PrivacyError):
        mask_lines(Broken(), [], lines)


def test_transcript_can_be_downloaded_masked_and_mail_follows_the_room(tmp_path, directory):
    seen: list[str] = []
    with _app(tmp_path, directory, seen) as c:
        put_settings(c, "anonymizer", enabled=True)
        _room, mid = _ended(c, anonymize_mode="on")
        raw = c.get(f"/api/v1/meetings/{mid}/transcript.txt")
        masked = c.get(f"/api/v1/meetings/{mid}/transcript.txt", params={"anonymize": "on"})
        assert "Alice A" in raw.text and "Alice A" not in masked.text and "Отчёт будет к пятнице" in masked.text
        assert c.get(f"/api/v1/meetings/{mid}/transcript/export", params={"format": "md", "anonymize": "on"}).status_code == 200

        async def mail():
            st = c.app_obj.state
            async with st.session_maker() as db:
                from app.models import Meeting
                from app.services.mail_delivery import MATERIALS
                m = await db.get(Meeting, uuid.UUID(mid))
                return await MATERIALS["transcript"].load(st.delivery, db, m)
        got = c.portal.call(mail)
        assert got is not None and "Alice A" not in got.md
