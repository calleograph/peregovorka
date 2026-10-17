"""Карта разговора в письме: всегда отдельный автономный HTML-файл, какие бы форматы документов ни выбрали. SMTP подменён (как в test_mail.py)."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.models import ConversationMap, TranscriptSegment
from app.services import map_export

from .conftest import login, make_room
from .test_mail import LEADERS, add_profile, fake_smtp, finished_meeting, run_queue  # noqa: F401

TITLE = "УникальнаяТемаКарты"
EVIL = "</script><script>window.pwned=1</script>"


def add_map(client, mid: str) -> None:
    data = {"topics": [{"id": "t1", "title": TITLE, "category": "dev", "note": EVIL, "segments": []}]}

    async def add():
        async with client.app_obj.state.session_maker() as db:
            db.add(ConversationMap(meeting_id=uuid.UUID(mid), status="ready", data=data, meta={"model": "m", "secret_internal": "не должно попасть"}))
            await db.commit()

    client.portal.call(add)


def add_transcript(client, mid: str) -> None:
    async def add():
        from app.models import Meeting

        async with client.app_obj.state.session_maker() as db:
            m = await db.get(Meeting, uuid.UUID(mid))
            for i in range(7):
                db.add(TranscriptSegment(segment_uid=uuid.uuid4(), meeting_id=m.id, room_id=m.room_id, participant_identity="u-x", started_at=m.started_at,
                                         ended_at=m.started_at, text=f"Реплика номер {i}"))
            await db.commit()

    client.portal.call(add)


def meeting_with_map(client, with_map=True, with_transcript=True):
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    mid = finished_meeting(client, room)
    if with_map:
        add_map(client, mid)
    if with_transcript:
        add_transcript(client, mid)
    login(client, "carol")
    return mid


def send(client, mid, kinds, formats):
    return client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": kinds, "formats": formats, "emails": ["alice@corp.test"]})


def sent_files(client, fake_smtp) -> dict:
    assert run_queue(client) == 1
    assert len(fake_smtp.sent) == 1
    return {p.get_filename(): p for p in fake_smtp.sent[0].iter_attachments()}


@pytest.mark.parametrize("kinds,formats,expect_docs", [
    (["map"], ["docx"], 0),
    (["map"], ["html"], 0),
    (["protocol", "transcript", "map"], ["docx"], 2),
    (["protocol", "transcript", "map"], ["pdf"], 2),
    (["protocol", "summary", "transcript", "map"], ["docx", "pdf", "txt"], 9),
])
def test_map_is_always_one_standalone_html_attachment(client, fake_smtp, kinds, formats, expect_docs):
    mid = meeting_with_map(client)
    r = send(client, mid, kinds, formats)
    assert r.status_code == 200 and r.json()["kinds"] == kinds and r.json()["unavailable"] == []
    files = sent_files(client, fake_smtp)
    maps = {n: p for n, p in files.items() if n.startswith("Карта разговора")}
    assert len(maps) == 1, list(files)                                    # карта — ровно один раз, независимо от числа форматов
    (name, part), = maps.items()
    assert name.endswith(".html") and part.get_content_type() == "text/html"
    assert len(files) - 1 == expect_docs
    assert all(n.split(".")[-1] in formats for n in files if n not in maps)   # форматы документов применены только к документам


def test_map_html_is_self_contained_safe_and_has_no_internal_data(client, fake_smtp):
    mid = meeting_with_map(client)
    assert send(client, mid, ["map"], ["docx"]).status_code == 200
    (part,) = sent_files(client, fake_smtp).values()
    html = part.get_content() if isinstance(part.get_content(), str) else part.get_payload(decode=True).decode("utf-8")
    assert TITLE in html and 'id="pg-map-data"' in html                    # данные карты внутри файла
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    assert "<script src" not in html and "<link" not in html and 'href="/' not in html and "/api/" not in html.split('id="pg-map-data"')[0]
    assert "</script><script>window.pwned=1" not in html                  # данные не могут закрыть тег и выполниться
    assert "secret_internal" not in html and "не должно попасть" not in html   # во внешний файл идут только разрешённые сведения о формировании


def test_unready_map_is_unavailable_and_nothing_is_sent_for_it(client, fake_smtp):
    mid = meeting_with_map(client, with_map=False)
    pv = client.get(f"/api/v1/meetings/{mid}/delivery").json()
    k = next(m for m in pv["available_kinds"] if m["kind"] == "map")
    assert k["available"] is False and "Карта разговора" in k["reason"]    # причина показана пользователю
    assert send(client, mid, ["map"], ["html"]).status_code == 409        # только карта и она не готова — отправлять нечего
    r = send(client, mid, ["protocol", "map"], ["docx"])
    assert r.status_code == 200 and r.json()["unavailable"] == ["map"] and r.json()["kinds"] == ["protocol"]
    files = sent_files(client, fake_smtp)
    assert not any(n.startswith("Карта разговора") for n in files)


def test_export_failure_is_an_error_not_a_false_success(client, fake_smtp, monkeypatch):
    mid = meeting_with_map(client)

    def boom(*a, **k):
        raise RuntimeError("export broken")

    monkeypatch.setattr(map_export, "standalone_html", boom)
    with pytest.raises(RuntimeError):
        send(client, mid, ["protocol", "map"], ["docx"])
    assert fake_smtp.sent == [] and run_queue(client) == 0


def test_viewer_copy_in_backend_matches_the_frontend_viewer():
    src = Path(__file__).resolve().parents[2] / "frontend" / "public" / "mapview"
    if not src.exists():
        pytest.skip("каталог frontend недоступен")
    for name in ("viewer.css", "viewer.js"):
        assert (map_export.ASSETS / name).read_bytes() == (src / name).read_bytes(), f"{name} разошёлся с frontend/public/mapview — скопируйте файл"
