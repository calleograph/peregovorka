"""Руководитель комнаты: история и материалы встреч СВОЕЙ комнаты (в том числе без участия), доступ к отдельной встрече через каталог, рассылка по материалам, журнал комнаты.
Всё — через API с проверкой разграничения между комнатами; SMTP подменён."""
from __future__ import annotations

import uuid

from app.models import MailMessage, Protocol, User
from sqlalchemy import select

from .conftest import STAFF_GROUP, login, make_room, put_settings
from .test_admin_features import _drain
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
    assert upd["details"]["mail_delivery"]["after"]["per_material"]["protocol"]["mode"] == "leader"
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


# ---------------------------------------------------------------- права доступа ко ВСЕМ маршрутам встречи, подмена идентификаторов

SECRET = "СЕКРЕТНОЕ-СОДЕРЖИМОЕ-7731"


def _paths(client, mid, pid):
    """Все маршруты пользовательского API с `{meeting_id}` (кроме административных и публичного API по ключам): путь, метод."""
    out = []
    for p, ops in client.app_obj.openapi()["paths"].items():
        if not p.startswith("/api/v1/") or "{meeting_id}" not in p or p.startswith(("/api/v1/admin", "/api/public")):
            continue
        for m in sorted(set(ops) - {"head", "options", "parameters"}):
            url = (p.replace("{meeting_id}", mid).replace("{protocol_id}", pid).replace("{recording_id}", str(uuid.uuid4()))
                   .replace("{attachment_id}", str(uuid.uuid4())).replace("{user_id}", str(uuid.uuid4())).replace("{identity}", "u-x"))
            out.append((m.upper(), url.replace("{", "x").replace("}", "")))
    return out


def _protocol(client, mid):
    async def make():
        async with client.app_obj.state.session_maker() as db:
            p = Protocol(meeting_id=uuid.UUID(mid), kind="protocol", status="ready", content=SECRET, created_by="test", title="Т")
            db.add(p)
            await db.commit()
            return str(p.id)
    return client.portal.call(make)


def _hammer(client, mid, pid, allow_ok=("/release", "/leave")):   # собственные «выход»/«освободить доступ» безвредны и идемпотентны
    """Каждый маршрут встречи: чужому — ни одного 2xx и ни следа содержимого; возвращает коды."""
    bad = []
    paths = _paths(client, mid, pid)
    assert len(paths) > 40, "маршруты встречи не найдены — проверка была бы пустой"
    for method, url in paths:
        if any(url.endswith(a) or a in url for a in allow_ok):
            continue
        r = client.request(method, url, json={} if method != "GET" else None)
        if 200 <= r.status_code < 300 or SECRET in r.text:
            bad.append((method, url.replace(mid, "<m>").replace(pid, "<p>"), r.status_code))
        assert r.status_code < 500, (method, url, r.status_code, r.text[:200])
    return bad


def test_every_meeting_route_is_closed_for_strangers_and_after_revocation(client, directory):
    room1, room2 = setup_rooms(client, directory)
    mid = meeting_of(client, room1)
    pid = _protocol(client, mid)
    dave = directory.users["dave"][1]
    for who in ("dave", "bob"):                       # не имеет права / руководитель другой комнаты
        login(client, who)
        assert _hammer(client, mid, pid) == [], who
    login(client, "carol")
    assert client.post(f"{API}/meetings/{mid}/access", json={"ref": dave.ad_guid, "sam": "dave"}).status_code == 201
    uid = client.get(f"{API}/meetings/{mid}/access").json()["grants"][0]["user_id"]
    login(client, "dave")                              # право просмотра: материалы открываются, управление — нет
    assert client.get(f"{API}/meetings/{mid}/protocols/{pid}").json()["content"] == SECRET
    assert client.get(f"{API}/meetings/{mid}/transcript/export?format=txt").status_code == 200
    assert client.get(f"{API}/meetings/{mid}/protocols/{pid}/export?format=txt").status_code == 200
    for method, url in [("GET", f"/meetings/{mid}/access"), ("GET", f"/meetings/{mid}/delivery"), ("GET", f"/meetings/{mid}/delivery/log"), ("PUT", f"/meetings/{mid}/settings"),
                        ("POST", f"/meetings/{mid}/delivery/send"), ("DELETE", f"/meetings/{mid}/protocols/{pid}"), ("DELETE", f"/meetings/{mid}"), ("POST", f"/meetings/{mid}/protocols")]:
        r = client.request(method, API + url, json={})
        assert r.status_code in (403, 404, 422), (method, url, r.status_code)
    assert client.get(f"{API}/meetings/{mid}/protocols/{pid}").json()["content"] == SECRET, "чужие действия ничего не удалили"
    login(client, "carol")
    assert client.delete(f"{API}/meetings/{mid}/access/{uid}").status_code == 204
    login(client, "dave")
    assert _hammer(client, mid, pid) == [], "после отзыва закрыты все маршруты — не только интерфейс"
    assert client.get(f"{API}/meetings/{mid}/protocols/{pid}/export?format=txt").status_code in (403, 404)


