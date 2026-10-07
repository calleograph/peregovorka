"""Гостевой доступ, чат встречи и общая доска (draw.io): права, привязка к встрече, отзыв ссылки, материалы для протокола."""
from __future__ import annotations

import json

import httpx
import pytest

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_access_and_protocols import LLM_OK, configure
from .test_admin_features import _drain
from .test_transcripts import _feed_and_consume, _join, _segment

BOARD = ('<mxfile><diagram id="p1" name="Схема сети"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
         '<mxCell id="a" value="Шлюз &lt;b&gt;GW-1&lt;/b&gt;" style="rounded=1" vertex="1" parent="1"><mxGeometry width="80" height="40" as="geometry"/></mxCell>'
         '<mxCell id="b" value="База данных" vertex="1" parent="1"><mxGeometry x="200" width="80" height="40" as="geometry"/></mxCell>'
         '<mxCell id="e" value="TCP 5432" edge="1" parent="1" source="a" target="b"><mxGeometry relative="1" as="geometry"/></mxCell>'
         '</root></mxGraphModel></diagram></mxfile>')


def guest_room(client, **over):
    return make_room(client, guest_access_enabled=True, **over)


def guest_join(client, token: str, name: str = "Иван Иванов", **body):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    return client.post(f"/api/v1/guest/room/{token}/join", json={"display_name": name, **body})


def ghead(g: dict) -> dict:
    return {"X-Guest-Token": g["guest_token"]}


def started(client, room):
    """Сотрудник открывает встречу; возвращает (join, guest_token)."""
    a = _join(client, "alice", room["id"])
    return a


# ====================================================================================== гостевой доступ
def test_guest_link_is_issued_only_when_enabled_and_can_be_toggled_and_revoked(client):
    off = make_room(client)
    assert off["guest_access_enabled"] is False and off["guest_token"] is None
    room = guest_room(client)
    tok = room["guest_token"]
    assert room["guest_access_enabled"] and tok and len(tok) >= 20
    assert client.get(f"/api/v1/guest/room/{tok}").json()["room_name"] == "Переговорка"
    assert client.get("/api/v1/guest/room/not-a-real-token-123").status_code == 404

    # выключили — ссылка неактивна, но не теряется; включили — работает прежняя
    login(client, "root")
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"guest_access_enabled": False}).json()["guest_access_enabled"] is False
    assert client.get(f"/api/v1/guest/room/{tok}").status_code == 404
    assert client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"guest_access_enabled": True}).json()["guest_token"] == tok
    assert client.get(f"/api/v1/guest/room/{tok}").status_code == 200

    # перевыпуск: старая ссылка перестаёт работать, новая работает; отзыв — гостевой доступ выключается совсем
    new = client.post(f"/api/v1/admin/rooms/{room['id']}/guest-link/rotate").json()["guest_token"]
    assert new != tok and client.get(f"/api/v1/guest/room/{tok}").status_code == 404 and client.get(f"/api/v1/guest/room/{new}").status_code == 200
    revoked = client.post(f"/api/v1/admin/rooms/{room['id']}/guest-link/revoke").json()
    assert revoked["guest_token"] is None and revoked["guest_access_enabled"] is False
    assert client.get(f"/api/v1/guest/room/{new}").status_code == 404
    assert {"room.guest_link.rotate", "room.guest_link.revoke"} <= {a["action"] for a in client.get("/api/v1/admin/audit").json()}


