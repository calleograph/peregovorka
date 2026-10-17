"""Предел «Максимум участников» — от 1 до 2000 везде одинаково: создание и правка в админке, настройки комнаты у руководителя.
Это граница настройки, а не гарантия 2000 одновременных видеоподключений."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import Room

from .conftest import OTHER_GROUP, login, make_room

LEADERS = [{"subject_type": "group", "subject_ref": OTHER_GROUP, "display_name": "Руководители"}]
OK = [1, 200, 1999, 2000]
BAD = [0, 2001, 5000]


def _stored(client, room_id: str) -> int:
    async def _read():
        async with client.app_obj.state.session_maker() as db:
            return (await db.execute(select(Room.max_participants).where(Room.id == uuid.UUID(room_id)))).scalar_one()

    return client.portal.call(_read)


@pytest.mark.parametrize("n", OK)
def test_admin_create_and_edit_accept_the_range_and_store_it(client, n):
    room = make_room(client, max_participants=n)
    assert room["max_participants"] == n and _stored(client, room["id"]) == n
    other = make_room(client)
    r = client.patch(f"/api/v1/admin/rooms/{other['id']}", json={"max_participants": n})
    assert r.status_code == 200 and r.json()["max_participants"] == n
    assert client.get(f"/api/v1/admin/rooms/{other['id']}").json()["max_participants"] == n   # после повторного открытия настроек
    assert _stored(client, other["id"]) == n


@pytest.mark.parametrize("n", BAD)
def test_admin_create_and_edit_reject_out_of_range(client, n):
    login(client, "root")
    assert client.post("/api/v1/admin/rooms", json={"slug": f"r-{uuid.uuid4().hex[:8]}", "name": "x", "max_participants": n}).status_code == 422
    room = make_room(client, max_participants=20)
    r = client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"max_participants": n})
    assert r.status_code == 422 and str(2000 if n > 1 else 1) in r.text    # в ответе названа нарушенная граница
    assert _stored(client, room["id"]) == 20    # прежнее значение не тронуто


@pytest.mark.parametrize("n", OK)
def test_leader_settings_accept_the_range(client, n):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    r = client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"max_participants": n})
    assert r.status_code == 200 and r.json()["max_participants"] == n
    assert _stored(client, room["id"]) == n


@pytest.mark.parametrize("n", BAD)
def test_leader_settings_reject_out_of_range(client, n):
    room = make_room(client, moderators=LEADERS, max_participants=20)
    login(client, "carol")
    assert client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"max_participants": n}).status_code == 422
    assert _stored(client, room["id"]) == 20