def test_swapping_ids_never_crosses_meetings_or_rooms(client, directory):
    room1, room2 = setup_rooms(client, directory)
    m1, m2 = meeting_of(client, room1), meeting_of(client, room2)
    p1, p2 = _protocol(client, m1), _protocol(client, m2)
    login(client, "carol")                              # руководитель комнаты 1: своя встреча — да, чужой документ в своей встрече и чужая встреча — нет
    assert client.get(f"{API}/meetings/{m1}/protocols/{p1}").status_code == 200
    assert client.get(f"{API}/meetings/{m1}/protocols/{p2}").status_code == 404, "документ другой встречи по идентификатору своей"
    assert client.get(f"{API}/meetings/{m1}/protocols/{p2}/export?format=txt").status_code == 404
    assert client.get(f"{API}/meetings/{m2}/protocols/{p2}").status_code == 404
    assert client.get(f"{API}/meetings/{m2}/protocols/{p1}").status_code == 404
    assert client.patch(f"{API}/meetings/{m1}/protocols/{p2}", json={"content": "x"}).status_code in (403, 404)
    assert client.delete(f"{API}/meetings/{m1}/protocols/{p2}").status_code in (403, 404)
    login(client, "bob")                                # руководитель комнаты 2 — зеркально
    assert client.get(f"{API}/meetings/{m2}/protocols/{p2}").status_code == 200
    assert client.get(f"{API}/meetings/{m2}/protocols/{p1}").status_code == 404
    assert client.get(f"{API}/meetings/{m1}/protocols/{p1}").status_code == 404
    login(client, "root")
    assert client.get(f"{API}/meetings/{m2}/protocols/{p2}").json()["content"] == SECRET
    # и ключ-идентификатор выдачи доступа: отозвать чужое право через свою встречу нельзя
    login(client, "carol")
    dave = directory.users["dave"][1]
    login(client, "bob")
    client.post(f"{API}/meetings/{m2}/access", json={"ref": dave.ad_guid, "sam": "dave"})
    uid = client.get(f"{API}/meetings/{m2}/access").json()["grants"][0]["user_id"]
    login(client, "carol")
    assert client.delete(f"{API}/meetings/{m1}/access/{uid}").status_code in (204, 404)
    login(client, "dave")
    assert client.get(f"{API}/meetings/{m2}/protocols/{p2}").status_code == 200, "отзыв через встречу другой комнаты не затронул выданное право"


# ---------------------------------------------------------------- полнота журнала комнаты

