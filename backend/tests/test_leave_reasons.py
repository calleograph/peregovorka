"""Причина каждого отключения — в «Журнале событий»; время автозавершения забытой встречи — настройка комнаты (по умолчанию 30 минут)."""
from __future__ import annotations

import time

from sqlalchemy import select

from app.models import EventLog
from app.services import leave_reasons

from .conftest import login, make_room
from .test_transcripts import _join, _signed_webhook


def _left(client, settings, a: dict, reason: int, name: str = "Алиса Тестовая") -> dict:
    body, token = _signed_webhook(settings, {
        "event": "participant_left", "room": {"name": a["livekit_room"]},
        "participant": {"identity": a["identity"], "name": name, "disconnect_reason": reason, "joined_at": int(time.time()) - 125}})
    assert client.post("/internal/v1/livekit/webhook", content=body, headers={"Authorization": token}).status_code == 200
    app = client.app_obj
    client.portal.call(app.state.journal.flush)

    async def _rows():
        async with app.state.session_maker() as db:
            return (await db.execute(select(EventLog).where(EventLog.event == "participant_left").order_by(EventLog.at.desc()))).scalars().all()

    row = client.portal.call(_rows)[0]
    return {"message": row.message, "data": row.data, "level": row.level, "room": row.room}


def test_each_disconnect_is_journaled_with_its_reason(client, settings, monkeypatch):
    monkeypatch.setattr("app.api.internal.LEFT_GRACE_S", 0)          # в жизни «вышел сам» ждёт /leave 3 с в фоне; здесь /leave пришёл раньше
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    lost = _left(client, settings, a, 9)                                    # SIGNAL_CLOSE: связь пропала
    assert lost["data"]["reason"] == "connection_lost" and lost["data"]["lk"] == "SIGNAL_CLOSE" and lost["level"] == "warn"
    assert lost["message"].startswith("Алиса Тестовая: потеря соединения") and "во встрече 2 мин" in lost["message"] and lost["room"] == room["name"]
    assert _left(client, settings, a, 1)["data"]["reason"] == "closed", "клиент закрыл подключение без кнопки «Выйти» — закрыл страницу или приложение"

    a = _join(client, "alice", room["id"])
    assert client.post(f"/api/v1/meetings/{a['meeting_id']}/leave").status_code == 204
    left = _left(client, settings, a, 1)
    assert left["data"]["reason"] == "left" and "вышел сам" in left["message"], "кнопка «Выйти» — метка сервера важнее причины LiveKit"
    assert _left(client, settings, a, 1)["data"]["reason"] == "closed", "метка действует один раз"


def test_resolve_prefers_server_action_then_meeting_end_then_livekit():
    class R:
        def __init__(self): self.d = {}
        async def set(self, k, v, ex=None): self.d[k] = v
        async def get(self, k): return self.d.get(k)
        async def getdel(self, k): return self.d.pop(k, None)

    import asyncio

    async def run():
        r = R()
        await leave_reasons.mark(r, "m", "u-1", "removed")
        assert (await leave_reasons.resolve(r, "m", "u-1", 4, None))[0] == "removed"
        await leave_reasons.mark_idle(r, "m", 1830)
        code, data = await leave_reasons.resolve(r, "m", "u-1", 5, "idle")
        assert code == "idle" and data["quiet_s"] == 1830
        assert "без действий 30 мин" in leave_reasons.message("Боб", code, data, 40)
        code, data = await leave_reasons.resolve(r, "m", "u-2", 5, "manual")
        assert code == "meeting_ended" and "«Завершить для всех»" in leave_reasons.message("Боб", code, data, None)
        assert (await leave_reasons.resolve(r, "m", "u-2", 14, None))[0] == "connection_lost"
        assert (await leave_reasons.resolve(r, "m", "u-2", 99, None))[0] == "other"

    asyncio.run(run())


def test_idle_minutes_is_a_room_setting_and_the_server_switch_still_wins(client):
    room = make_room(client)
    assert room["idle_end_minutes"] == 30
    assert _join(client, "alice", room["id"])["client"]["idle_minutes"] == 30
    login(client, "root")
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"idle_end_minutes": 45}).json()["idle_end_minutes"] == 45
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"idle_end_minutes": 721}).status_code == 422
    assert _join(client, "alice", room["id"])["client"]["idle_minutes"] == 45
    login(client, "root")
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"idle_end_minutes": 0}).status_code == 200
    j = _join(client, "alice", room["id"])["client"]
    assert j["idle_minutes"] == 0 and j["idle_end"] == "off", "0 — не завершать (и прежнее поле для старых страниц)"
