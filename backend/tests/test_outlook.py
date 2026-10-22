from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Room, utcnow
from app.services import outlook as ol

from .conftest import login

O = "/api/v1/outlook"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _enable(client, **extra):
    login(client, "root")
    r = client.put("/api/v1/admin/settings/outlook", json={"enabled": True, **extra})
    assert r.status_code == 200, r.text


def _token(client, user="alice"):
    login(client, user)
    r = client.post(f"{O}/token")
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _body(key=None, start_in=60, length=30, **extra):
    s = utcnow() + timedelta(minutes=start_in)
    return {"key": key or ("k" + uuid.uuid4().hex), "subject": "Планёрка", "start": iso(s), "end": iso(s + timedelta(minutes=length)), **extra}


def _anon(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


def test_disabled_by_default(client):
    login(client, "alice")
    assert client.post(f"{O}/token").status_code == 404
    assert client.get(f"{O}/config").json()["enabled"] is False
    assert client.get(f"{O}/manifest.xml").status_code == 404


def test_token_only_for_logged_in_and_addin_endpoints_need_it(client):
    _enable(client)
    _anon(client)
    assert client.post(f"{O}/token").status_code == 401
    assert client.post(f"{O}/meetings", json=_body()).status_code == 401
    assert client.post(f"{O}/meetings", json=_body(), headers={"Authorization": "Bearer pgo_bogus"}).status_code == 401
    assert client.post(f"{O}/meetings", json=_body(), headers={"Authorization": "Bearer nottoken"}).status_code == 401


def test_create_is_idempotent_and_returns_invite_with_link(client):
    _enable(client)
    h = _token(client)
    b = _body(key="draft-" + uuid.uuid4().hex)
    _anon(client)
    r = client.post(f"{O}/meetings", json=b, headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["created"] is True and j["url"].startswith("http") and "/m/" in j["url"] and j["state"] == "waiting"
    assert j["url"] in j["invite_text"] and j["url"] in j["invite_html"] and "10 мин" in j["invite_text"]
    again = client.post(f"{O}/meetings", json=b, headers=h).json()                       # повторное нажатие кнопки / черновик / удалённая ссылка
    assert again["created"] is False and again["id"] == j["id"] and again["url"] == j["url"]
    assert client.get(f"{O}/meetings/by-key/{b['key']}", headers=h).json()["id"] == j["id"]
    assert client.get(f"{O}/meetings/by-key/unknownunknownunknown", headers=h).status_code == 404
    # другой пользователь с тем же ключом получает свою комнату
    h2 = _token(client, "bob")
    _anon(client)
    other = client.post(f"{O}/meetings", json=b, headers=h2).json()
    assert other["created"] is True and other["id"] != j["id"]


def test_validation(client):
    _enable(client)
    h = _token(client)
    _anon(client)
    ok = _body()
    for bad in ({**ok, "key": "короткий"}, {**ok, "key": "x" * 10}, {**ok, "end": ok["start"]}, {**ok, "start": "вчера"},
                {**_body(start_in=-600)}, {**_body(length=60 * 25)}, {**ok, "start": iso(utcnow() + timedelta(days=900)), "end": iso(utcnow() + timedelta(days=900, hours=1))}):
        assert client.post(f"{O}/meetings", json=bad, headers=h).status_code == 422, bad
    assert client.post(f"{O}/meetings", json={**_body(length=60 * 30), "recurring": True}, headers=h).status_code == 200      # серия: длительность не ограничена


def test_reschedule_cancel_reopen_and_ownership(client):
    _enable(client)
    h = _token(client)
    _anon(client)
    j = client.post(f"{O}/meetings", json=_body(), headers=h).json()
    new_start = utcnow() + timedelta(days=2)
    r = client.patch(f"{O}/meetings/{j['id']}", json={"start": iso(new_start), "end": iso(new_start + timedelta(hours=1)), "subject": "Перенесено"}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "Перенесено" and r.json()["opens_at"].startswith(iso(new_start - timedelta(minutes=10))[:16])
    assert client.patch(f"{O}/meetings/{j['id']}", json={"start": "плохо"}, headers=h).status_code == 422
    other = _token(client, "bob")
    _anon(client)
    assert client.patch(f"{O}/meetings/{j['id']}", json={"subject": "чужое"}, headers=other).status_code == 404
    assert client.delete(f"{O}/meetings/{j['id']}", headers=other).status_code == 404
    assert client.delete(f"{O}/meetings/{j['id']}", headers=h).status_code == 204
    code = j["url"].rsplit("/", 1)[1]
    assert client.get(f"{O}/public/{code}").json()["state"] == "cancelled"
    again = client.post(f"{O}/meetings", json={"key": client.get(f"{O}/meetings/by-key/" + "x" * 16, headers=h).text and "x" * 16, "subject": "s", "start": iso(new_start), "end": iso(new_start + timedelta(hours=1))}, headers=h)
    assert again.status_code == 200                                                    # новый ключ — новая комната, а не воскрешение чужой


def test_cancelled_room_is_reopened_by_pressing_the_button_again(client):
    _enable(client)
    h = _token(client)
    _anon(client)
    b = _body()
    j = client.post(f"{O}/meetings", json=b, headers=h).json()
    client.delete(f"{O}/meetings/{j['id']}", headers=h)
    r = client.post(f"{O}/meetings", json=b, headers=h).json()
    assert r["id"] == j["id"] and r["created"] is False and r["state"] == "waiting"


def test_public_countdown_page_state_and_join_gate(client):
    _enable(client, early_minutes=10)
    h = _token(client)
    _anon(client)
    soon = _body(start_in=60)
    j = client.post(f"{O}/meetings", json=soon, headers=h).json()
    code = j["url"].rsplit("/", 1)[1]
    pub = client.get(f"{O}/public/{code}")
    assert pub.status_code == 200 and pub.json()["state"] == "waiting" and pub.json()["name"] == "Планёрка" and pub.json()["server_time"] and pub.json()["guest_token"] is None
    assert client.get(f"{O}/public/{'z' * 20}").status_code == 404
    assert client.get(f"{O}/public/abc").status_code == 404
    # раньше окна — вход закрыт, внутри окна — открыт
    login(client, "bob")
    rid = j["id"]
    early = client.post(f"/api/v1/rooms/{rid}/join", json={})
    assert early.status_code == 425 and early.json()["detail"]["code"] == "too_early"
    async def shift(minutes):
        async with client.app_obj.state.session_maker() as db:
            room = await db.get(Room, uuid.UUID(rid))
            room.join_opens_at = utcnow() - timedelta(minutes=minutes)
            await db.commit()
    client.portal.call(shift, 1)
    inside = client.post(f"/api/v1/rooms/{rid}/join", json={})
    assert inside.status_code == 200, inside.text
    _anon(client)
    assert client.get(f"{O}/public/{code}").json()["state"] == "live"


def test_scheduled_room_hidden_from_lists_but_open_by_link_to_employees(client):
    _enable(client)
    h = _token(client)
    _anon(client)
    j = client.post(f"{O}/meetings", json=_body(), headers=h).json()
    login(client, "alice")
    assert j["id"] in [r["id"] for r in client.get("/api/v1/rooms").json()]
    login(client, "bob")
    assert j["id"] not in [r["id"] for r in client.get("/api/v1/rooms").json()]
    slug = "ol-" + j["url"].rsplit("/", 1)[1]
    assert client.get(f"/api/v1/rooms/resolve/{slug}").status_code == 200                      # по ссылке сотрудник находит комнату


def test_allowed_groups_policy_and_limits(client):
    _enable(client, allowed_groups="cn=staff,ou=groups,dc=corp,dc=test")
    login(client, "carol")                                                                    # carol не в staff
    assert client.post(f"{O}/token").status_code == 403
    login(client, "alice")
    assert client.post(f"{O}/token").status_code == 200
    _enable(client, allowed_groups="", max_active_per_user=1)
    h = _token(client)
    _anon(client)
    assert client.post(f"{O}/meetings", json=_body(), headers=h).status_code == 200
    r = client.post(f"{O}/meetings", json=_body(), headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "limit_user"


def test_recurring_series_has_no_time_gate(client):
    _enable(client)
    h = _token(client)
    _anon(client)
    j = client.post(f"{O}/meetings", json=_body(start_in=60 * 24 * 7, recurring=True), headers=h).json()
    assert j["recurring"] is True and j["state"] == "open"
    login(client, "bob")
    assert client.post(f"/api/v1/rooms/{j['id']}/join", json={}).status_code == 200


def test_guest_link_only_when_open(client):
    _enable(client, guest_links=True)
    h = _token(client)
    _anon(client)
    j = client.post(f"{O}/meetings", json=_body(), headers=h).json()
    code = j["url"].rsplit("/", 1)[1]
    assert client.get(f"{O}/public/{code}").json()["guest_token"] is None and client.get(f"{O}/public/{code}").json()["guests"] is True
    async def open_now():
        async with client.app_obj.state.session_maker() as db:
            room = await db.get(Room, uuid.UUID(j["id"]))
            room.join_opens_at = utcnow() - timedelta(minutes=1)
            await db.commit()
    client.portal.call(open_now)
    assert client.get(f"{O}/public/{code}").json()["guest_token"]                              # окно открылось — гостевая ссылка отдаётся


def test_manifest_is_valid_xml_with_stable_id_and_https(client):
    _enable(client, public_url="https://peregovorka.example.org")
    r = client.get(f"{O}/manifest.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(r.content)
    text = r.text
    assert "https://peregovorka.example.org/outlook/commands.html" in text and "AppointmentOrganizerCommandSurface" in text
    # кнопка открывает боковую панель (видимый ход операции, вход, ошибки и повтор), а не скрытую функцию
    assert '<Action xsi:type="ShowTaskpane">' in text and 'DefaultValue="https://peregovorka.example.org/outlook/taskpane.html"' in text and "ExecuteFunction" not in text
    assert "<ProviderName>calleograph</ProviderName>" in text
    for size in (16, 20, 24, 32, 40, 48, 64, 80):
        assert f'DefaultValue="https://peregovorka.example.org/outlook/icon-{size}.png"' in text
    assert "http://" not in text.replace("http://schemas.microsoft.com", "").replace("http://www.w3.org", "")
    gid = root.find("{http://schemas.microsoft.com/office/appforoffice/1.1}Id").text
    assert gid == client.get(f"{O}/manifest.xml").text.split("<Id>")[1].split("</Id>")[0]       # идентификатор стабилен
    login(client, "root")
    assert client.put("/api/v1/admin/settings/outlook", json={"public_url": "http://insecure.example.org"}).status_code == 422


def test_scheduled_room_not_closed_by_idle_rule_and_purged_when_unused(client):
    _enable(client, closed_retention_days=1)
    h = _token(client)
    _anon(client)
    j = client.post(f"{O}/meetings", json=_body(start_in=60 * 24 * 3), headers=h).json()       # через 3 дня: «простой» 30 минут к ней не применяется
    app = client.app_obj

    async def reap(age_created_min):
        async with app.state.session_maker() as db:
            room = await db.get(Room, uuid.UUID(j["id"]))
            room.created_at = utcnow() - timedelta(minutes=age_created_min)
            await db.commit()
            await app.state.meetings.cleanup_temporary(db)
            await db.refresh(room)
            return room.lifecycle
    assert client.portal.call(reap, 600) == "active"

    async def close_and_purge():
        async with app.state.session_maker() as db:
            room = await db.get(Room, uuid.UUID(j["id"]))
            room.lifecycle, room.closed_at = "closed", utcnow() - timedelta(days=2)
            await db.commit()
            n = await app.state.meetings.cleanup_temporary(db)
            return n, (await db.execute(select(Room).where(Room.id == uuid.UUID(j["id"])))).first()
    n, gone = client.portal.call(close_and_purge)
    assert n >= 1 and gone is None


def test_helpers():
    assert ol.KEY_RE.match("a" * 16) and not ol.KEY_RE.match("a" * 15) and not ol.KEY_RE.match("a b" * 8)
    cfg = ol.OutlookSettings(allowed_groups="CN=A,ou=x;cn=b\ncn=c")
    assert cfg.allowed_groups == "cn=a,ou=x;cn=b;cn=c"            # запятые внутри DN сохраняются, разделители — «;» и перевод строки
