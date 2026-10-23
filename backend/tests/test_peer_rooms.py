from __future__ import annotations

from sqlalchemy import select

from app.models import Room
from app.services import peer_rooms as pr

from .conftest import login, make_room

P = "/api/v1/rooms/peer"
CODE = "abcdefghjkmnpqrstuvw"                      # 20 знаков безопасного алфавита


def _enable(client, **extra):
    login(client, "root")
    r = client.put("/api/v1/admin/settings/general", json={"peer_rooms_enabled": True, **extra})
    assert r.status_code == 200, r.text


def _open(client, user, code=CODE):
    login(client, user)
    return client.post(f"{P}/open", json={"code": code})


def test_disabled_by_default_and_hides_feature(client):
    login(client, "alice")
    p = client.get(f"{P}/policy").json()
    assert p["enabled"] is False and p["can_create"] is False
    assert client.post(f"{P}", json={"name": "x"}).status_code == 403
    r = client.post(f"{P}/open", json={"code": CODE})
    assert r.status_code == 404                                               # наличие функции не раскрывается


def test_first_visit_by_link_creates_room_and_second_user_joins_it_equally(client):
    _enable(client)
    a = _open(client, "alice")
    assert a.status_code == 200 and a.json()["created"] is True and a.json()["owner"] is True
    slug, rid = a.json()["slug"], a.json()["id"]
    assert slug == "p-" + CODE and a.json()["link"] == "/p/" + CODE
    b = _open(client, "bob")                                                  # уже существует — просто открыть
    assert b.status_code == 200 and b.json()["created"] is False and b.json()["id"] == rid and b.json()["owner"] is False
    # оба входят; руководителей нет, права равные
    ja = client.post(f"/api/v1/rooms/{rid}/join", json={})                    # bob
    assert ja.status_code == 200, ja.text
    jb = (login(client, "alice"), client.post(f"/api/v1/rooms/{rid}/join", json={}))[1]
    assert jb.status_code == 200
    for j in (ja.json(), jb.json()):
        assert j["client"]["can_moderate"] is False and j["client"]["can_control"] is True
        assert set(j["client"].get("publish_sources", ["microphone", "camera", "screen_share"])) >= {"microphone"}


def test_peer_room_not_listed_for_others_but_listed_for_owner_and_admin(client):
    _enable(client)
    rid = _open(client, "alice").json()["id"]
    up = lambda: [r["id"] for r in client.get("/api/v1/rooms/upcoming").json()["items"]]      # noqa: E731
    assert rid in up() and rid not in [r["id"] for r in client.get("/api/v1/rooms").json()], "временная комната — в «Предстоящих», не в каталоге переговорок"
    login(client, "bob")
    assert rid not in up()
    login(client, "root")
    assert rid in up()
    regular = make_room(client)                                                # обычные комнаты и их доступ не изменились
    login(client, "alice")
    assert regular["id"] in [r["id"] for r in client.get("/api/v1/rooms").json()]


def test_code_validation_and_rate_limits(client):
    _enable(client, peer_create_per_hour=2, peer_room_max_per_user=10)
    login(client, "alice")
    for bad in ("short", "ABCDEFGHJKMNPQRSTUVW0", "a" * 19, "с" * 20, "../etc/passwd", ""):
        assert client.post(f"{P}/open", json={"code": bad}).status_code == 404
    assert client.post(f"{P}/open", json={"code": "a" * 20}).status_code == 200
    assert client.post(f"{P}/open", json={"code": "b" * 20}).status_code == 200
    r = client.post(f"{P}/open", json={"code": "c" * 20})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
    assert client.post(f"{P}", json={}).status_code == 429                    # то же ограничение у кнопки


