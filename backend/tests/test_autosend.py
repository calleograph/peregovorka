"""Автоотправка материалов встречи (настройки комнаты): независимые переключатели, получатели (руководители / владелец / организатор / участники / выбранные),
права на изменение, аудит, отправка только полных готовых документов, отсутствие дублей. SMTP подменён (см. test_mail)."""
from __future__ import annotations

import uuid

from app.models import Protocol

from .conftest import login, make_room
from .test_mail import LEADERS, add_profile, fake_smtp, finished_meeting, messages, run_queue, set_delivery  # noqa: F401

NONE = {"mode": "none", "users": []}


def pm(**modes) -> dict:
    return {"per_material": {k: ({"mode": modes[k], "users": []} if k in modes else NONE) for k in ("protocol", "summary", "transcript", "map")}}


def test_each_material_is_switched_independently_and_defaults_are_off(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    got = client.get(f"/api/v1/rooms/{room['id']}/manage").json()["mail_delivery"]
    assert got["enabled"] is False and got["materials"] == [], "по умолчанию автоотправка выключена"
    for kinds in (["protocol"], ["summary"], ["transcript"], ["map"], ["protocol", "map"]):
        saved = set_delivery(client, room, pm(**{k: "leader" for k in kinds}))
        assert saved["materials"] == kinds and saved["enabled"] is True, "включение одного материала не включает остальные"
        assert {k for k, v in saved["per_material"].items() if v["mode"] != "none"} == set(kinds)
    saved = set_delivery(client, room, pm())
    assert saved["materials"] == [] and saved["enabled"] is False


def test_new_recipient_modes_are_accepted_and_unknown_ones_become_off(client):
    room = make_room(client, moderators=LEADERS)
    saved = set_delivery(client, room, pm(protocol="owner", summary="organizer", transcript="all", map="bogus"))
    assert [saved["per_material"][k]["mode"] for k in ("protocol", "summary", "transcript", "map")] == ["owner", "organizer", "all", "none"]


def test_owner_and_admin_may_change_but_participant_and_stranger_may_not(client):
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, pm(protocol="leader"), user="carol")                       # руководитель комнаты
    set_delivery(client, room, pm(summary="all"), user="root")                            # главный администратор — любая комната
    for who in ("alice", "bob"):
        login(client, who)
        r = client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": pm(transcript="all")})
        assert r.status_code in (403, 404), "обычный участник не меняет настройки (проверка на сервере)"
    login(client, "root")
    assert client.get(f"/api/v1/rooms/{room['id']}/manage").json()["mail_delivery"]["materials"] == ["summary"], "чужая попытка ничего не изменила"


def test_changes_are_audited_with_who_and_values_but_without_addresses(client):
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {**pm(protocol="selected", summary="all"), "per_material": {**pm(summary="all")["per_material"],
                 "protocol": {"mode": "selected", "users": [{"ref": "x-1", "name": "Иван Иванов", "email": "ivan@example.org"}]}}}, user="carol")
    login(client, "root")
    rows = [a for a in client.get("/api/v1/admin/audit").json() if a["action"] == "room.manage.update"]
    assert rows and "Carol C" in str(rows[0]), "в аудите видно, кто изменил"
    text = str(rows[0])
    assert "per_material" in text and "'summary': {'mode': 'all'" in text.replace('"', "'")
    assert "ivan@example.org" not in text and "Иван Иванов" not in text, "адреса и имена в аудит не пишутся"


def test_only_enabled_materials_are_sent_to_the_chosen_recipients_after_the_meeting(client, fake_smtp):  # noqa: F811
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, pm(protocol="all", summary="organizer"))
    finished_meeting(client, room)
    got: dict[str, set[str]] = {}
    for r in messages(client):
        got.setdefault(r["recipient"], set()).update(r["kinds"])
    assert got == {"alice@corp.test": {"protocol", "summary"}, "bob@corp.test": {"protocol"}}, "участнику — только протокол, организатору (начавшему встречу) ещё и резюме; стенограммы и карты нет"
    assert run_queue(client) == 3
    assert all(r["state"] == "sent" for r in messages(client))


def test_owner_mode_sends_only_to_the_room_owner(client, directory, fake_smtp):  # noqa: F811
    add_profile(client)
    carol = directory.users["carol"][1]
    room = make_room(client, moderators=[{"subject_type": "user", "subject_ref": carol.ad_guid, "display_name": "Carol C"}])
    set_delivery(client, room, pm(summary="owner"))
    finished_meeting(client, room)
    rows = messages(client)
    assert [(r["recipient"], r["kinds"]) for r in rows] == [("carol@corp.test", ["summary"])]


def test_incomplete_or_missing_documents_are_not_sent_automatically_and_nothing_is_duplicated(client, fake_smtp):  # noqa: F811
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {**pm(protocol="all", summary="all"), "if_missing": "send"})
    mid = finished_meeting(client, room, users=("alice", "bob"))
    # finished_meeting создаёт готовые документы ДО завершения; сам тест подменяет их: протокол оборван, резюме готово
    import asyncio

    async def mark():
        async with client.app_obj.state.session_maker() as db:
            from sqlalchemy import select
            p = (await db.execute(select(Protocol).where(Protocol.meeting_id == uuid.UUID(mid), Protocol.kind == "protocol"))).scalars().first()
            p.meta = {"truncated": True}
            await db.commit()
    client.portal.call(mark)
    login(client, "root")
    from sqlalchemy import delete
    from app.models import MailMessage

    async def reset():
        async with client.app_obj.state.session_maker() as db:
            await db.execute(delete(MailMessage).where(MailMessage.meeting_id == uuid.UUID(mid)))
            await db.commit()
        await client.app_obj.state.redis.delete(f"mail:auto:{mid}")
    client.portal.call(reset)
    client.portal.call(lambda: client.app_obj.state.delivery.run_auto(uuid.UUID(mid)))
    kinds = {tuple(r["kinds"]) for r in messages(client)}
    assert kinds == {("summary",)}, "оборванный протокол автоматически не уходит как готовый, полное резюме уходит"
    n = len(messages(client))
    client.portal.call(lambda: client.app_obj.state.delivery.run_auto(uuid.UUID(mid)))        # повторное выполнение фонового задания
    assert len(messages(client)) == n, "повторный запуск рассылку не дублирует"