def test_room_journal_records_every_listed_action_with_actor_time_and_meeting(tmp_path, directory, fake_smtp):  # noqa: F811
    from .test_access_and_protocols import configure, llm_app

    with llm_app(tmp_path, directory, []) as c:
        configure(c)
        directory.add("dave", "dave-pass", name="Dave D")
        room = make_room(c, name="Журналируемая", moderators=LEADERS)
        add_profile(c)
        set_delivery(c, room, {"enabled": True, "per_material": {"protocol": {"mode": "leader"}}, "formats": ["txt"]})   # изменение настроек — событие
        a = _join(c, "alice", room["id"])                                                                               # вход, начало
        _feed_and_consume(c, [_segment(a["meeting_id"], a["identity"], "Принято решение")])
        mid = a["meeting_id"]
        login(c, "carol")
        r = c.put(f"{API}/meetings/{mid}/settings", json={"delivery": {"enabled": True, "per_material": {"summary": {"mode": "leader"}}}})  # настройки встречи
        assert r.status_code == 200, r.text
        login(c, "alice")
        assert c.post(f"{API}/meetings/{mid}/leave").status_code == 204                                                  # выход
        login(c, "root")
        assert c.post(f"{API}/meetings/{mid}/end").status_code == 204                                                    # завершение
        run_queue(c)
        login(c, "carol")
        p1 = c.post(f"{API}/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x"}).json()["protocol_id"]       # формирование
        _drain(c)
        c.post(f"{API}/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "заново"})                             # пересоздание
        _drain(c)
        assert c.patch(f"{API}/meetings/{mid}/protocols/{p1}", json={"content": "# Правка"}).status_code == 200                   # правка
        assert c.get(f"{API}/meetings/{mid}/protocols/{p1}/export", params={"format": "txt"}).status_code == 200                  # скачивание
        assert c.post(f"{API}/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["carol@corp.test"]}).status_code in (200, 202)   # ручная отправка
        run_queue(c)
        dave = directory.users["dave"][1]
        assert c.post(f"{API}/meetings/{mid}/access", json={"ref": dave.ad_guid, "sam": "dave"}).status_code == 201
        uid = c.get(f"{API}/meetings/{mid}/access").json()["grants"][0]["user_id"]
        assert c.delete(f"{API}/meetings/{mid}/access/{uid}").status_code == 204
        login(c, "root")
        assert c.delete(f"{API}/meetings/{mid}/protocols/{p1}").status_code == 204                                                # удаление материала
        login(c, "carol")
        items = c.get(f"{API}/rooms/{room['id']}/journal", params={"limit": 500}).json()["items"]
        actions = {e["action"] for e in items}
        need = {"meeting.join", "meeting.leave", "meeting.start", "protocol.create", "protocol.edit", "protocol.export", "protocol.delete", "meeting.materials.send",
                "meeting.settings.update", "room.manage.update", "meeting.grant", "meeting.revoke", "mail.sent"}
        assert need <= actions, f"в журнале нет: {sorted(need - actions)}"
        assert actions & {"meeting.end", "meeting.ended"}, "завершение встречи"
        assert sum(1 for e in items if e["action"] == "protocol.create") >= 2, "формирование и пересоздание записаны отдельно"
        for e in items:
            assert e["actor"], f"нет исполнителя: {e['action']}"
            assert e["at"].endswith("+00:00"), f"время не в UTC: {e['action']}"
            if e["kind"] in ("access", "materials", "mail", "participants") or e["action"].startswith("meeting."):
                assert e["meeting_id"] == mid, f"нет встречи: {e['action']}"
        sent = [e for e in items if e["action"] == "mail.sent"]
        assert sent and all(e["details"]["recipient"] and e["details"]["kinds"] for e in sent), "отправка — с получателем и материалами; результат в поле result"
        assert any(e["action"] == "room.manage.update" and "before" in e["details"].get("mail_delivery", {}) and "after" in e["details"]["mail_delivery"] for e in items), "что изменено в рассылке: было/стало"
        assert not any(w in str(items).lower() for w in ("pass", "secret", SECRET.lower()))


# ---------------------------------------------------------------- фактическая отправка четырёх материалов (SMTP подменён), повтор после сбоя, пересоздание

def test_all_four_materials_reach_their_own_recipients_survive_smtp_failure_and_are_not_resent(client, directory, fake_smtp):  # noqa: F811
    from .test_mail import FakeSMTP
    from .test_mail_map import add_map

    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    carol_ref = directory.users["carol"][1].ad_guid
    set_delivery(client, room, {"enabled": True, "formats": ["txt"], "per_material": {
        "protocol": {"mode": "all"}, "summary": {"mode": "leader"},
        "transcript": {"mode": "selected", "users": [{"ref": carol_ref, "name": "Carol C", "email": "carol@corp.test"}]}, "map": {"mode": "all"}}})
    FakeSMTP.fail_at = "connect"                                                      # почтовый сервер недоступен в момент автоотправки
    joins = [_join(client, u, room["id"]) for u in ("alice", "bob")]
    mid = joins[0]["meeting_id"]
    _feed_and_consume(client, [_segment(mid, joins[0]["identity"], f"Реплика {i}", offset=i * 3) for i in range(7)])
    add_map(client, mid)

    async def prepare():
        async with client.app_obj.state.session_maker() as db:
            for kind, text in (("protocol", "# Протокол\n\nРешили."), ("summary", "Кратко: тест.")):
                db.add(Protocol(meeting_id=uuid.UUID(mid), kind=kind, status="ready", content=text, created_by="test"))
            await db.commit()
    client.portal.call(prepare)
    login(client, "root")
    assert client.post(f"{API}/meetings/{mid}/end").status_code == 204
    _drain(client)
    run_queue(client)                                                                  # попытка отправки при недоступном сервере
    rows = messages(client)
    assert rows and all(r["state"] in ("queued", "failed") for r in rows) and all(r["attempts"] >= 1 for r in rows), "письма в очереди, материал не потерян"
    login(client, "carol")
    assert client.get(f"{API}/meetings/{mid}/protocols").json(), "сам материал остался на месте"
    log = client.get(f"{API}/meetings/{mid}/delivery/log").json()
    assert all(x["state"] != "sent" for x in log) and any(x["error"] for x in log) or not log, "ошибка видна в журнале доставки"
    FakeSMTP.fail_at = None

    async def due():                                                                   # наступило время повторной попытки
        from sqlalchemy import update

        from app.models import utcnow
        async with client.app_obj.state.session_maker() as db:
            await db.execute(update(MailMessage).values(next_attempt_at=utcnow()))
            await db.commit()
    client.portal.call(due)
    run_queue(client, passes=3)
    got: dict[str, set[str]] = {}
    for m in fake_smtp.sent:
        names = {p.get_filename() for p in m.iter_attachments()}
        got.setdefault(m["To"], set()).update(names)
    sent_ids = [(m["To"], tuple(sorted(p.get_filename() for p in m.iter_attachments()))) for m in fake_smtp.sent]
    assert len(sent_ids) == len(set(sent_ids)), "одно и то же письмо не уходит дважды"
    assert any("карт" in n.lower() or n.lower().endswith(".html") for n in got.get("carol@corp.test", set()) | got.get("alice@corp.test", set())), got
    assert any("стенограмм" in n.lower() or "transcript" in n.lower() for n in got.get("carol@corp.test", set())), (got, "стенограмма — выбранному")
    assert not any("стенограмм" in n.lower() or "transcript" in n.lower() for n in got.get("alice@corp.test", set()) | got.get("bob@corp.test", set())), "участникам стенограмма не уходит"
    before = len(fake_smtp.sent)
    client.portal.call(lambda: client.app_obj.state.delivery.run_auto(uuid.UUID(mid)))
    run_queue(client, passes=2)
    assert len(fake_smtp.sent) == before, "повторное автоматическое выполнение не отправляет ничего"
    # пересоздание: автоматически не уходит; вручную — по явному действию руководителя
    async def regenerate():
        async with client.app_obj.state.session_maker() as db:
            db.add(Protocol(meeting_id=uuid.UUID(mid), kind="protocol", status="ready", content="# Пересозданный", created_by="test"))
            await db.commit()
        await client.app_obj.state.delivery.run_auto(uuid.UUID(mid))
    client.portal.call(regenerate)
    run_queue(client, passes=2)
    assert len(fake_smtp.sent) == before, "пересозданный материал сам не рассылается"
    login(client, "carol")
    assert client.post(f"{API}/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["carol@corp.test"]}).status_code in (200, 202)
    run_queue(client, passes=2)
    assert len(fake_smtp.sent) == before + 1, "ручная отправка пересозданного — ровно одно письмо"