def test_guest_cannot_start_a_meeting_and_joins_only_an_active_one(client):
    room = guest_room(client)
    r = guest_join(client, room["guest_token"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "meeting_not_active"
    started(client, room)
    ok = guest_join(client, room["guest_token"], "  Иван   Иванов  (гость) ")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["display_name"] == "Иван Иванов" and body["identity"].startswith("g-") and body["client"]["is_guest"] is True
    assert body["client"]["can_moderate"] is False and body["guest_token"]
    from livekit import api as lkapi

    claims = lkapi.TokenVerifier("devkey", "s" * 40).verify(body["token"])
    assert claims.identity == body["identity"] and claims.name == "Иван Иванов (гость)"
    assert claims.video.can_publish_data is False
    assert "screen_share" not in [str(s).lower() for s in claims.video.can_publish_sources]


def test_guest_is_a_separate_participant_type_shown_with_a_mark_and_has_no_user_rights(client):
    room = guest_room(client)
    a = started(client, room)
    g = guest_join(client, room["guest_token"]).json()
    login(client, "alice")
    m = client.get(f"/api/v1/meetings/{a['meeting_id']}").json()
    guests = [p for p in m["participants"] if p["participant_type"] == "guest"]
    assert len(guests) == 1 and guests[0]["display_name"] == "Иван Иванов (гость)" and guests[0]["user_id"] is None and m["guests"] == 1
    assert [p["display_name"] for p in m["participants"] if p["participant_type"] == "user"] == ["Alice A"]

    # гость не получает ничего из сотруднических и административных эндпоинтов
    gh = ghead(g)
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    for method, url in (("get", "/api/v1/rooms"), ("get", "/api/v1/meetings"), ("get", "/api/v1/admin/rooms"), ("get", f"/api/v1/meetings/{a['meeting_id']}"),
                        ("get", f"/api/v1/meetings/{a['meeting_id']}/transcript"), ("post", f"/api/v1/meetings/{a['meeting_id']}/end"),
                        ("post", f"/api/v1/meetings/{a['meeting_id']}/moderation/mute-all")):
        assert getattr(client, method)(url, headers=gh).status_code in (401, 403), url


def test_guest_session_is_bound_to_one_meeting_and_ends_with_it(client):
    room, other = guest_room(client), guest_room(client)
    a = started(client, room)
    b = _join(client, "bob", other["id"])
    g = guest_join(client, room["guest_token"]).json()
    gh = ghead(g)
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/chat", headers=gh).status_code == 200
    assert client.get(f"/api/v1/meetings/{b['meeting_id']}/chat", headers=gh).status_code == 404  # чужая встреча
    # после завершения встречи гость больше ничего не видит
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{a['meeting_id']}/end").status_code == 204
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/chat", headers=gh).status_code == 404
    assert client.post("/api/v1/guest/session/rejoin", headers=gh).status_code == 409


def test_revoking_the_link_disconnects_guests_but_not_employees(client):
    room = guest_room(client)
    a = started(client, room)
    g = guest_join(client, room["guest_token"]).json()
    login(client, "root")
    assert client.post(f"/api/v1/admin/rooms/{room['id']}/guest-link/revoke").status_code == 200
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/chat", headers=ghead(g)).status_code == 401
    assert client.post("/api/v1/guest/session/rejoin", headers=ghead(g)).status_code == 401
    login(client, "alice")
    m = client.get(f"/api/v1/meetings/{a['meeting_id']}").json()
    assert m["ended_at"] is None and [p["online"] for p in m["participants"] if p["participant_type"] == "guest"] == [False]
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/chat").status_code == 200


def test_guest_name_is_cleaned_and_join_is_rate_limited_and_respects_room_password(client):
    room = guest_room(client, password="секрет-комнаты")
    started_room = guest_room(client)
    _join(client, "alice", started_room["id"])
    tok = started_room["guest_token"]
    assert guest_join(client, tok, "A").status_code == 422
    name = guest_join(client, tok, "‮Пётр\x00 Петров").json()["display_name"]
    assert name == "Пётр Петров"
    # пароль комнаты действует и для гостей
    login(client, "bob")
    pr = _join_with_password(client, room, "секрет-комнаты")
    assert pr.status_code == 200
    assert guest_join(client, room["guest_token"]).status_code == 403
    assert guest_join(client, room["guest_token"], password="неверно").json()["detail"]["code"] == "room_password_invalid"
    assert guest_join(client, room["guest_token"], password="секрет-комнаты").status_code == 200


def _join_with_password(client, room, password):
    return client.post(f"/api/v1/rooms/{room['id']}/join", json={"password": password})


def test_guest_websocket_requires_token_and_only_own_meeting(client):
    room, other = guest_room(client), guest_room(client)
    a = started(client, room)
    b = _join(client, "bob", other["id"])
    g = guest_join(client, room["guest_token"]).json()
    client.cookies.clear()
    with client.websocket_connect("/api/v1/ws?guest=1") as ws:
        ws.send_json({"type": "auth", "guest_token": "wrong-token"})
        with pytest.raises(Exception):
            ws.receive_json()
    with client.websocket_connect("/api/v1/ws?guest=1") as ws:
        ws.send_json({"type": "auth", "guest_token": g["guest_token"]})
        assert ws.receive_json()["type"] == "authed"
        ws.send_json({"type": "subscribe", "meeting_id": b["meeting_id"]})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "subscribe", "meeting_id": a["meeting_id"]})
        assert ws.receive_json()["type"] == "subscribed"


