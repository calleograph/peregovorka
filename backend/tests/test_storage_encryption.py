from __future__ import annotations

import base64
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.models import Meeting, MeetingChatMessage, Room, TranscriptSegment, crypto_events, utcnow
from app.security import storage_crypto as sc
from app.services import storage_encryption as se

from .conftest import login, make_room
from .test_collab_guests import _join

CHAT = "/api/v1/meetings/{}/chat"


# ------------------------------------------------------------------------------------------------ ядро
def test_keyring_roundtrip_aad_tamper_and_rotation():
    kr = sc.Keyring()
    kr.load([(1, os.urandom(32), False), (2, os.urandom(32), True)])
    t = kr.seal("секретная реплика", b"chat:m1")
    assert t.startswith("pgenc1:2:") and "секретная" not in t
    assert kr.open_(t, b"chat:m1") == "секретная реплика"
    with pytest.raises(sc.StorageCryptoError):
        kr.open_(t, b"chat:other")                                   # перенос шифртекста в другую встречу
    body = t.split(":", 2)[2]
    bad = "pgenc1:2:" + base64.b64encode(bytearray(base64.b64decode(body))[:-1] + b"\x00").decode()
    with pytest.raises(sc.StorageCryptoError):
        kr.open_(bad, b"chat:m1")                                    # подмена
    assert kr.open_("обычный текст", b"x") == "обычный текст"          # не шифртекст — возвращается как есть
    assert len({kr.seal("a", b"x") for _ in range(20)}) == 20          # nonce каждый раз новый
    old = sc.Keyring()
    old.load([(1, bytes(32), True)])
    t1 = old.seal("старое", b"a")
    kr2 = sc.Keyring()
    kr2.load([(1, bytes(32), False), (2, os.urandom(32), True)])
    assert kr2.open_(t1, b"a") == "старое"                            # после смены ключа старые записи читаются
    with pytest.raises(sc.StorageCryptoError):
        sc.Keyring().seal("x", b"a")                                  # без ключа шифровать нельзя


def test_wrap_unwrap_and_master_key_validation():
    mk = base64.b64encode(os.urandom(32)).decode()
    kek = sc.derive_kek(mk)
    dek = os.urandom(32)
    w = sc.wrap_key(kek, 7, dek)
    assert sc.unwrap_key(kek, 7, w) == dek and dek.hex() not in w
    with pytest.raises(sc.StorageCryptoError):
        sc.unwrap_key(kek, 8, w)                                       # обёртка привязана к номеру ключа
    with pytest.raises(sc.StorageCryptoError):
        sc.unwrap_key(sc.derive_kek(base64.b64encode(os.urandom(32)).decode()), 7, w)       # другой мастер-ключ
    for bad in ("", "не base64", base64.b64encode(b"short").decode()):
        with pytest.raises(sc.StorageCryptoError):
            sc.derive_kek(bad)


# ------------------------------------------------------------------------------------------------ сквозные сценарии
def _raw(client, sql, **p):
    async def run():
        async with client.app_obj.state.session_maker() as db:
            return (await db.execute(text(sql), p)).all()
    return client.portal.call(run)


def _enable(client, room_id, on=True):
    login(client, "root")
    r = client.patch(f"/api/v1/admin/rooms/{room_id}", json={"encrypt_storage": on})
    assert r.status_code == 200, r.text
    return r.json()


def _send(client, user, mid, body):
    login(client, user)
    r = client.post(CHAT.format(mid), json={"text": body})
    assert r.status_code == 201, r.text
    return r.json()


def test_chat_is_encrypted_in_db_and_readable_through_api(client):
    room = make_room(client)
    _enable(client, room["id"])
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    secret = f"совершенно секретно {uuid.uuid4().hex}"
    out = _send(client, "alice", mid, secret)
    assert out["text"] == secret                                       # в ответе — обычный текст
    rows = _raw(client, "select text from meeting_chat_messages where text like :m or text like :s", m="%" + secret[:20] + "%", s="pgenc1:%")
    assert rows and all(r[0].startswith("pgenc1:1:") and secret not in r[0] for r in rows)
    listed = client.get(CHAT.format(mid)).json()
    assert secret in [m["text"] for m in listed["messages"]] if isinstance(listed, dict) else secret in [m["text"] for m in listed]


