from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog

from .conftest import OTHER_GROUP, STAFF_GROUP, login, make_room


def test_regular_user_cannot_use_admin_api(client):
    room = make_room(client)
    login(client, "alice")
    assert client.get("/api/v1/admin/rooms").status_code == 403
    assert client.post("/api/v1/admin/rooms", json={"slug": "xx-room", "name": "x"}).status_code == 403
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"name": "hack"}).status_code == 403
    assert client.delete(f"/api/v1/admin/rooms/{room['id']}").status_code == 403
    assert client.get("/api/v1/admin/audit").status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/admin/rooms").status_code == 401


def test_admin_crud_and_audit_trail_without_secrets(client):
    room = make_room(client, password="top-secret-pw")
    app = client.app_obj

    r = client.patch(f"/api/v1/admin/rooms/{room['id']}", json={
        "name": "Новое имя", "max_participants": 5, "transcription_enabled": False, "record_audio": True,
        "text_retention_days": 30, "audio_retention_days": 7,
        "acl": [{"subject_type": "group", "subject_ref": OTHER_GROUP.upper()}]})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Новое имя" and body["max_participants"] == 5 and body["record_audio"] is True
    assert body["acl"][0]["subject_ref"] == OTHER_GROUP  # нормализуется в нижний регистр
    assert body["has_password"] is True

    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"password": ""}).json()["has_password"] is False
    assert client.delete(f"/api/v1/admin/rooms/{room['id']}").status_code == 204
    assert client.get(f"/api/v1/admin/rooms/{room['id']}").status_code == 404

    async def _audit():
        async with app.state.session_maker() as db:
            return (await db.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()

    rows = client.portal.call(_audit)
    assert [r.action for r in rows] == ["room.create", "room.update", "room.update", "room.delete"]
    assert all(r.actor_name == "Root Admin" for r in rows)
    dump = " ".join(str(r.details) for r in rows)
    assert "top-secret-pw" not in dump, "пароль комнаты не должен попадать в аудит"


def test_duplicate_slug_conflict_and_validation(client):
    room = make_room(client)
    login(client, "root")
    dup = client.post("/api/v1/admin/rooms", json={"slug": room["slug"], "name": "dup"})
    assert dup.status_code == 409
    assert client.post("/api/v1/admin/rooms", json={"slug": "Bad Slug!", "name": "x"}).status_code == 422
    assert client.post("/api/v1/admin/rooms", json={"slug": "ok-slug", "name": "x", "max_participants": 0}).status_code == 422


def test_cannot_delete_room_with_active_meeting(client):
    room = make_room(client)
    login(client, "alice")
    assert client.post(f"/api/v1/rooms/{room['id']}/join", json={}).status_code == 200
    login(client, "root")
    assert client.delete(f"/api/v1/admin/rooms/{room['id']}").status_code == 409
    assert STAFF_GROUP
