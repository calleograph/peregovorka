"""«История» проверяет доступ пакетно (services.access.accessible_history), отдельная встреча — по одной (can_access_meeting).
Правила должны совпадать: в истории ровно те встречи, которые открываются по прямой ссылке."""
from __future__ import annotations

from .conftest import login, make_room
from .test_access_and_protocols import end_by_alice
from .test_mail import LEADERS
from .test_transcripts import _join

API = "/api/v1"


def test_history_lists_exactly_the_meetings_that_open_directly(client):
    plain = make_room(client, name="Обычная")
    policy = make_room(client, name="История участникам", history_access="participants")
    led = make_room(client, name="Под руководством carol", moderators=LEADERS)
    mids = {}

    def meeting(name, room, users, end=True, release=()):
        js = [_join(client, u, room["id"]) for u in users]
        mid = js[0]["meeting_id"]
        if end:
            end_by_alice(client, mid)
        for u in release:                      # ушёл со страницы встречи — «аренда» снята
            login(client, u)
            assert client.post(f"{API}/meetings/{mid}/release").status_code == 204
        mids[name] = mid

    meeting("released", plain, ["alice", "bob"], release=["alice", "bob"])        # никто не держит страницу, политики нет
    meeting("lease", plain, ["alice", "bob"], release=["alice"])                  # у bob «аренда» осталась
    meeting("policy", policy, ["alice", "bob"], release=["alice", "bob"])         # политика комнаты: участникам
    meeting("led", led, ["alice"], release=["alice"])                             # carol — руководитель комнаты, не участник
    meeting("granted", plain, ["alice"], release=["alice"])
    meeting("ongoing", plain, ["alice"], end=False)                               # идёт: видят участники
    login(client, "carol")
    carol_id = client.get(f"{API}/auth/me").json()["user"]["id"]
    login(client, "root")
    assert client.post(f"{API}/admin/meetings/{mids['granted']}/grants", json={"user_id": carol_id}).status_code == 201

    expected = {"alice": {"policy", "ongoing"}, "bob": {"lease", "policy"}, "carol": {"led", "granted"}}
    for user, names in expected.items():
        login(client, user)
        listed = {m["id"] for m in client.get(f"{API}/meetings?limit=100").json()}
        direct = {mid for mid in mids.values() if client.get(f"{API}/meetings/{mid}").status_code == 200}
        assert listed == direct, f"{user}: история и прямой доступ расходятся"
        assert listed == {mids[n] for n in names}, f"{user}: {sorted(n for n, m in mids.items() if m in listed)}"
        by_room = {m["id"] for m in client.get(f"{API}/meetings?room_id={plain['id']}&limit=100").json()}
        assert by_room == {m for m in listed if m in {mids[n] for n in ("released", "lease", "ongoing", "granted")}}
    login(client, "alice")
    page = [m["id"] for m in client.get(f"{API}/meetings?limit=1&offset=1").json()]
    full = [m["id"] for m in client.get(f"{API}/meetings?limit=100").json()]
    assert page == full[1:2], "постраничный вывод — срез того же порядка (новые сверху)"


def test_room_list_without_orm_matches_orm_rule_field_by_field(client, directory):
    """Список комнат строится из строк таблиц (RoomView): поля, видимость и счётчики — те же, что у вывода через объекты ORM по правилу _listed."""
    import json

    from sqlalchemy import select

    from app.api.rooms import room_out
    from app.auth.deps import SessionUser
    from app.models import Meeting, MeetingParticipant, Room
    from app.services.rooms import _listed

    login(client, "root")
    assert client.put(f"{API}/admin/settings/general", json={"peer_rooms_enabled": True}).status_code == 200
    bob_guid = directory.users["bob"][1].ad_guid
    make_room(client, name="Б: группа")
    make_room(client, name="А: только bob", acl=[{"subject_type": "user", "subject_ref": bob_guid}], description="для bob")
    make_room(client, name="В: руководит carol", moderators=LEADERS, guest_access_enabled=True)
    off = make_room(client, name="Г: выключена")
    assert client.patch(f"{API}/admin/rooms/{off['id']}", json={"is_enabled": False}).status_code == 200
    busy = make_room(client, name="Д: идёт встреча")
    _join(client, "alice", busy["id"]); _join(client, "bob", busy["id"])
    login(client, "alice")
    assert client.post(f"{API}/rooms/peer/open", json={"code": "abcdefghjkmnpqrstuvw"}).status_code == 200
    app = client.app_obj
    for user in ("alice", "bob", "carol", "root"):
        login(client, user)
        got = client.get(f"{API}/rooms").json()
        sid = client.cookies.get(app.state.settings.cookie_name)

        async def expected():
            su = SessionUser.from_session(sid, await app.state.sessions.get(sid))
            async with app.state.session_maker() as db:
                rooms = [r for r in (await db.execute(select(Room).where(Room.is_enabled.is_(True), Room.lifecycle != "closed").order_by(Room.name))).scalars().all() if _listed(r, su)]
                out = []
                for r in rooms:
                    m = (await db.execute(select(Meeting).where(Meeting.room_id == r.id, Meeting.ended_at.is_(None)))).scalars().first()
                    n = len({p.user_id for p in m.participants if p.left_at is None}) if m else 0
                    out.append((await room_out(db, r, m, n, su)).model_dump(mode="json"))
                return out
        want = client.portal.call(expected)
        assert json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True), user
        assert got, user
    login(client, "bob")
    names = [r["name"] for r in client.get(f"{API}/rooms").json()]
    assert "А: только bob" in names and "Г: выключена" not in names
    assert next(r for r in client.get(f"{API}/rooms").json() if r["name"] == "Д: идёт встреча")["active_meeting"]["participants"] == 2