def test_plain_room_stays_plain_and_toggle_applies_to_new_messages_only(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    _send(client, "alice", mid, "до включения")
    _enable(client, room["id"])
    _send(client, "alice", mid, "после включения")
    _enable(client, room["id"], False)
    _send(client, "alice", mid, "после выключения")
    rows = {r[0] for r in _raw(client, "select text from meeting_chat_messages")}
    assert "до включения" in rows and "после выключения" in rows
    assert not any("после включения" in r for r in rows) and any(r.startswith("pgenc1:") for r in rows)
    texts = [m["text"] for m in client.get(CHAT.format(mid)).json()["messages"]] if isinstance(client.get(CHAT.format(mid)).json(), dict) else [m["text"] for m in client.get(CHAT.format(mid)).json()]
    assert {"до включения", "после включения", "после выключения"} <= set(texts)          # всё читается


def test_transcript_segments_encrypted_and_readable(client):
    room = make_room(client)
    _enable(client, room["id"])
    a = _join(client, "alice", room["id"])
    mid = uuid.UUID(a["meeting_id"])
    app = client.app_obj
    uid = uuid.uuid4()
    now = utcnow()

    async def add():
        async with app.state.session_maker() as db:
            m = await db.get(Meeting, mid)
            db.add(TranscriptSegment(segment_uid=uid, meeting_id=mid, room_id=m.room_id, participant_identity="u-x", started_at=now, ended_at=now + timedelta(seconds=2),
                                     text="Решили перенести релиз на пятницу", language="ru"))
            await db.commit()
    client.portal.call(add)
    raw = _raw(client, "select text from transcript_segments where segment_uid = :u", u=uid.hex)
    assert raw and raw[0][0].startswith("pgenc1:") and "релиз" not in raw[0][0]

    async def read():
        async with app.state.session_maker() as db:
            return (await db.execute(select(TranscriptSegment).where(TranscriptSegment.segment_uid == uid))).scalar_one().text
    assert client.portal.call(read) == "Решили перенести релиз на пятницу"


def test_unreadable_record_shows_placeholder_instead_of_failing(client):
    room = make_room(client)
    _enable(client, room["id"])
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    _send(client, "alice", mid, "будет повреждено")
    _raw(client, "select 1")
    app = client.app_obj

    async def corrupt():
        async with app.state.session_maker() as db:
            await db.execute(text("update meeting_chat_messages set text = :t where text like 'pgenc1:%'"), {"t": "pgenc1:1:AAAA"})
            await db.commit()
    client.portal.call(corrupt)
    msgs = client.get(CHAT.format(mid)).json()
    items = msgs["messages"] if isinstance(msgs, dict) else msgs
    assert any(crypto_events.UNREADABLE == m["text"] for m in items)


def test_rotate_and_reseal_existing(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    _send(client, "alice", mid, "старое открытое")
    _enable(client, room["id"])
    _send(client, "alice", mid, "новое закрытое")
    login(client, "root")
    st = client.get("/api/v1/admin/crypto").json()
    assert st["ready"] and st["algorithm"] == "AES-256-GCM" and st["counts"]["meeting_chat_messages"]["sealed"] >= 1
    assert client.post("/api/v1/admin/crypto/rotate", json={"password": "wrong"}).status_code == 403
    new = client.post("/api/v1/admin/crypto/rotate", json={"password": "root-pass"}).json()["active"]
    assert new == 2
    _send(client, "alice", mid, "после смены ключа")
    login(client, "root")
    assert client.post("/api/v1/admin/crypto/reseal", json={"room_id": room["id"], "rotate_all": True}).status_code == 202
    import time
    for _ in range(50):
        job = client.get("/api/v1/admin/crypto").json()["job"]
        if job.get("state") in ("done", "failed"):
            break
        time.sleep(0.2)
    assert job["state"] == "done", job
    rows = [r[0] for r in _raw(client, "select text from meeting_chat_messages order by id")]
    assert all(r.startswith("pgenc1:2:") for r in rows), rows                 # открытое зашифровано, старые ключи переписаны
    login(client, "alice")
    msgs = client.get(CHAT.format(mid)).json()
    items = msgs["messages"] if isinstance(msgs, dict) else msgs
    assert {"старое открытое", "новое закрытое", "после смены ключа"} <= {m["text"] for m in items}
    login(client, "root")
    acts = [x["action"] for x in client.get("/api/v1/admin/audit").json()]
    assert {"crypto.rotate", "crypto.reseal"} <= set(acts)


def test_cannot_enable_without_keys_and_regular_user_has_no_access(client, monkeypatch):
    room = make_room(client)
    login(client, "alice")
    assert client.get("/api/v1/admin/crypto").status_code == 403
    login(client, "root")

    async def broken(*a, **k):
        raise RuntimeError("база недоступна")
    monkeypatch.setattr(se, "init", broken)                                    # ключи не загрузились и не загружаются
    sc.keyring.clear()
    client.app_obj.state.storage_keys_loaded = False
    r = client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"encrypt_storage": True})
    assert r.status_code == 409 and "APP_MASTER_KEY" in r.json()["detail"]
    assert client.get("/api/v1/admin/rooms/" + room["id"]).json()["encrypt_storage"] is False       # режим не включился
    monkeypatch.undo()
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"encrypt_storage": True}).status_code == 200       # ключи подгрузились при первой потребности


def test_new_rooms_default_follows_general_setting(client):
    login(client, "root")
    assert client.put("/api/v1/admin/settings/general", json={"encrypt_new_rooms": True}).status_code == 200
    r = client.post("/api/v1/admin/rooms", json={"slug": "enc-default", "name": "Секретная", "acl": []})
    assert r.status_code == 201 and r.json()["encrypt_storage"] is True
    r2 = client.post("/api/v1/admin/rooms", json={"slug": "enc-explicit-off", "name": "Открытая", "encrypt_storage": False, "acl": []})
    assert r2.json()["encrypt_storage"] is False                                # явный выбор администратора сильнее умолчания
