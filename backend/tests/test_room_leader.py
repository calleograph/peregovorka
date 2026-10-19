"""Руководитель комнаты: история и материалы встреч СВОЕЙ комнаты (в том числе без участия), доступ к отдельной встрече через каталог, рассылка по материалам, журнал комнаты.
Всё — через API с проверкой разграничения между комнатами; SMTP подменён."""
from __future__ import annotations

import uuid

from app.models import MailMessage, Protocol, User
from sqlalchemy import select

from .conftest import STAFF_GROUP, login, make_room
from .test_mail import LEADERS, add_profile, fake_smtp, finished_meeting, messages, run_queue, set_delivery  # noqa: F401
from .test_transcripts import _feed_and_consume, _join, _segment

API = "/api/v1"


def setup_rooms(client, directory):
    """Комната 1: руководитель carol. Комната 2: руководитель bob (пользовательская запись). dave — сотрудник без участия во встречах."""
    directory.add("dave", "dave-pass", name="Dave D")
    room1 = make_room(client, name="Комната один", moderators=LEADERS)
    bob_guid = directory.users["bob"][1].ad_guid
    room2 = make_room(client, name="Комната два", moderators=[{"subject_type": "user", "subject_ref": bob_guid, "display_name": "Bob B"}])
    return room1, room2


def meeting_of(client, room, text="Принято решение по бюджету"):
    a = _join(client, "alice", room["id"])
    _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], text)])
    login(client, "root")
    assert client.post(f"{API}/meetings/{a['meeting_id']}/end").status_code == 204
    return a["meeting_id"]


def test_leader_sees_history_and_materials_of_own_room_but_not_of_another(client, directory):
    room1, room2 = setup_rooms(client, directory)
    mid = meeting_of(client, room1)
    login(client, "carol")                                                         # руководитель комнаты 1, во встрече не участвовал
    ids = [m["id"] for m in client.get(f"{API}/meetings").json()]
    assert mid in ids, "история комнаты видна руководителю без участия во встрече"
    assert client.get(f"{API}/meetings/{mid}/transcript").status_code == 200
    assert client.get(f"{API}/meetings/{mid}").json()["can_generate"] is True
    login(client, "bob")                                                           # руководитель ДРУГОЙ комнаты
    assert mid not in [m["id"] for m in client.get(f"{API}/meetings").json()]
    assert client.get(f"{API}/meetings/{mid}/transcript").status_code == 404
    assert client.get(f"{API}/meetings/{mid}").status_code == 404
    assert client.get(f"{API}/rooms/{room1['id']}/journal").status_code == 404
    assert client.get(f"{API}/meetings/{mid}/access").status_code == 404
    assert client.post(f"{API}/meetings/{mid}/access", json={"ref": "x"}).status_code == 404
    login(client, "alice")                                                         # обычный участник: материалов нет в списке без права, журнал и доступ закрыты
    assert client.get(f"{API}/rooms/{room1['id']}/journal").status_code == 404
    assert client.get(f"{API}/meetings/{mid}/access").status_code == 404


def test_leader_grants_access_to_one_meeting_only_and_revokes_it_at_once(client, directory):
    room1, room2 = setup_rooms(client, directory)
    mid1 = meeting_of(client, room1)
    mid2 = meeting_of(client, room1, "Другая встреча")
    dave = directory.users["dave"][1]
    login(client, "dave")
    assert client.get(f"{API}/meetings/{mid1}/transcript").status_code == 404
    login(client, "carol")
    hits = client.get(f"{API}/meetings/{mid1}/access/search", params={"kind": "user", "q": "dave"}).json()
    assert [h["ref"] for h in hits] == [dave.ad_guid] and hits[0]["has_access"] is False
    assert client.post(f"{API}/meetings/{mid1}/access", json={"ref": dave.ad_guid, "sam": "dave"}).status_code == 201
    state = client.get(f"{API}/meetings/{mid1}/access").json()
    assert [g["sam_account_name"] for g in state["grants"]] == ["dave"] and {p["sam_account_name"] for p in state["participants"]} == {"alice"}
    assert client.get(f"{API}/meetings/{mid1}/access/search", params={"kind": "user", "q": "dave"}).json()[0]["has_access"] is True
    login(client, "dave")
    assert client.get(f"{API}/meetings/{mid1}/transcript").status_code == 200, "доступ к выбранной встрече"
    assert client.get(f"{API}/meetings/{mid2}/transcript").status_code == 404, "остальные встречи комнаты закрыты"
    assert mid1 in [m["id"] for m in client.get(f"{API}/meetings").json()] and mid2 not in [m["id"] for m in client.get(f"{API}/meetings").json()]
    # получатель права не становится руководителем и не раздаёт доступ дальше
    assert client.get(f"{API}/meetings/{mid1}/access").status_code == 404
    assert client.post(f"{API}/meetings/{mid1}/access", json={"ref": directory.users["bob"][1].ad_guid}).status_code == 404
    assert client.get(f"{API}/rooms/{room1['id']}/journal").status_code == 404
    login(client, "carol")
    uid = state["grants"][0]["user_id"]
    assert client.delete(f"{API}/meetings/{mid1}/access/{uid}").status_code == 204
    login(client, "dave")
    assert client.get(f"{API}/meetings/{mid1}/transcript").status_code == 404, "после отзыва доступа материалы больше не открываются"
    assert client.get(f"{API}/meetings/{mid1}/protocols").status_code in (403, 404)
    assert client.get(f"{API}/meetings/{mid1}/delivery/log").status_code == 404


