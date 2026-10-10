"""Публичный API v1: ключи и права, область комнат (BOLA), ротация, лимиты, пагинация, форматы, журнал, описание. Без сети."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select

from app.models import (ApiKey, AuditLog, Meeting, MeetingChatMessage, MeetingParticipant, Protocol, TranscriptSegment, User, utcnow)
from app.publicapi import ids, keys

from .conftest import login, make_room, make_settings, put_settings, running_app

P = "/api/public/v1"
ALL = ["rooms:read", "meetings:read", "meetings:end", "transcripts:read", "protocols:read", "summaries:read", "maps:read", "recordings:read", "messages:read"]


# ------------------------------------------------------------------------------------------------ помощники
def hdr(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def make_client(c, *, name="integration", scopes=None, rooms=None, ip_allowlist=None, key_days=None) -> tuple[dict, str]:
    """Сервисная учётная запись + ключ (через админский API). Возвращает (клиент, полный ключ)."""
    login(c, "root")
    r = c.post("/api/v1/admin/public-api/clients", json={"name": name, "scopes": ALL if scopes is None else scopes, "rooms": rooms, "ip_allowlist": ip_allowlist})
    assert r.status_code == 201, r.text
    cl = r.json()
    k = c.post(f"/api/v1/admin/public-api/clients/{cl['id']}/keys", json={"label": "main", "expires_in_days": key_days})
    assert k.status_code == 201, k.text
    c.cookies.clear()
    c.headers.pop("X-CSRF-Token", None)
    return cl, k.json()["secret"]


def enable(c):
    put_settings(c, "api", enabled=True)
    c.cookies.clear()
    c.headers.pop("X-CSRF-Token", None)


def run(c, coro_fn):
    return c.portal.call(coro_fn)


def uid(c, sam: str) -> uuid.UUID:
    async def get():
        async with c.app_obj.state.session_maker() as db:
            return (await db.execute(select(User.id).where(User.sam_account_name == sam))).scalar_one()
    return run(c, get)


def seed_meeting(c, room_id: str, *, hours_ago=1, active=False, segments=0, people=("alice",), chat=0) -> uuid.UUID:
    async def go():
        async with c.app_obj.state.session_maker() as db:
            start = utcnow() - dt.timedelta(hours=hours_ago)
            m = Meeting(room_id=uuid.UUID(room_id), livekit_room=f"m-{uuid.uuid4().hex}", started_at=start, ended_at=None if active else start + dt.timedelta(minutes=30),
                        end_reason=None if active else "manual")
            db.add(m)
            await db.flush()
            for sam in people:
                user = (await db.execute(select(User).where(User.sam_account_name == sam))).scalar_one()
                db.add(MeetingParticipant(meeting_id=m.id, user_id=user.id, joined_at=start, left_at=None if active else m.ended_at,
                                          snapshot={"name": user.display_name, "title": "Инженер", "department": "ИТ", "email": f"{sam}@corp.test", "phone": "+7 000"}))
            first = (await db.execute(select(User).where(User.sam_account_name == people[0]))).scalar_one() if people else None
            for i in range(segments):
                t = start + dt.timedelta(seconds=10 * i)
                db.add(TranscriptSegment(segment_uid=uuid.uuid4(), meeting_id=m.id, room_id=m.room_id, user_id=first.id if first else None, participant_identity="x",
                                         started_at=t, ended_at=t + dt.timedelta(seconds=4), text=f"Реплика номер {i}"))
            for i in range(chat):
                db.add(MeetingChatMessage(meeting_id=m.id, author_type="user", user_id=first.id if first else None, author_name="Алиса", text=f"Сообщение {i}"))
            await db.commit()
            return m.id
    return run(c, go)


def seed_doc(c, meeting_id: uuid.UUID, kind: str, status="ready") -> uuid.UUID:
    async def go():
        async with c.app_obj.state.session_maker() as db:
            p = Protocol(meeting_id=meeting_id, kind=kind, status=status, content="# Итоги\n\n- решили **всё**" if status == "ready" else None, title=f"Документ {kind}",
                         instruction="секретная инструкция", meta={"model": "m1"})
            db.add(p)
            await db.commit()
            return p.id
    return run(c, go)


def setup(tmp_path, directory, **kw):
    return running_app(make_settings(tmp_path), directory, **kw)


def err(r) -> dict:
    j = r.json()
    assert set(j) == {"error"} and {"code", "message", "request_id"} <= set(j["error"]), j
    return j["error"]


def users_ready(c):
    login(c, "alice")                      # создаёт запись пользователя
    c.cookies.clear()
    c.headers.pop("X-CSRF-Token", None)


# ------------------------------------------------------------------------------------------------ включение и подлинность
def test_api_is_off_by_default_and_has_json_errors(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        cl, key = make_client(c)
        r = c.get(f"{P}/me", headers=hdr(key))
        assert r.status_code == 503 and err(r)["code"] == "api_disabled"
        enable(c)
        assert c.get(f"{P}/me", headers=hdr(key)).status_code == 200


def test_missing_or_wrong_key_is_always_the_same_401(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        cl, key = make_client(c)
        enable(c)
        r = c.get(f"{P}/me")
        assert r.status_code == 401 and err(r)["code"] == "unauthorized" and r.headers["www-authenticate"] == "Bearer"
        key_id = key.split("_")[1]
        wrong_secret = f"pgk_{key_id}_{'A' * 43}"
        unknown_id = f"pgk_{'0' * 8}_{'A' * 43}"
        a, b, g = (c.get(f"{P}/me", headers=hdr(x)) for x in (wrong_secret, unknown_id, "garbage"))
        assert a.status_code == b.status_code == g.status_code == 401
        assert err(a)["code"] == err(b)["code"] == err(g)["code"] == "invalid_api_key", "неверный ключ не отличается от несуществующего"
        assert err(a)["message"] == err(b)["message"] == err(g)["message"]


def test_browser_session_cookie_is_not_an_api_credential(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        make_client(c)
        enable(c)
        login(c, "root")                                    # администратор с живой сессией
        assert c.get(f"{P}/rooms").status_code == 401, "cookie-сессия не заменяет ключ"
        assert c.get(f"{P}/meetings").status_code == 401


def test_key_secret_is_shown_once_and_stored_hashed(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        cl, key = make_client(c)
        secret = key.split("_", 2)[2]
        login(c, "root")
        listing = c.get("/api/v1/admin/public-api/clients").text
        assert secret not in listing and key not in listing

        async def rows():
            async with c.app_obj.state.session_maker() as db:
                return (await db.execute(select(ApiKey))).scalars().all()
        row = run(c, rows)[0]
        assert row.secret_hash == keys.hashlib.sha256(secret.encode()).hexdigest() and secret not in (row.secret_hash, row.last4, row.key_id)


# ------------------------------------------------------------------------------------------------ права и область комнат
def test_scopes_are_enforced_and_named_in_the_error(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        cl, key = make_client(c, scopes=["rooms:read"])
        enable(c)
        assert c.get(f"{P}/rooms", headers=hdr(key)).status_code == 200
        r = c.get(f"{P}/meetings", headers=hdr(key))
        assert r.status_code == 403 and err(r)["code"] == "insufficient_scope" and err(r)["required_scope"] == "meetings:read"
        mid = ids.pub("meeting", uuid.uuid4())
        assert c.post(f"{P}/meetings/{mid}/end", headers=hdr(key)).status_code == 403
        me = c.get(f"{P}/me", headers=hdr(key)).json()
        assert me["scopes"] == ["rooms:read"] and me["rooms"] is None


def test_room_scope_hides_other_rooms_as_not_found(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        a, b = make_room(c), make_room(c)
        users_ready(c)
        ma, mb = seed_meeting(c, a["id"]), seed_meeting(c, b["id"], segments=2, chat=1)
        seed_doc(c, mb, "protocol")
        cl, key = make_client(c, rooms=[a["id"]])
        enable(c)
        H = hdr(key)
        assert [x["id"] for x in c.get(f"{P}/rooms", headers=H).json()["items"]] == [ids.pub("room", uuid.UUID(a["id"]))]
        assert [x["id"] for x in c.get(f"{P}/meetings", headers=H).json()["items"]] == [ids.pub("meeting", ma)]
        nonexistent = c.get(f"{P}/meetings/{ids.pub('meeting', uuid.uuid4())}", headers=H)
        foreign = c.get(f"{P}/meetings/{ids.pub('meeting', mb)}", headers=H)
        assert foreign.status_code == nonexistent.status_code == 404
        assert err(foreign)["code"] == err(nonexistent)["code"] == "not_found" and err(foreign)["message"] == err(nonexistent)["message"], "существование чужого объекта не раскрывается"
        for tail in ("participants", "transcript", "documents", "map", "recordings", "messages"):
            assert c.get(f"{P}/meetings/{ids.pub('meeting', mb)}/{tail}", headers=H).status_code == 404, tail
        assert c.post(f"{P}/meetings/{ids.pub('meeting', mb)}/end", headers=H).status_code == 404
        assert c.get(f"{P}/rooms/{ids.pub('room', uuid.UUID(b['id']))}", headers=H).status_code == 404
        assert c.get(f"{P}/meetings", params={"room_id": ids.pub("room", uuid.UUID(b["id"]))}, headers=H).status_code == 404


def test_client_with_empty_room_list_sees_nothing(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        a = make_room(c)
        users_ready(c)
        seed_meeting(c, a["id"])
        cl, key = make_client(c, rooms=[])
        enable(c)
        assert c.get(f"{P}/meetings", headers=hdr(key)).json()["items"] == []
        assert c.get(f"{P}/rooms", headers=hdr(key)).json()["items"] == []


def test_ids_are_typed_and_foreign_prefix_is_rejected(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        m = seed_meeting(c, room["id"])
        cl, key = make_client(c)
        enable(c)
        assert c.get(f"{P}/meetings/{ids.pub('room', m)}", headers=hdr(key)).status_code == 404, "идентификатор комнаты не открывает встречу"
        assert c.get(f"{P}/meetings/{m}", headers=hdr(key)).status_code == 404, "голый UUID без префикса не принимается"
        assert c.get(f"{P}/meetings/mtg_zzzz", headers=hdr(key)).status_code == 404


# ------------------------------------------------------------------------------------------------ состояние ключа
def test_revoked_expired_disabled_and_ip_denied(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        cl, key = make_client(c)
        enable(c)
        H = hdr(key)
        assert c.get(f"{P}/me", headers=H).status_code == 200
        # истёк срок
        async def expire():
            async with c.app_obj.state.session_maker() as db:
                k = (await db.execute(select(ApiKey))).scalars().first()
                k.expires_at = utcnow() - dt.timedelta(seconds=1)
                await db.commit()
        run(c, expire)
        assert err(c.get(f"{P}/me", headers=H))["code"] == "key_expired"
        # отозван
        cl2, key2 = make_client(c, name="second")
        login(c, "root")
        kid = c.get("/api/v1/admin/public-api/clients").json()
        k2 = next(k for cc in kid if cc["name"] == "second" for k in cc["keys"])
        assert c.post(f"/api/v1/admin/public-api/keys/{k2['id']}/revoke").status_code == 204
        assert err(c.get(f"{P}/me", headers=hdr(key2)))["code"] == "key_revoked"
        # учётная запись отключена
        cl3, key3 = make_client(c, name="third")
        login(c, "root")
        assert c.patch(f"/api/v1/admin/public-api/clients/{cl3['id']}", json={"name": "third", "enabled": False, "scopes": ALL}).status_code == 200
        r = c.get(f"{P}/me", headers=hdr(key3))
        assert r.status_code == 403 and err(r)["code"] == "client_disabled"
        # адрес не разрешён (клиент теста приходит не из сети 192.0.2.0/24)
        cl4, key4 = make_client(c, name="fourth", ip_allowlist=["192.0.2.0/24"])
        r = c.get(f"{P}/me", headers=hdr(key4))
        assert r.status_code == 403 and err(r)["code"] == "ip_not_allowed"


def test_ip_allowlist_logic():
    assert keys.ip_allowed("192.0.2.17", ["192.0.2.0/24"]) and not keys.ip_allowed("198.51.100.1", ["192.0.2.0/24"])
    assert keys.ip_allowed("203.0.113.5", None) and keys.ip_allowed("203.0.113.5", [])
    assert not keys.ip_allowed("not-an-ip", ["192.0.2.0/24"]), "нечитаемый адрес клиента не проходит при заданном списке"
    assert keys.ip_allowed("2001:db8::1", ["2001:db8::/32"])


def test_rotation_has_a_grace_window_then_old_key_stops(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        cl, old = make_client(c)
        enable(c)
        login(c, "root")
        old_row = c.get("/api/v1/admin/public-api/clients").json()[0]["keys"][0]
        r = c.post(f"/api/v1/admin/public-api/keys/{old_row['id']}/rotate", json={"grace_hours": 24})
        assert r.status_code == 201 and r.json()["secret"].startswith("pgk_")
        new = r.json()["secret"]
        assert new != old
        c.cookies.clear()
        assert c.get(f"{P}/me", headers=hdr(old)).status_code == 200, "в окне совместимости работают оба ключа"
        assert c.get(f"{P}/me", headers=hdr(new)).status_code == 200
        login(c, "root")
        keys_now = {k["key_id"]: k for k in c.get("/api/v1/admin/public-api/clients").json()[0]["keys"]}
        assert keys_now[old_row["key_id"]]["expires_at"] is not None, "у прежнего ключа появился срок"
        r0 = c.post(f"/api/v1/admin/public-api/keys/{r.json()['id']}/rotate", json={"grace_hours": 0})
        c.cookies.clear()
        assert err(c.get(f"{P}/me", headers=hdr(new)))["code"] == "key_expired", "при grace_hours=0 прежний ключ перестаёт работать сразу"
        assert c.get(f"{P}/me", headers=hdr(r0.json()["secret"])).status_code == 200


# ------------------------------------------------------------------------------------------------ лимиты
def test_rate_limit_headers_and_429(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        cl, key = make_client(c)
        put_settings(c, "api", enabled=True, rate_read=3)
        c.cookies.clear()
        c.app_obj.state.api_cfg_cache = None
        H = hdr(key)
        r1 = c.get(f"{P}/me", headers=H)
        assert r1.headers["x-ratelimit-limit"] == "3" and r1.headers["x-ratelimit-remaining"] == "2" and r1.headers["x-ratelimit-class"] == "read"
        assert c.get(f"{P}/me", headers=H).status_code == 200 and c.get(f"{P}/me", headers=H).status_code == 200
        r4 = c.get(f"{P}/me", headers=H)
        assert r4.status_code == 429 and err(r4)["code"] == "rate_limited" and int(r4.headers["retry-after"]) >= 1 and r4.headers["x-ratelimit-remaining"] == "0"


def test_rate_limit_is_per_client_not_global(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        _, k1 = make_client(c, name="one")
        _, k2 = make_client(c, name="two")
        put_settings(c, "api", enabled=True, rate_read=1)
        c.cookies.clear()
        c.app_obj.state.api_cfg_cache = None
        assert c.get(f"{P}/me", headers=hdr(k1)).status_code == 200
        assert c.get(f"{P}/me", headers=hdr(k1)).status_code == 429
        assert c.get(f"{P}/me", headers=hdr(k2)).status_code == 200, "лимит одной интеграции не мешает другой"


# ------------------------------------------------------------------------------------------------ данные
def test_meetings_pagination_is_stable_and_complete(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        made = [seed_meeting(c, room["id"], hours_ago=h) for h in (1, 2, 3, 4, 5)]
        cl, key = make_client(c)
        enable(c)
        seen, cur = [], None
        for _ in range(10):
            r = c.get(f"{P}/meetings", params={"limit": 2, **({"after": cur} if cur else {})}, headers=hdr(key)).json()
            seen += [x["id"] for x in r["items"]]
            cur = r["next_cursor"]
            if not cur:
                break
        assert seen == [ids.pub("meeting", m) for m in made], "новые первыми, без повторов и пропусков"
        bad = c.get(f"{P}/meetings", params={"after": "!!!"}, headers=hdr(key))
        assert bad.status_code == 400 and err(bad)["code"] == "invalid_cursor"
        r = c.get(f"{P}/meetings", params={"limit": 0}, headers=hdr(key))
        assert r.status_code == 422 and err(r)["code"] == "validation_error" and err(r)["fields"]


def test_meeting_filters_and_fields(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        done = seed_meeting(c, room["id"], hours_ago=5, people=("alice",))
        live = seed_meeting(c, room["id"], hours_ago=0, active=True)
        cl, key = make_client(c)
        enable(c)
        H = hdr(key)
        assert [x["id"] for x in c.get(f"{P}/meetings", params={"state": "active"}, headers=H).json()["items"]] == [ids.pub("meeting", live)]
        assert [x["id"] for x in c.get(f"{P}/meetings", params={"state": "ended"}, headers=H).json()["items"]] == [ids.pub("meeting", done)]
        m = c.get(f"{P}/meetings/{ids.pub('meeting', done)}", headers=H).json()
        assert m["state"] == "ended" and m["duration_s"] == 1800 and m["participants_count"] == 1 and m["room"]["slug"] == room["slug"]
        assert c.get(f"{P}/rooms/{ids.pub('room', uuid.UUID(room['id']))}", headers=H).json()["active_meeting_id"] == ids.pub("meeting", live)


def test_participants_expose_name_title_but_no_contact_data(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        m = seed_meeting(c, room["id"])
        cl, key = make_client(c)
        enable(c)
        r = c.get(f"{P}/meetings/{ids.pub('meeting', m)}/participants", headers=hdr(key))
        item = r.json()["items"][0]
        assert item["title"] == "Инженер" and item["department"] == "ИТ" and item["type"] == "user" and item["id"].startswith("usr_")
        assert "@" not in r.text and "+7" not in r.text, "e-mail и телефон не передаются"


def test_transcript_json_cursor_and_text_formats(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        m = seed_meeting(c, room["id"], segments=5)
        cl, key = make_client(c)
        enable(c)
        H, base = hdr(key), f"{P}/meetings/{ids.pub('meeting', m)}/transcript"
        got, cur = [], None
        for _ in range(10):
            r = c.get(base, params={"limit": 2, **({"after": cur} if cur else {})}, headers=H).json()
            got += [s["text"] for s in r["items"]]
            cur = r["next_cursor"]
            if not cur:
                break
        assert got == [f"Реплика номер {i}" for i in range(5)]
        txt = c.get(base, params={"format": "txt"}, headers=H)
        assert txt.status_code == 200 and "Реплика номер 0" in txt.text and txt.text.startswith("Переговорка:")
        md = c.get(base, params={"format": "md"}, headers=H)
        assert md.headers["content-type"].startswith("text/markdown") and md.text.startswith("# Стенограмма:") and "**[" in md.text
        vtt = c.get(base, params={"format": "vtt"}, headers=H).text
        assert vtt.startswith("WEBVTT") and "00:00:00.000 --> 00:00:04.000" in vtt and "00:00:10.000 --> 00:00:14.000" in vtt
        srt = c.get(base, params={"format": "srt"}, headers=H).text
        assert srt.startswith("1\n00:00:00,000 --> 00:00:04,000") and "\n2\n00:00:10,000" in srt
        assert c.get(base, params={"format": "exe"}, headers=H).status_code == 422


def test_documents_respect_per_kind_scopes_and_hide_cross_meeting_ids(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        m1, m2 = seed_meeting(c, room["id"], hours_ago=2), seed_meeting(c, room["id"], hours_ago=1)
        pro, summ = seed_doc(c, m1, "protocol"), seed_doc(c, m1, "summary")
        other = seed_doc(c, m2, "protocol")
        seed_doc(c, m1, "protocol", status="pending")
        cl, key = make_client(c, scopes=["meetings:read", "protocols:read"])
        enable(c)
        H, M = hdr(key), ids.pub("meeting", m1)
        kinds = {d["kind"] for d in c.get(f"{P}/meetings/{M}/documents", headers=H).json()["items"]}
        assert kinds == {"protocol"}, "резюме не видно без summaries:read"
        r = c.get(f"{P}/meetings/{M}/documents/{ids.pub('protocol', summ)}", headers=H)
        assert r.status_code == 403 and err(r)["required_scope"] == "summaries:read"
        full = c.get(f"{P}/meetings/{M}/documents/{ids.pub('protocol', pro)}", headers=H).json()
        assert full["content"].startswith("# Итоги") and full["model"] == "m1"
        assert c.get(f"{P}/meetings/{M}/documents/{ids.pub('protocol', other)}", headers=H).status_code == 404, "документ другой встречи не отдаётся по этому адресу"
        assert c.get(f"{P}/meetings/{M}/documents/{ids.pub('protocol', pro)}", params={"format": "md"}, headers=H).text.startswith("# Итоги")
        assert "<" in c.get(f"{P}/meetings/{M}/documents/{ids.pub('protocol', pro)}", params={"format": "html"}, headers=H).text
        _, k_none = make_client(c, name="none2", scopes=["meetings:read"])
        assert c.get(f"{P}/meetings/{M}/documents", headers=hdr(k_none)).status_code == 403


def test_messages_pagination_and_recordings_metadata(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        m = seed_meeting(c, room["id"], chat=5)
        cl, key = make_client(c)
        enable(c)
        H, base = hdr(key), f"{P}/meetings/{ids.pub('meeting', m)}"
        got, cur = [], None
        for _ in range(10):
            r = c.get(f"{base}/messages", params={"limit": 2, **({"after": cur} if cur else {})}, headers=H).json()
            got += [x["text"] for x in r["items"]]
            cur = r["next_cursor"]
            if not cur:
                break
        assert got == [f"Сообщение {i}" for i in range(5)]
        assert c.get(f"{base}/recordings", headers=H).json() == {"items": []}
        assert c.get(f"{base}/map", headers=H).status_code == 404


def test_end_meeting_is_idempotent_and_audited(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        live = seed_meeting(c, room["id"], active=True)
        cl, key = make_client(c, name="crm")
        enable(c)
        url = f"{P}/meetings/{ids.pub('meeting', live)}/end"
        r1 = c.post(url, headers=hdr(key))
        assert r1.status_code == 200 and r1.json() == {"id": ids.pub("meeting", live), "state": "ended", "already_ended": False}
        r2 = c.post(url, headers=hdr(key))
        assert r2.status_code == 200 and r2.json()["already_ended"] is True

        async def audit():
            async with c.app_obj.state.session_maker() as db:
                return (await db.execute(select(AuditLog).where(AuditLog.action == "meeting.end"))).scalars().all()
        rows = run(c, audit)
        assert len(rows) == 1 and rows[0].actor_name == "api:crm" and rows[0].details["via"] == "public_api"


# ------------------------------------------------------------------------------------------------ администрирование
def test_admin_endpoints_are_admin_only_and_validate_input(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        login(c, "alice")
        assert c.get("/api/v1/admin/public-api/clients").status_code == 403
        assert c.post("/api/v1/admin/public-api/clients", json={"name": "x", "scopes": []}).status_code == 403
        login(c, "root")
        bad = [{"name": "x", "scopes": ["root:all"]}, {"name": "x", "scopes": [], "ip_allowlist": ["300.1.1.1"]}, {"name": "x", "scopes": [], "rooms": [str(uuid.uuid4())]}, {"name": " ", "scopes": []}]
        for body in bad:
            assert c.post("/api/v1/admin/public-api/clients", json=body).status_code in (422,), body
        assert c.post("/api/v1/admin/public-api/clients", json={"name": "Dup", "scopes": ["rooms:read"], "rooms": [room["id"]]}).status_code == 201
        assert c.post("/api/v1/admin/public-api/clients", json={"name": "dup", "scopes": []}).status_code == 409
        assert "meetings:end" in {s["name"] for s in c.get("/api/v1/admin/public-api/scopes").json()["scopes"]}


def test_every_declared_scope_is_used_by_some_route(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        spec = c.get(f"{P}/openapi.json").json()
        used = {op.get("x-required-scope") for item in spec["paths"].values() for op in item.values()}
        from app.publicapi.scopes import SCOPES
        unused = set(SCOPES) - used - {"protocols:read", "summaries:read"}      # эти два проверяются внутри маршрута документов по виду документа
        assert not unused, f"scope без маршрута: {unused}"


def test_openapi_and_docs_do_not_need_a_key_and_describe_security(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        spec = c.get(f"{P}/openapi.json").json()
        assert spec["openapi"].startswith("3.1") and spec["components"]["securitySchemes"]["ApiKey"]["scheme"] == "bearer"
        assert f"{P}/meetings/{{meeting_id}}" in spec["paths"] and spec["paths"][f"{P}/meetings"]["get"]["x-required-scope"] == "meetings:read"
        assert "/api/v1/auth/login" not in spec["paths"], "в публичное описание не попадает внутренний API"
        page = c.get(f"{P}/docs")
        assert page.status_code == 200 and "<script" not in page.text.lower() and "style-src 'self'" in page.headers["content-security-policy"]
        assert "meetings:read" in page.text
        assert c.get(f"{P}/docs.css").headers["content-type"].startswith("text/css")


def test_unknown_public_path_has_the_public_error_format(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        r = c.get(f"{P}/nope")
        assert r.status_code == 404 and err(r)["code"] == "not_found"
        r = c.get("/api/v1/nope")
        assert r.status_code == 404 and "error" not in r.json(), "внутренний API сохранил прежний формат ошибок"


def test_request_log_is_batched_template_only_and_keeps_no_secrets(tmp_path, directory):
    with setup(tmp_path, directory) as c:
        room = make_room(c)
        users_ready(c)
        m = seed_meeting(c, room["id"])
        cl, key = make_client(c)
        enable(c)
        c.get(f"{P}/meetings/{ids.pub('meeting', m)}", headers=hdr(key))
        c.get(f"{P}/me", headers=hdr(f"pgk_{key.split('_')[1]}_{'B' * 43}"))
        run(c, c.app_obj.state.api_log.flush)
        login(c, "root")
        log = c.get("/api/v1/admin/public-api/log").json()
        assert {r["path"] for r in log} >= {f"{P}/meetings/{{meeting_id}}", f"{P}/me"}, "в журнале шаблон пути, а не идентификаторы"
        assert any(r["status"] == 401 and r["error_code"] == "invalid_api_key" for r in log)
        assert key.split("_", 2)[2] not in str(log) and ids.pub("meeting", m) not in str(log)
        assert c.get("/api/v1/admin/public-api/log", params={"status_from": 400}).json()
        login(c, "alice")
        assert c.get("/api/v1/admin/public-api/log").status_code == 403
