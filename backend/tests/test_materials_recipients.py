"""Ручная отправка материалов: получатели не зависят от правила автоматической рассылки и от обезличивания; участники без почты видны; гости — по их согласию."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import GuestParticipant, Meeting, Room, User

from .conftest import login, make_room
from .test_mail import LEADERS
from .test_transcripts import _join


def _ended(client, room, *users):
    mid = None
    for u in users:
        mid = _join(client, u, room["id"])["meeting_id"]
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code in (200, 204)
    return mid


def _set(client, room_id, **fields):
    async def go():
        async with client.app_obj.state.session_maker() as db:
            r = await db.get(Room, uuid.UUID(room_id))
            for k, v in fields.items():
                setattr(r, k, v)
            await db.commit()
    client.portal.call(go)


def _guest(client, mid, name, email=None, consent=False):
    async def go():
        async with client.app_obj.state.session_maker() as db:
            m = await db.get(Meeting, uuid.UUID(mid))
            db.add(GuestParticipant(meeting_id=m.id, room_id=m.room_id, display_name=name, email=email, materials_consent=consent))
            await db.commit()
    client.portal.call(go)


def _mail(client, login_name, email):
    async def go():
        async with client.app_obj.state.session_maker() as db:
            u = (await db.execute(select(User).where(User.sam_account_name == login_name))).scalars().first()
            u.email = email
            await db.commit()
    client.portal.call(go)


def preview(client, mid):
    login(client, "carol")
    r = client.get(f"/api/v1/meetings/{mid}/delivery")
    assert r.status_code == 200, r.text
    return r.json()


RULE_OFF = {"enabled": False, "materials": ["protocol"], "recipients": {"leaders": False, "participants": False, "users": [], "emails": []}}


def test_room_rule_without_recipients_does_not_empty_the_manual_window(client):
    """Регрессия: окно брало получателей из правила автоматической рассылки, и при сохранённом правиле без получателей показывало «Кому (0)»."""
    room = make_room(client, moderators=LEADERS)
    mid = _ended(client, room, "alice", "bob")
    _set(client, room["id"], mail_delivery=RULE_OFF)
    plan = preview(client, mid)
    assert len([r for r in plan["recipients"] if r["source"] in ("participant", "leader")]) >= 2, plan["recipients"]
    assert plan["preselected"] is None, "правило никого не называет: заранее отмечаются все, а не никто"


def test_manual_list_is_wider_than_the_rule_and_the_rule_is_preselected(client):
    room = make_room(client, moderators=LEADERS)
    mid = _ended(client, room, "alice", "bob")
    _mail(client, "alice", "alice@example.org")
    _set(client, room["id"], mail_delivery={"enabled": True, "materials": ["protocol"], "recipients": {"leaders": False, "participants": False, "users": [], "emails": ["partner@example.org"]}})
    plan = preview(client, mid)
    emails = {r["email"] for r in plan["recipients"]}
    assert "alice@example.org" in emails and "partner@example.org" in emails
    assert plan["preselected"] == ["partner@example.org"]


def test_anonymization_does_not_touch_recipients(client):
    room = make_room(client, moderators=LEADERS)
    mid = _ended(client, room, "alice", "bob")
    plain = preview(client, mid)
    _set(client, room["id"], anonymize_mode="on")
    assert preview(client, mid)["recipients"] == plain["recipients"]


def test_participant_without_address_is_listed_with_a_reason_and_manual_address_keeps_the_name(client, monkeypatch):
    room = make_room(client, moderators=LEADERS)
    mid = _ended(client, room, "alice")
    _mail(client, "alice", None)
    plan = preview(client, mid)
    row = next(r for r in plan["recipients"] if r["source"] == "participant")
    assert row["problem"] == "no_email" and row["email"] == "" and row["name"]

    seen = {}

    async def enqueue(db, meeting, kinds, recipients, **kw):
        seen["r"] = recipients
        return {"queued": len(recipients), "skipped": []}

    async def active(db):
        return object()
    state = client.app_obj.state
    monkeypatch.setattr(state.delivery, "enqueue", enqueue)
    monkeypatch.setattr(state.mail, "active", active)

    async def fake_load(svc, db, meeting):
        return object()
    from app.services import mail_delivery as md
    monkeypatch.setitem(md.MATERIALS, "protocol", md.MaterialDef(md.MATERIALS["protocol"].label, md.MATERIALS["protocol"].describe, fake_load))
    r = client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["added@example.org"], "names": {"added@example.org": row["name"]}, "subject": "Т", "body": "Т"})
    assert r.status_code == 200, r.text
    assert seen["r"][0].email == "added@example.org" and seen["r"][0].name == row["name"]


def test_guests_are_shown_but_only_those_who_agreed_get_an_address(client):
    room = make_room(client, moderators=LEADERS)
    mid = _ended(client, room, "alice")
    _guest(client, mid, "Гость Согласен", "ok@example.org", True)
    _guest(client, mid, "Гость Против", "no@example.org", False)
    _guest(client, mid, "Гость Без почты")
    rows = {r["name"]: r for r in preview(client, mid)["recipients"]}
    assert rows["Гость Согласен (гость)"]["email"] == "ok@example.org" and rows["Гость Согласен (гость)"]["problem"] is None
    assert rows["Гость Против (гость)"]["problem"] == "no_consent" and rows["Гость Против (гость)"]["email"] == "", "адрес без согласия не показывается"
    assert rows["Гость Без почты (гость)"]["problem"] == "no_email"


def test_automatic_rule_stays_strict_about_guests(client):
    room = make_room(client, moderators=LEADERS)
    mid = _ended(client, room, "alice")
    _guest(client, mid, "Гость Согласен", "ok@example.org", True)
    _guest(client, mid, "Гость Без почты")

    async def go():
        st = client.app_obj.state
        async with st.session_maker() as db:
            m = await db.get(Meeting, uuid.UUID(mid))
            return await st.delivery.resolve(db, m.room, m, {"recipients": {"leaders": False, "participants": True, "users": [], "emails": []}})
    names = {r.name for r in client.portal.call(go)}
    assert "Гость Согласен (гость)" in names and "Гость Без почты (гость)" not in names, "автоматическая рассылка не обрастает строками «нет адреса»"
