"""Общая сцена видеозаписи: токен страницы-рекордера (Egress), состояние для неё, окно «над доской работают», закрытая доска, доступ без сеанса."""
from __future__ import annotations

import time
import uuid

from app.config import Settings
from app.services import rec_scene as rs

from .conftest import login, make_room
from .test_collab_guests import BOARD
from .test_transcripts import _join


def S(**kw) -> Settings:
    return Settings(**{"app_master_key": "k" * 40, **kw})


def test_token_roundtrip_expiry_and_tampering():
    s, mid = S(), uuid.uuid4()
    t = rs.make_token(s, mid)
    assert rs.check_token(s, t) == mid
    assert rs.check_token(s, t, now=time.time() + rs.TOKEN_TTL_S + 5) is None, "срок вышел"
    head, exp, sig = t.split(".")
    assert rs.check_token(s, f"{uuid.uuid4().hex}.{exp}.{sig}") is None, "подпись привязана к встрече"
    assert rs.check_token(s, f"{head}.{int(exp) + 100000}.{sig}") is None, "срок не продлить"
    assert rs.check_token(S(app_master_key="z" * 40), t) is None, "другой ключ сервера"
    for junk in ("", "a.b", "x.y.z", "😀" * 5):
        assert rs.check_token(s, junk) is None


def test_board_counts_as_active_only_for_a_short_window_after_an_edit():
    st = {"items": [], "at": None}
    recent = rs.scene_payload(stage=st, board_age=3.0, board={"seq": 4}, ended=False)
    assert recent["board"]["active"] is True
    old = rs.scene_payload(stage=st, board_age=rs.BOARD_ACTIVE_S + 1, board={"seq": 4}, ended=False)
    assert old["board"]["active"] is False, "после паузы запись возвращается к участникам"
    never = rs.scene_payload(stage=st, board_age=None, board={}, ended=False)
    assert never["board"]["active"] is False
    assert rs.scene_payload(stage=st, board_age=1.0, board={}, ended=True)["board"]["active"] is False, "встреча завершена — доски в записи уже нет"


def test_recorder_state_needs_a_valid_token_and_does_not_reveal_why(client):
    for rt in ("bad", rs.make_token(S(app_master_key="other" * 10), uuid.uuid4())):
        r = client.get("/api/v1/rec/state", params={"rt": rt})
        assert r.status_code == 404 and r.json()["detail"] == "Не найдено"
    r = client.get("/api/v1/rec/state", params={"rt": rs.make_token(client.app_obj.state.settings, uuid.uuid4())})
    assert r.status_code == 404, "подпись верна, но такой встречи нет"


def test_recorder_state_follows_board_edits_and_the_leaders_stage_without_a_session(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    s = client.app_obj.state.settings
    rt = rs.make_token(s, uuid.UUID(mid))
    client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 1}, "client_id": "c1"})
    client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 1})
    client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 2}, "client_id": "c1"})
    client.cookies.clear()                                                         # у рекордера нет сеанса
    st = client.get("/api/v1/rec/state", params={"rt": rt})
    assert st.status_code == 200 and st.headers["cache-control"] == "no-store"
    d = st.json()
    assert d["board"]["active"] is True and d["board"]["xml"] == BOARD and d["board"]["seq"] == 1 and [p["seq"] for p in d["board"]["patches"]] == [2]
    assert d["stage"]["items"] == [] and d["ended"] is False
    # без правок в течение окна доска перестаёт быть «активной», схема остаётся доступной
    async def age():
        await client.app_obj.state.redis.set(rs.board_touch_key(uuid.UUID(mid)), f"{time.time() - rs.BOARD_ACTIVE_S - 5:.3f}")
    client.portal.call(age)
    assert client.get("/api/v1/rec/state", params={"rt": rt}).json()["board"]["active"] is False


def test_private_board_is_not_recorded(client):
    room = make_room(client)
    assert client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"board_access": "private"}).status_code == 200
    a = _join(client, "root", room["id"])
    mid = a["meeting_id"]
    client.post(f"/api/v1/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 1}, "client_id": "c1"})
    client.cookies.clear()
    d = client.get("/api/v1/rec/state", params={"rt": rs.make_token(client.app_obj.state.settings, uuid.UUID(mid))}).json()
    assert d["board"]["active"] is False and d["board"]["xml"] is None and d["board"]["patches"] == []
