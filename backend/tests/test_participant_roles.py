"""Роли участников в карточке встречи: организатор (начал встречу) и руководитель комнаты (поимённо). Нужны истории: первыми показываются они."""
from __future__ import annotations

from app.models import RoomModerator

from .conftest import login, make_room
from .test_transcripts import _join


def test_organizer_is_marked_and_other_participants_have_no_role(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    _join(client, "bob", room["id"])
    login(client, "root")
    parts = client.get(f"/api/v1/meetings/{a['meeting_id']}").json()["participants"]
    roles = {p["display_name"]: p["role"] for p in parts}
    assert roles == {"Alice A": "organizer", "Bob B": None}, roles


def test_named_room_leader_is_marked_leader(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    bob = _join(client, "bob", room["id"])
    login(client, "root")

    async def add_leader():
        from sqlalchemy import select
        from app.models import User
        async with client.app_obj.state.session_maker() as db:
            bob_user = (await db.execute(select(User).where(User.sam_account_name == "bob"))).scalars().one()
            db.add(RoomModerator(room_id=__import__("uuid").UUID(room["id"]), subject_type="user", subject_ref=bob_user.ad_guid.lower(), display_name="Bob B"))
            await db.commit()

    client.portal.call(add_leader)
    parts = client.get(f"/api/v1/meetings/{a['meeting_id']}").json()["participants"]
    roles = {p["display_name"]: p["role"] for p in parts}
    assert roles == {"Alice A": "organizer", "Bob B": "leader"}, roles
    assert bob["meeting_id"] == a["meeting_id"]