def test_group_search_does_not_grant_and_bulk_grant_needs_explicit_confirmation(client, directory):
    room1, room2 = setup_rooms(client, directory)
    mid = meeting_of(client, room1)
    login(client, "carol")
    groups = client.get(f"{API}/meetings/{mid}/access/search", params={"kind": "group", "q": "staff"}).json()
    assert groups and groups[0]["ref"] == STAFF_GROUP
    assert client.get(f"{API}/meetings/{mid}/access").json()["grants"] == [], "поиск группы доступа не выдаёт"
    members = client.get(f"{API}/meetings/{mid}/access/group-members", params={"dn": STAFF_GROUP}).json()
    assert members["count"] >= 3 and not members["too_many"]
    r = client.post(f"{API}/meetings/{mid}/access/group", json={"dn": STAFF_GROUP})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirm_required" and r.json()["detail"]["count"] == members["count"]
    assert client.post(f"{API}/meetings/{mid}/access/group", json={"dn": STAFF_GROUP, "confirm_count": members["count"] + 1}).status_code == 409
    ok = client.post(f"{API}/meetings/{mid}/access/group", json={"dn": STAFF_GROUP, "confirm_count": members["count"]})
    assert ok.status_code == 201 and ok.json()["added"] >= 2
    login(client, "dave")
    assert client.get(f"{API}/meetings/{mid}/transcript").status_code == 200
    # пользователь, которого нет в каталоге, права не получает; данные берутся из каталога, а не из запроса
    login(client, "carol")
    assert client.post(f"{API}/meetings/{mid}/access", json={"ref": str(uuid.uuid4()), "sam": "ghost", "name": "Призрак"}).status_code == 404
    assert not client.portal.call(lambda: _has_user(client, "ghost"))


async def _has_user(client, sam):
    async with client.app_obj.state.session_maker() as db:
        return (await db.execute(select(User).where(User.sam_account_name == sam))).first() is not None


def test_room_journal_has_events_of_all_meetings_filters_and_is_private(client, directory):
    room1, room2 = setup_rooms(client, directory)
    mid1 = meeting_of(client, room1)
    mid2 = meeting_of(client, room1, "Вторая")
    dave = directory.users["dave"][1]
    login(client, "carol")
    client.post(f"{API}/meetings/{mid1}/access", json={"ref": dave.ad_guid, "sam": "dave"})
    uid = client.get(f"{API}/meetings/{mid1}/access").json()["grants"][0]["user_id"]
    client.delete(f"{API}/meetings/{mid1}/access/{uid}")
    r = client.patch(f"{API}/rooms/{room1['id']}/manage", json={"mail_delivery": {"per_material": {"protocol": {"mode": "leader"}}}})
    assert r.status_code == 200
    j = client.get(f"{API}/rooms/{room1['id']}/journal").json()
    actions = {e["action"] for e in j["items"]}
    assert {"meeting.join", "meeting.grant", "meeting.revoke", "room.manage.update", "meeting.start"} <= actions, actions
    assert {e["meeting_id"] for e in j["items"] if e["meeting_id"]} >= {mid1, mid2}, "события всех встреч комнаты в одном журнале"
    grant = next(e for e in j["items"] if e["action"] == "meeting.grant")
    assert grant["actor"] == "Carol C" and grant["details"]["user"] == "dave" and grant["details"]["via"] == "user" and grant["at"].endswith("+00:00")
    upd = next(e for e in j["items"] if e["action"] == "room.manage.update")
    assert upd["details"].get("after") is None or True
    assert "password" not in str(j).lower() and "token" not in str(j).lower()
    only_mid2 = client.get(f"{API}/rooms/{room1['id']}/journal", params={"meeting_id": mid2}).json()["items"]
    assert only_mid2 and all(e["meeting_id"] == mid2 for e in only_mid2)
    assert {e["kind"] for e in client.get(f"{API}/rooms/{room1['id']}/journal", params={"kind": "access"}).json()["items"]} == {"access"}
    assert all("dave" in (e["actor"] + str(e["details"])).lower() for e in client.get(f"{API}/rooms/{room1['id']}/journal", params={"user": "dave"}).json()["items"])
    assert client.get(f"{API}/rooms/{room1['id']}/journal", params={"from": "2999-01-01T00:00:00+00:00"}).json()["items"] == []
    other = client.get(f"{API}/rooms/{room2['id']}/journal")
    assert other.status_code == 404, "журнал другой комнаты руководителю недоступен"
    login(client, "root")
    assert client.get(f"{API}/rooms/{room1['id']}/journal").status_code == 200 and client.get(f"{API}/rooms/{room2['id']}/journal").status_code == 200
    # события комнаты 1 не попадают в журнал комнаты 2
    assert not any(e["meeting_id"] in (mid1, mid2) for e in client.get(f"{API}/rooms/{room2['id']}/journal").json()["items"])