def test_guest_speech_is_attributed_with_the_guest_mark(client):
    room = guest_room(client)
    a = started(client, room)
    g = guest_join(client, room["guest_token"]).json()
    _feed_and_consume(client, [_segment(a["meeting_id"], g["identity"], "Слово гостя")])
    login(client, "alice")
    segs = client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").json()["segments"]
    assert segs[0]["display_name"] == "Иван Иванов (гость)" and segs[0]["guest_id"] == g["guest_id"] and segs[0]["user_id"] is None
    assert "Иван Иванов (гость): Слово гостя" in client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript.txt").text
    # чужая identity вида g-<uuid> не из состава встречи не превращается в гостя
    _feed_and_consume(client, [_segment(a["meeting_id"], "g-" + "0" * 32, "Подделка")])
    segs = client.get(f"/api/v1/meetings/{a['meeting_id']}/transcript").json()["segments"]
    assert [s for s in segs if s["text"] == "Подделка"][0]["guest_id"] is None


def test_guest_leave_closes_the_session(client):
    room = guest_room(client)
    a = started(client, room)
    g = guest_join(client, room["guest_token"]).json()
    client.cookies.clear()
    assert client.post("/api/v1/guest/session/leave", headers=ghead(g)).status_code == 204
    assert client.get(f"/api/v1/meetings/{a['meeting_id']}/chat", headers=ghead(g)).status_code == 401


