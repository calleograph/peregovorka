"""Гости и зрители эфира: имя (обязательно), почта и согласие на материалы; вопросы зрителей к идущему эфиру; подготовка к эфиру в комнате трансляции;
автоматическая рассылка — только по настройкам комнаты; вложения чатов — до 3 МБ, только изображения и документы."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import LiveQuestion, Meeting, Protocol
from app.services.chat_files import REJECT_TEXT
from app.services.settings import ChatFilesSettings

from .conftest import login, make_room
from .test_collab_guests import guest_join, guest_room
from .test_live import broadcasts, lv, room_live, start, vw  # noqa: F401 — фикстура lv
from .test_mail import LEADERS, _drain, add_profile, fake_smtp, messages, set_delivery  # noqa: F401
from .test_transcripts import _join
from .test_video_recording import call


def _meeting(client, mid):
    async def q(db):
        m = await db.get(Meeting, uuid.UUID(mid))
        await db.refresh(m)
        return m
    return call(client, q)


# ====================================================================================== гость: имя и почта
def test_guest_joins_with_a_name_only_or_with_email_and_only_organizers_see_it(client):
    room = guest_room(client, moderators=LEADERS)
    a = _join(client, "alice", room["id"])
    _join(client, "bob", room["id"])
    assert guest_join(client, room["guest_token"], "Пётр Петров").status_code == 200, "почта необязательна"
    bad = guest_join(client, room["guest_token"], "Анна Смирнова", email="не-адрес")
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "email_invalid"
    assert guest_join(client, room["guest_token"], "Анна Смирнова", email="anna@example.org", materials_consent=True).status_code == 200
    assert guest_join(client, room["guest_token"], "", email="x@example.org").status_code == 422, "имя обязательно"
    login(client, "carol")                                                   # руководитель комнаты
    guests = {p["display_name"]: p for p in client.get(f"/api/v1/meetings/{a['meeting_id']}").json()["participants"] if p["participant_type"] == "guest"}
    assert guests["Анна Смирнова (гость)"]["email"] == "anna@example.org" and guests["Анна Смирнова (гость)"]["materials_consent"] is True
    assert guests["Пётр Петров (гость)"]["email"] is None
    login(client, "bob")                                                     # обычный участник
    seen = client.get(f"/api/v1/meetings/{a['meeting_id']}").json()["participants"]
    assert all(p["email"] is None for p in seen), "почта гостя другим участникам не показывается"


# ====================================================================================== зритель и вопросы
def test_viewer_must_give_a_name_and_asks_questions_only_while_live(lv):  # noqa: F811
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    _join(lv, "bob", room["id"])
    lv.cookies.clear()
    login(lv, "carol")
    room_tok = lv.get(f"/api/v1/rooms/{room['id']}/live").json()["view_path"].rsplit("/", 1)[-1]
    lv.cookies.clear()
    assert lv.post(f"/api/v1/live/{room_tok}/viewer", json={"display_name": " "}).status_code == 422, "имя обязательно"
    assert lv.post(f"/api/v1/live/{room_tok}/viewer", json={"display_name": "Ольга", "email": "плохо"}).status_code == 422
    assert lv.post(f"/api/v1/live/{room_tok}/access", json={}).json()["detail"]["code"] in ("viewer_required", "not_live", "no_hls")
    v = lv.post(f"/api/v1/live/{room_tok}/viewer", json={"display_name": "Ольга Новикова", "email": "olga@example.org", "materials_consent": True}).json()
    ask = lambda text, who=v["viewer"]: lv.post(f"/api/v1/live/{room_tok}/questions", json={"viewer": who, "text": text})  # noqa: E731
    assert ask("Когда начнём?").json()["detail"]["code"] == "not_live", "до «Выйти в эфир» вопросы не принимаются"

    assert start(lv, mid).status_code == 200
    lv.cookies.clear()
    assert lv.post(f"/api/v1/live/{room_tok}/questions", json={"viewer": "0" * 32 + ".x", "text": "Привет"}).status_code == 403, "чужой или поддельный токен зрителя"
    q = ask("  Будет ли‮ запись\n\n\n\nэфира? <b>да</b> ")
    assert q.status_code == 201, q.text
    assert q.json()["text"] == "Будет ли запись\n\nэфира? <b>да</b>", "управляющие символы убраны, разметка — просто текст"
    assert ask("Ещё вопрос").json()["detail"]["code"] == "question_too_fast", "не чаще одного вопроса за несколько секунд"
    assert ask("x" * 501).status_code in (422, 429)
    other = lv.post(f"/api/v1/live/{room_tok}/viewer", json={"display_name": "Иван"}).json()["viewer"]
    assert ask("Вопрос Ивана", other).status_code == 201
    mine = lv.get(f"/api/v1/live/{room_tok}/questions", params={"viewer": v["viewer"]}).json()
    assert [x["text"][:8] for x in mine["items"]] == ["Будет ли"] and mine["open"] is True and "author" not in mine["items"][0], "зритель видит только свои вопросы"

    login(lv, "carol")
    feed = lv.get(f"/api/v1/meetings/{mid}/live/questions").json()
    assert feed["counts"]["new"] == 2 and feed["contacts"] is True
    olga = next(x for x in feed["items"] if x["author"] == "Ольга Новикова")
    assert olga["email"] == "olga@example.org" and olga["materials_consent"] is True, "руководитель видит почту автора как контакт"
    login(lv, "bob")
    feed_b = lv.get(f"/api/v1/meetings/{mid}/live/questions").json()
    assert feed_b["contacts"] is False and all("email" not in x for x in feed_b["items"]) and feed_b["can_moderate"] is True, "ведущий видит вопросы без почты"
    assert lv.patch(f"/api/v1/meetings/{mid}/live/questions/{olga['id']}", json={"status": "done"}).json()["status"] == "done"
    assert lv.patch(f"/api/v1/meetings/{mid}/live/questions/{olga['id']}", json={"status": "bogus"}).status_code == 422
    lv.cookies.clear()
    assert lv.get(f"/api/v1/live/{room_tok}/questions", params={"viewer": v["viewer"]}).json()["items"][0]["status"] == "done", "зритель видит, что вопрос обработан"


def test_questions_are_bound_to_the_broadcast_and_removed_with_it(lv):  # noqa: F811
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    (b,) = broadcasts(lv, mid)
    lv.cookies.clear()
    viewer = vw(lv, b.view_token)
    assert lv.post(f"/api/v1/live/{b.view_token}/questions", json={"viewer": viewer, "text": "Вопрос"}).status_code == 201

    async def rows(db):
        return list((await db.execute(select(LiveQuestion).where(LiveQuestion.broadcast_id == b.id))).scalars())
    assert len(call(lv, rows)) == 1
    login(lv, "root")
    lv.post(f"/api/v1/meetings/{mid}/end")
    assert lv.delete(f"/api/v1/meetings/{mid}").status_code in (200, 204)
    assert call(lv, rows) == [], "вопросы удаляются вместе со встречей и эфиром (сроки хранения встречи)"


# ====================================================================================== подготовка к эфиру
def test_broadcast_room_prepares_without_transcription_and_go_live_turns_it_on_and_off(lv):  # noqa: F811
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    assert _meeting(lv, mid).transcription_enabled is False, "до «Выйти в эфир» распознавание не идёт"
    start(lv, mid)
    assert _meeting(lv, mid).transcription_enabled is True, "эфир начался — распознавание включено по настройкам комнаты"
    start(lv, mid, enabled=False)
    assert _meeting(lv, mid).transcription_enabled is False, "эфир закончился — распознавание снова выключено"
    plain = make_room(lv, moderators=LEADERS)
    pm = _join(lv, "alice", plain["id"])["meeting_id"]
    assert _meeting(lv, pm).transcription_enabled is True, "обычная комната — как раньше"


# ====================================================================================== рассылка только по комнате
def test_auto_delivery_follows_only_the_room_and_reaches_consenting_guests(client, fake_smtp):  # noqa: F811
    add_profile(client)
    room = guest_room(client, moderators=LEADERS)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    guest_join(client, room["guest_token"], "Анна Смирнова", email="anna@example.org", materials_consent=True)
    guest_join(client, room["guest_token"], "Пётр Петров", email="petr@example.org")        # почта без согласия — только контакт

    async def prep(db):
        db.add(Protocol(meeting_id=uuid.UUID(mid), kind="protocol", status="ready", content="# Протокол\n\nТест.", created_by="test"))
        m = await db.get(Meeting, uuid.UUID(mid))
        # прежняя «своя» настройка встречи (до 0.64) — больше не действует: комната выключена, значит ничего не уходит
        m.delivery_override = {"enabled": True, "materials": ["protocol"], "recipients": {"participants": True}}
        await db.commit()
    call(client, prep)
    login(client, "carol")
    assert client.put(f"/api/v1/meetings/{mid}/settings", json={"delivery": {"enabled": True, "materials": ["protocol"]}}).status_code == 409, \
        "настройку рассылки для одной встречи больше не задать"
    login(client, "root")
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    _drain(client)
    assert messages(client) == [], "в комнате рассылка выключена — ничего не отправлено, даже если у встречи была своя настройка"

    room2 = guest_room(client, moderators=LEADERS)
    set_delivery(client, room2, {"per_material": {"protocol": {"mode": "all", "users": []}}})
    mid2 = _join(client, "alice", room2["id"])["meeting_id"]
    guest_join(client, room2["guest_token"], "Анна Смирнова", email="anna@example.org", materials_consent=True)
    guest_join(client, room2["guest_token"], "Пётр Петров", email="petr@example.org")
    call(client, lambda db: _add_protocol(db, mid2))
    login(client, "root")
    client.post(f"/api/v1/meetings/{mid2}/end")
    _drain(client)
    got = sorted(r["recipient"] for r in messages(client))
    assert got == ["alice@corp.test", "anna@example.org"], "гость с согласием получает материалы, без согласия — нет"


async def _add_protocol(db, mid):
    db.add(Protocol(meeting_id=uuid.UUID(mid), kind="protocol", status="ready", content="# Протокол\n\nТест.", created_by="test"))
    await db.commit()


def test_legacy_room_settings_are_shown_and_sent_the_same_way():
    from app.services.mail_delivery import clean_spec, view_spec

    legacy = clean_spec({"enabled": True, "materials": ["protocol"], "recipients": {"organizer": True}})
    assert view_spec(legacy)["per_material"]["protocol"]["mode"] == "organizer", "прежняя настройка «организатору» видна в редакторе, а не как «не отправлять»"
    from app.services.mail_delivery import recipients_spec

    with_mails = view_spec(clean_spec({"enabled": True, "materials": ["protocol"], "recipients": {"participants": True, "emails": ["x@example.org"]}}))
    assert recipients_spec(with_mails, "protocol")["recipients"]["emails"] == ["x@example.org"], "«Свои адреса» видны в редакторе — и получают материал"
    assert recipients_spec(with_mails, "summary")["recipients"]["emails"] == [], "выключенный материал на свои адреса не уходит"
    only = view_spec(clean_spec({"enabled": True, "materials": ["protocol"], "recipients": {"emails": ["x@example.org"]}}))
    assert only["per_material"]["protocol"]["mode"] == "emails", "прежняя настройка «только свои адреса» видна в редакторе как есть"


# ====================================================================================== вложения чатов
def test_chat_attachments_are_capped_at_3_mb_and_only_images_and_documents():
    old = ChatFilesSettings.model_validate({"max_size_mb": 25, "allowed_extensions": "png,zip,mp4,pdf"})
    assert old.max_size_mb == 3 and old.extensions() == {"png", "pdf"}, "прежние настройки (25 МБ, архивы, видео) приводятся к правилам чатов"
    assert "zip" not in ChatFilesSettings().extensions() and {"png", "jpg", "pdf", "docx"} <= ChatFilesSettings().extensions()
    from app.services.chat_files import AttachmentError, inspect

    for name, data in (("a.zip", b"PK\x03\x04" + b"0" * 10), ("v.mp4", b"\x00\x00\x00 ftypisom"), ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024))):
        try:
            inspect(name, data, ChatFilesSettings())
            raise AssertionError(name)
        except AttachmentError as e:
            assert str(e) == REJECT_TEXT
    assert inspect("shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, ChatFilesSettings())[2] == "image", "снимок экрана проходит"