def test_limits_per_user_and_create_on_link_switch(client):
    _enable(client, peer_room_max_per_user=1)
    login(client, "alice")
    assert client.post(f"{P}", json={"name": "Первая"}).status_code == 201
    r = client.post(f"{P}", json={"name": "Вторая"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "limit_user"
    _enable(client, peer_create_on_link=False, peer_room_max_per_user=5)
    assert _open(client, "bob", "m" * 20).status_code == 404                    # создание по ссылке выключено
    login(client, "bob")
    assert client.post(f"{P}", json={}).status_code == 201                       # кнопкой — можно


def test_owner_controls_access_invites_rotate_close_and_nonowner_cannot(client):
    _enable(client)
    login(client, "bob")
    bob_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    a = _open(client, "alice").json()
    rid = a["id"]
    login(client, "bob")
    assert client.patch(f"{P}/{rid}", json={"access": "invited"}).status_code == 404       # не владелец
    assert client.post(f"{P}/{rid}/close").status_code == 404
    login(client, "alice")
    r = client.patch(f"{P}/{rid}", json={"access": "invited", "invited": [bob_id], "extend_hours": 5})
    assert r.status_code == 200 and r.json()["access"] == "invited" and len(r.json()["invited"]) == 1
    assert client.patch(f"{P}/{rid}", json={"extend_hours": 9999}).status_code == 422
    assert client.patch(f"{P}/{rid}", json={"access": "everyone"}).status_code == 422
    assert _open(client, "bob").status_code == 200                              # приглашён
    login(client, "alice")
    carol_id = (login(client, "carol"), client.get("/api/v1/auth/me").json()["user"]["id"])[1]
    login(client, "alice")
    r = client.patch(f"{P}/{rid}", json={"invited_add": [carol_id, carol_id]})                # добавление без замены списка; повтор игнорируется
    assert r.status_code == 200 and len(r.json()["invited"]) == 2
    assert _open(client, "carol").status_code == 200
    login(client, "alice")
    ref = next(i["ref"] for i in client.get(f"{P}/{rid}").json()["invited"] if i["name"] == "Carol C")
    assert len(client.patch(f"{P}/{rid}", json={"invited_remove": [ref]}).json()["invited"]) == 1
    assert _open(client, "carol").status_code == 404                              # снова не приглашена
    assert _open(client, "carol").status_code == 404                            # не приглашена: комната «не существует»
    login(client, "alice")
    new = client.post(f"{P}/{rid}/rotate").json()
    assert new["slug"] != a["slug"] and new["link"] != a["link"]
    login(client, "bob")
    assert client.post(f"{P}/open", json={"code": CODE}).status_code == 404         # прежняя ссылка мертва и новую комнату по ней не создаёт
    login(client, "alice")
    assert client.post(f"{P}/{rid}/close").status_code == 204
    assert _open(client, "alice", new["slug"].removeprefix("p-")).status_code == 404       # закрытая недоступна по ссылке


def test_admin_can_close_any_peer_room_and_audit(client):
    _enable(client)
    rid = _open(client, "alice").json()["id"]
    login(client, "root")
    assert client.post(f"{P}/{rid}/close").status_code == 204
    acts = [a["action"] for a in client.get("/api/v1/admin/audit").json()]
    assert {"room.create_peer", "room.close_peer"} <= set(acts)
    assert _open(client, "bob").status_code == 404                                  # закрытая комната по ссылке недоступна


def test_room_without_leaders_lets_any_participant_control_recording(client):
    _enable(client)
    rid = _open(client, "alice").json()["id"]
    j = client.post(f"/api/v1/rooms/{rid}/join", json={})
    assert j.status_code == 200 and j.json()["client"]["can_control"] is True


def test_helpers():
    assert pr.CODE_RE.match(pr.new_code()) and len(pr.new_code()) == pr.CODE_LEN
    assert len({pr.new_code() for _ in range(200)}) == 200
    assert not pr.CODE_RE.match("a" * 19) and not pr.CODE_RE.match("A" * 20) and not pr.CODE_RE.match("0" * 20)


def test_invitee_search_needs_two_chars_and_excludes_self(client):
    _enable(client)
    login(client, "bob")
    login(client, "alice")
    assert client.get(f"{P}/users/search", params={"q": "b"}).json() == []
    names = [u["name"] for u in client.get(f"{P}/users/search", params={"q": "bo"}).json()]
    assert "Bob B" in names and "Alice A" not in client.get(f"{P}/users/search", params={"q": "al"}).json().__repr__()


def test_unused_peer_room_survives_idle_minutes_but_closes_by_its_own_ttl(client):
    """Ссылку можно подготовить заранее: комната живёт по `peer_room_ttl_hours`, а не закрывается через 30 минут «простоя» как обычная временная."""
    from datetime import timedelta

    from app.models import utcnow

    _enable(client)
    rid = _open(client, "alice").json()["id"]
    app = client.app_obj

    async def age_and_reap(created_ago_min, close_in_hours):
        async with app.state.session_maker() as db:
            room = await db.get(Room, __import__("uuid").UUID(rid))
            room.created_at = utcnow() - timedelta(minutes=created_ago_min)
            room.auto_close_at = utcnow() + timedelta(hours=close_in_hours)
            await db.commit()
            await app.state.meetings.cleanup_temporary(db)
            await db.refresh(room)
            return room.lifecycle
    assert client.portal.call(age_and_reap, 300, 20) == "active"                 # создана 5 часов назад, срок ещё не вышел
    assert client.portal.call(age_and_reap, 300, -1) == "closed"                # срок вышел


def test_simultaneous_first_visits_by_link_open_the_same_room_instead_of_failing(client, monkeypatch):
    """Двойной переход по новой ссылке (повторная загрузка, двойной щелчок): второй запрос не должен получать «комната уже есть» — он открывает комнату, созданную первым."""
    _enable(client)
    login(client, "alice")
    real = pr.create
    state = {"n": 0}

    async def racing_create(db, redis, su, cfg, name, **kw):
        if state["n"] == 0:
            state["n"] = 1
            await real(db, redis, su, cfg, name, **kw)          # «параллельный запрос» успел создать комнату
            await db.commit()
        return await real(db, redis, su, cfg, name, **kw)       # а этот упрётся в занятый адрес

    monkeypatch.setattr(pr, "create", racing_create)
    r = client.post(f"{P}/open", json={"code": CODE})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == pr.slug_of(CODE) and r.json()["created"] is False