# ============================================================================================== чат
def test_chat_is_bound_to_the_meeting_keeps_links_and_exact_text_and_history(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    _join(client, "bob", room["id"])
    mid = a["meeting_id"]
    login(client, "alice")
    text = "Заведите задачу JIRA-4521: https://wiki.corp.test/x?a=1&b=2 сервер srv-db-07 (10.20.30.40)\nвторая строка"
    r = client.post(f"/api/v1/meetings/{mid}/chat", json={"text": text})
    assert r.status_code == 201 and r.json()["text"] == text and r.json()["author_name"] == "Alice A" and r.json()["author_type"] == "user"
    assert client.post(f"/api/v1/meetings/{mid}/chat", json={"text": "   "}).status_code == 422
    assert client.post(f"/api/v1/meetings/{mid}/chat", json={"text": "x" * 4001}).status_code == 422
    assert client.post(f"/api/v1/meetings/{mid}/chat", json={"text": "a\x00b‮c"}).json()["text"] == "abc"
    login(client, "bob")
    msgs = client.get(f"/api/v1/meetings/{mid}/chat").json()["messages"]
    assert [m["text"] for m in msgs][0] == text and len(msgs) == 2
    # чат другой встречи не смешивается
    other = make_room(client)
    c = _join(client, "alice", other["id"])
    assert client.get(f"/api/v1/meetings/{c['meeting_id']}/chat").json()["messages"] == []
    # человек вне встречи чат не видит и не пишет
    login(client, "carol")
    assert client.get(f"/api/v1/meetings/{mid}/chat").status_code == 404
    assert client.post(f"/api/v1/meetings/{mid}/chat", json={"text": "привет"}).status_code == 404


def test_chat_paging_and_history_after_the_meeting_ends(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    for i in range(7):
        client.post(f"/api/v1/meetings/{mid}/chat", json={"text": f"сообщение {i}"})
    last = client.get(f"/api/v1/meetings/{mid}/chat", params={"limit": 3}).json()
    assert [m["text"] for m in last["messages"]] == ["сообщение 4", "сообщение 5", "сообщение 6"] and last["has_more"]
    earlier = client.get(f"/api/v1/meetings/{mid}/chat", params={"limit": 3, "before_id": last["messages"][0]["id"]}).json()
    assert [m["text"] for m in earlier["messages"]] == ["сообщение 1", "сообщение 2", "сообщение 3"]
    newer = client.get(f"/api/v1/meetings/{mid}/chat", params={"after_id": last["messages"][1]["id"]}).json()
    assert [m["text"] for m in newer["messages"]] == ["сообщение 6"]
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    # история доступна (участник на странице встречи), писать в завершённую встречу нельзя
    assert len(client.get(f"/api/v1/meetings/{mid}/chat", params={"limit": 100}).json()["messages"]) == 7
    assert client.post(f"/api/v1/meetings/{mid}/chat", json={"text": "поздно"}).status_code == 409
    txt = client.get(f"/api/v1/meetings/{mid}/chat.txt")
    assert txt.status_code == 200 and "Alice A: сообщение 0" in txt.text
    assert client.get(f"/api/v1/meetings/{mid}", headers={}).json()["chat_messages"] == 7


def test_chat_events_reach_websocket_subscribers_and_guests_can_write(client):
    room = guest_room(client)
    a = started(client, room)
    mid = a["meeting_id"]
    g = guest_join(client, room["guest_token"]).json()
    login(client, "alice")
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": mid})
        assert ws.receive_json()["type"] == "subscribed"
        r = client.post(f"/api/v1/meetings/{mid}/chat", json={"text": "из гостевой вкладки"}, headers=ghead(g))
        assert r.status_code == 201 and r.json()["author_type"] == "guest" and r.json()["author_name"] == "Иван Иванов (гость)"
        ev = ws.receive_json()
        while ev["type"] != "chat_message":
            ev = ws.receive_json()
        assert ev["message"]["text"] == "из гостевой вкладки" and ev["message"]["author_type"] == "guest"


def test_chat_is_rate_limited(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    codes = [client.post(f"/api/v1/meetings/{a['meeting_id']}/chat", json={"text": f"m{i}"}).status_code for i in range(20)]
    assert codes.count(201) == 15 and codes[-1] == 429


# ============================================================================================ доска
def test_whiteboard_snapshot_patches_and_late_joiner(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    empty = client.get(f"/api/v1/meetings/{mid}/whiteboard").json()
    assert empty["xml"] is None and empty["seq"] == 0 and empty["used"] is False

    p1 = client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": [{"op": "add"}], "checksum": "abc", "client_id": "c1"}).json()["seq"]
    p2 = client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 1}, "client_id": "c1"}).json()["seq"]
    assert (p1, p2) == (1, 2)
    saved = client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 2}).json()
    assert saved["saved"] and saved["shapes"] == 3 and saved["used"] is True
    p3 = client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 3}, "client_id": "c1"}).json()["seq"]
    assert p3 == 3
    # опоздавший получает снимок + только более новые патчи
    login(client, "bob")
    _join(client, "bob", room["id"])
    got = client.get(f"/api/v1/meetings/{mid}/whiteboard").json()
    assert got["xml"] == BOARD and got["seq"] == 2 and [p["seq"] for p in got["patches"]] == [3]
    # устаревший снимок не затирает свежий
    stale = client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD.replace("GW-1", "СТАРОЕ"), "seq": 1}).json()
    assert stale["saved"] is False and "GW-1" in client.get(f"/api/v1/meetings/{mid}/whiteboard").json()["xml"]


def test_whiteboard_rejects_unsafe_or_foreign_content_and_other_meetings(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    evil = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><mxfile><diagram>&a;</diagram></mxfile>'
    for bad in (evil, "<html/>", "не xml", "", "<mxfile>" + "x" * 3_100_000 + "</mxfile>", None, 5):
        assert client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": bad, "seq": 0}).status_code == 422
    assert client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": -1}).status_code == 422
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": "строка"}).status_code == 422
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": "y" * 500_000}}).status_code == 422
    login(client, "carol")  # вне встречи
    assert client.get(f"/api/v1/meetings/{mid}/whiteboard").status_code == 404
    assert client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 0}).status_code == 404


