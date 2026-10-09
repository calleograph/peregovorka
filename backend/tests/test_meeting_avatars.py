"""Аватарки участников для плиток комнаты: только у тех, у кого они есть; адрес версионный; чужим встреча не раскрывается."""
from __future__ import annotations

from sqlalchemy import select

from app.models import User, utcnow

from .conftest import login, make_room
from .test_transcripts import _join


def test_avatars_listed_only_for_participants_who_have_one(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    _join(client, "bob", room["id"])

    async def give_avatar():
        async with client.app_obj.state.session_maker() as db:
            u = (await db.execute(select(User).where(User.sam_account_name == "alice"))).scalars().one()
            u.avatar_mime, u.avatar_updated_at = "image/webp", utcnow()
            await db.commit()
            return u.livekit_identity, str(u.id)

    ident, uid = client.portal.call(give_avatar)
    login(client, "alice")
    got = client.get(f"/api/v1/meetings/{a['meeting_id']}/avatars")
    assert got.status_code == 200, got.text
    data = got.json()
    assert list(data) == [ident], "у Боба аватарки нет — в списке только Алиса"
    assert data[ident].startswith(f"/api/v1/users/{uid}/avatar?v="), "адрес версионный"


def test_stranger_cannot_list_avatars_of_someone_elses_meeting(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    login(client, "carol")
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/avatars").status_code == 404