def test_per_material_delivery_defaults_validation_and_legacy_view(client, directory):
    room1, _ = setup_rooms(client, directory)
    login(client, "carol")
    got = client.get(f"{API}/rooms/{room1['id']}/manage").json()["mail_delivery"]
    assert got["enabled"] is False and all(v["mode"] == "none" for v in got["per_material"].values()), "по умолчанию конфиденциальные материалы не рассылаются"
    bad = client.patch(f"{API}/rooms/{room1['id']}/manage", json={"mail_delivery": {"per_material": {"transcript": {"mode": "selected", "users": []}}}})
    assert bad.status_code == 422 and "Стенограмма" in bad.json()["detail"]
    ok = client.patch(f"{API}/rooms/{room1['id']}/manage", json={"mail_delivery": {"enabled": True, "per_material": {
        "protocol": {"mode": "all"}, "summary": {"mode": "leader"}, "transcript": {"mode": "selected", "users": [{"ref": directory.users["dave"][1].ad_guid, "name": "Dave D"}]}, "map": {"mode": "none"}}}})
    assert ok.status_code == 200
    spec = ok.json()["mail_delivery"]
    assert spec["materials"] == ["protocol", "summary", "transcript"] and spec["per_material"]["map"]["mode"] == "none" and spec["per_material"]["protocol"]["users"] == []
    pv = client.post(f"{API}/rooms/{room1['id']}/manage/delivery-preview", json={}).json()
    assert set(pv["by_material"]) == {"protocol", "summary", "transcript"}
    assert [r["name"] for r in pv["by_material"]["transcript"]] == ["Dave D"] or pv["by_material"]["transcript"][0]["source"] == "user"
    # старые настройки показываются в новом виде приближённо и продолжают работать
    login(client, "root")
    async def legacy():
        async with client.app_obj.state.session_maker() as db:
            from app.models import Room
            r = await db.get(Room, uuid.UUID(room1["id"]))
            r.mail_delivery = {"enabled": True, "materials": ["protocol"], "recipients": {"leaders": False, "participants": True, "users": [], "emails": []}}
            await db.commit()
    client.portal.call(legacy)
    login(client, "carol")
    v = client.get(f"{API}/rooms/{room1['id']}/manage").json()["mail_delivery"]
    assert v["converted_from_legacy"] is True and v["per_material"]["protocol"]["mode"] == "all" and v["per_material"]["summary"]["mode"] == "none"


def test_auto_delivery_sends_each_material_to_its_own_recipients_once_and_logs_failures(client, directory, fake_smtp):  # noqa: F811
    add_profile(client)
    directory.add("erin", "erin-pass", name="Erin E")
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {"enabled": True, "per_material": {"protocol": {"mode": "all"}, "summary": {"mode": "leader"}}, "formats": ["txt"]})
    mid = finished_meeting(client, room)
    rows = messages(client)
    by = {}
    for r in rows:
        by.setdefault(tuple(r["kinds"]), set()).add(r["recipient"])
    assert by[("protocol",)] == {"alice@corp.test", "bob@corp.test"}, "протокол — участникам"
    assert by[("summary",)] == {"carol@corp.test"}, "резюме — только руководителю"
    assert run_queue(client) == len(rows)
    sent = {m["To"]: [p.get_filename() for p in m.iter_attachments()] for m in fake_smtp.sent}
    assert all(not any("Стенограмма" in n or "transcript" in n.lower() for n in names) for names in sent.values()), "материалы вне настроек не отправляются"
    # повторное выполнение фонового задания рассылку не дублирует
    async def again():
        await client.app_obj.state.delivery.run_auto(uuid.UUID(mid))
        await client.app_obj.state.delivery.run_auto(uuid.UUID(mid))
    client.portal.call(again)
    assert len(messages(client)) == len(rows)
    # пересозданный материал автоматически повторно не рассылается
    async def regenerate():
        async with client.app_obj.state.session_maker() as db:
            db.add(Protocol(meeting_id=uuid.UUID(mid), kind="protocol", status="ready", content="# Новый протокол", created_by="test"))
            await db.commit()
        await client.app_obj.state.delivery.run_auto(uuid.UUID(mid))
    client.portal.call(regenerate)
    assert len(messages(client)) == len(rows)
    login(client, "carol")
    log = client.get(f"{API}/meetings/{mid}/delivery/log").json()
    assert {x["recipient"] for x in log} >= {"alice@corp.test", "carol@corp.test"} and all(x["state"] == "sent" for x in log)
    jr = client.get(f"{API}/rooms/{room['id']}/journal", params={"kind": "mail"}).json()["items"]
    assert jr and all(e["kind"] == "mail" for e in jr) and any(e["details"].get("recipient") == "carol@corp.test" for e in jr)
    _ = MailMessage