def test_whiteboard_patches_are_relayed_live_and_board_is_downloadable_from_history(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": mid})
        assert ws.receive_json()["type"] == "subscribed"
        client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"k": 1}, "checksum": "c0ffee", "client_id": "tab-1"})
        ev = ws.receive_json()
        while ev["type"] != "whiteboard_patch":
            ev = ws.receive_json()
        assert ev["seq"] == 1 and ev["patch"] == {"k": 1} and ev["checksum"] == "c0ffee" and ev["from"] == "tab-1" and ev["by"] == "Alice A"
    assert client.get(f"/api/v1/meetings/{mid}/whiteboard.drawio").status_code == 404  # доска не использовалась
    client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 1})
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    dl = client.get(f"/api/v1/meetings/{mid}/whiteboard.drawio")
    assert dl.status_code == 200 and dl.text == BOARD and ".drawio" in dl.headers["content-disposition"]
    m = client.get(f"/api/v1/meetings/{mid}").json()
    assert m["whiteboard_shapes"] == 3
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"k": 2}}).status_code == 409  # встреча завершена


def test_whiteboard_description_extracts_labels_and_connections():
    from app.services import whiteboard as wb

    d = wb.describe(BOARD)
    assert d.shapes == 3
    assert "Страница «Схема сети»" in d.text and "блок: Шлюз GW-1" in d.text and "база данных".lower() in d.text.lower()
    assert "связь: «Шлюз GW-1» → «База данных» (подпись: TCP 5432)" in d.text
    import base64
    import urllib.parse
    import zlib

    inner = BOARD[BOARD.index("<mxGraphModel>"):BOARD.index("</diagram>")]
    comp = zlib.compressobj(wbits=-15)
    packed = base64.b64encode(comp.compress(urllib.parse.quote(inner, safe="").encode()) + comp.flush()).decode()
    compressed = f'<mxfile><diagram name="Сжатая">{packed}</diagram></mxfile>'
    assert wb.describe(compressed).shapes == 3 and "Шлюз GW-1" in wb.describe(compressed).text


# ===================================================================== материалы для протокола (LLM)
def test_protocol_gets_chat_and_board_as_separate_sources(tmp_path, directory):
    seen: list[dict] = []

    def llm(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return LLM_OK(req)

    from .test_admin_features import anon_ok

    with running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm)}) as c:
        configure(c)
        put_settings(c, "anonymizer", enabled=False)
        room = make_room(c)
        a = _join(c, "alice", room["id"])
        mid = a["meeting_id"]
        _feed_and_consume(c, [_segment(mid, a["identity"], "Давайте переведём базу на новый сервер")])
        login(c, "alice")
        c.post(f"/api/v1/meetings/{mid}/chat", json={"text": "Сервер: srv-db-07, IP 10.20.30.40, задача JIRA-4521, https://wiki.corp.test/db"})
        c.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 0})
        assert c.post(f"/api/v1/meetings/{mid}/end").status_code == 204
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "Сделай протокол"})
        assert r.status_code == 202, r.text
        _drain(c)
        sent = json.dumps(seen[-1], ensure_ascii=False)
        for must in ("ЧАТ ВСТРЕЧИ", "СТЕНОГРАММА", "СХЕМА НА ОБЩЕЙ ДОСКЕ", "srv-db-07", "10.20.30.40", "JIRA-4521", "https://wiki.corp.test/db",
                     "Шлюз GW-1", "TCP 5432", "переведём базу"):
            assert must in sent, must
        proto = c.get(f"/api/v1/meetings/{mid}/protocols/{r.json()['protocol_id']}").json()
        assert proto["status"] == "ready"
        # пустой чат и доска → текст ровно как раньше (без заголовков источников)
        room2 = make_room(c)
        b = _join(c, "alice", room2["id"])
        _feed_and_consume(c, [_segment(b["meeting_id"], b["identity"], "Только речь")])
        login(c, "alice")
        c.post(f"/api/v1/meetings/{b['meeting_id']}/end")
        c.post(f"/api/v1/meetings/{b['meeting_id']}/protocols", json={"kind": "protocol", "instruction": "x"})
        _drain(c)
        plain = json.dumps(seen[-1], ensure_ascii=False)
        assert "ЧАТ ВСТРЕЧИ" not in plain and "Стенограмма встречи" in plain


def test_protocol_possible_from_chat_alone_and_chat_is_deleted_with_meeting(tmp_path, directory):
    from .test_admin_features import anon_ok

    seen: list[dict] = []

    def llm(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return LLM_OK(req)

    with running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm)}) as c:
        configure(c)
        room = make_room(c)
        a = _join(c, "alice", room["id"])
        mid = a["meeting_id"]
        login(c, "alice")
        c.post(f"/api/v1/meetings/{mid}/end")
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x"}).status_code == 202
        _drain(c)
        assert c.get(f"/api/v1/meetings/{mid}/protocols").json()[0]["status"] == "failed"  # ни речи, ни чата
        room2 = make_room(c)
        b = _join(c, "alice", room2["id"])
        login(c, "alice")
        c.post(f"/api/v1/meetings/{b['meeting_id']}/chat", json={"text": "Итог: срок 15 числа"})
        c.post(f"/api/v1/meetings/{b['meeting_id']}/end")
        pid = c.post(f"/api/v1/meetings/{b['meeting_id']}/protocols", json={"kind": "summary", "instruction": "x"}).json()["protocol_id"]
        _drain(c)
        assert c.get(f"/api/v1/meetings/{b['meeting_id']}/protocols/{pid}").json()["status"] == "ready"
        login(c, "root")
        assert c.delete(f"/api/v1/meetings/{b['meeting_id']}").status_code == 204
        login(c, "root")
        assert c.get(f"/api/v1/meetings/{b['meeting_id']}/chat").status_code == 404


# ============================================================ выгрузка рядом со стенограммой
def test_chat_and_board_are_exported_next_to_the_transcript_and_guests_are_in_the_header(tmp_path, directory):
    from .test_admin_features import _drain

    with running_app(make_settings(tmp_path), directory) as c:
        put_settings(c, "storage", enabled=True, mode="local", local_path=str(tmp_path / "out"))
        room = guest_room(c, name="Совещание")
        a = _join(c, "alice", room["id"])
        mid = a["meeting_id"]
        g = guest_join(c, room["guest_token"]).json()
        c.headers.pop("X-CSRF-Token", None)
        _feed_and_consume(c, [_segment(mid, a["identity"], "Начинаем"), _segment(mid, g["identity"], "Слышно хорошо")])
        login(c, "alice")
        c.post(f"/api/v1/meetings/{mid}/chat", json={"text": "https://wiki.corp.test/plan срок 15.10"})
        c.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 0})
        assert c.post(f"/api/v1/meetings/{mid}/end").status_code == 204
        _drain(c)
        proto = list((tmp_path / "out").rglob("protocol.txt"))
        assert len(proto) == 1
        folder = proto[0].parent
        text = proto[0].read_text(encoding="utf-8")
        assert "Участвовали: Alice A, Иван Иванов (гость)" in text and "Иван Иванов (гость): Слышно хорошо" in text
        assert "https://wiki.corp.test/plan срок 15.10" in (folder / "chat.txt").read_text(encoding="utf-8")
        assert (folder / "whiteboard.drawio").read_text(encoding="utf-8") == BOARD


def test_meeting_without_chat_and_board_exports_only_the_transcript(tmp_path, directory):
    from .test_admin_features import _drain

    with running_app(make_settings(tmp_path), directory) as c:
        put_settings(c, "storage", enabled=True, mode="local", local_path=str(tmp_path / "out"))
        room = make_room(c)
        a = _join(c, "alice", room["id"])
        _feed_and_consume(c, [_segment(a["meeting_id"], a["identity"], "Только речь")])
        login(c, "alice")
        c.post(f"/api/v1/meetings/{a['meeting_id']}/end")
        _drain(c)
        assert len(list((tmp_path / "out").rglob("protocol.txt"))) == 1
        assert not list((tmp_path / "out").rglob("chat.txt")) and not list((tmp_path / "out").rglob("whiteboard.drawio"))
