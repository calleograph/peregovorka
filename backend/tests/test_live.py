"""Трансляции: настройки и права владельца, запуск/остановка эфира (Egress подменён), подписанные ссылки HLS, пароль зрителя, отзыв ссылок, сверка, уборка диска.
Сам Egress и nginx в этих тестах не участвуют (настоящие — в tests/live/egress/run_live.py): проверяется логика приложения вокруг них."""
from __future__ import annotations

import base64
import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from livekit.protocol import egress as eg
from sqlalchemy import select

from app.models import Broadcast, Meeting, Recording, Room
from app.services import live as live_mod
from app.services.live import build_live_request, hls_path, hls_secret, sign

from .conftest import login, make_room, put_settings
from .test_mail import LEADERS
from .test_transcripts import _join
from .test_video_recording import FakeEgress, call, finish_event


@pytest.fixture
def lv(client, monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_DIR", str(tmp_path))
    fake = FakeEgress()
    app = client.app_obj
    app.state.video.api = fake
    app.state.live.api = fake
    present = {"set": {"u-1"}}

    async def fake_present(settings, room_name):
        return present["set"]

    monkeypatch.setattr(live_mod, "list_present_identities", fake_present)
    monkeypatch.setattr("app.services.video_rec.list_present_identities", fake_present)
    monkeypatch.setattr("app.services.video_rec.readable_duration", lambda p: 60)
    app.state.live.prepare()
    client.fake, client.present, client.dir = fake, present, tmp_path
    put_settings(client, "live", enabled=True, min_free_gb=1)
    return client


def room_live(client, **over):
    """Комната с руководителями; трансляция включена владельцем (carol — руководитель)."""
    room = make_room(client, moderators=LEADERS, **({"room_type": "broadcast"} | over))
    return room


def patch(client, room, user="carol", **fields):
    login(client, user)
    return client.patch(f"/api/v1/rooms/{room['id']}/live", json=fields)


def start(client, mid, user="carol", enabled=True):
    login(client, user)
    return client.post(f"/api/v1/meetings/{mid}/live", json={"enabled": enabled})


def broadcasts(client, mid):
    async def q():
        async with client.app_obj.state.session_maker() as db:
            return list((await db.execute(select(Broadcast).where(Broadcast.meeting_id == uuid.UUID(mid)).order_by(Broadcast.started_at))).scalars())
    return client.portal.call(q)


def get_room(client, rid):
    async def q():
        async with client.app_obj.state.session_maker() as db:
            r = await db.get(Room, uuid.UUID(rid))
            await db.refresh(r)
            return r
    return client.portal.call(q)


def kinds(req):
    return [k for o in req.outputs for k in ("file", "segments", "stream") if o.HasField(k)]


def reconcile(client):
    return call(client, lambda db: client.app_obj.state.live.reconcile(db))


# --------------------------------------------------------------------------------------------- запрос к Egress и подписи
def test_request_has_mp4_hls_and_rtmp_without_urls_in_one_process():
    req = build_live_request("room-x", "720p", mp4_path="/out/a.mp4", hls_prefix="/live/b/abc", urls=[])
    got = kinds(req)
    assert got == ["file", "segments", "stream"], "MP4 + HLS + RTMP — один запрос, один браузер"
    seg = req.outputs[1].segments
    assert seg.filename_prefix == "/live/b/abc/seg" and seg.live_playlist_name == "/live/b/abc/index.m3u8" and seg.segment_duration == 2
    assert req.outputs[2].stream.protocol == eg.StreamProtocol.RTMP and list(req.outputs[2].stream.urls) == []
    assert req.advanced.key_frame_interval == 2.0 and (req.advanced.width, req.advanced.height) == (1280, 720)
    assert req.advanced.video_bitrate == 3000, "не ниже минимума YouTube для 720p"
    only = build_live_request("r", "1080p", mp4_path=None, hls_prefix=None, urls=["rtmp://a/b/c"])
    assert kinds(only) == ["stream"] and list(only.outputs[0].stream.urls) == ["rtmp://a/b/c"] and only.advanced.width == 1920 and only.advanced.video_bitrate == 5000


def test_signature_matches_the_nginx_secure_link_formula_and_is_shared_inside_a_time_bucket():
    exp = 1_900_000_300
    want = base64.urlsafe_b64encode(hashlib.md5(f"{exp}abc123 SECRET".encode()).digest()).decode().rstrip("=")
    assert sign("SECRET", "abc123", exp) == want and len(want) == 22
    p1, e1 = hls_path("S", "a" * 24, 900, now=1_000_000_000, bucket_s=300)
    p2, e2 = hls_path("S", "a" * 24, 900, now=1_000_000_100, bucket_s=300)
    p3, _ = hls_path("S", "a" * 24, 900, now=1_000_000_400, bucket_s=300)
    assert p1 == p2 and e1 == e2 and p3 != p1, "зрители одного окна получают ОДИН адрес — CDN кэширует сегменты"
    assert e1 - 1_000_000_000 >= 900 and p1.startswith(f"/hls/{'a' * 24}/{e1}/") and p1.endswith("/index.m3u8")
    assert sign("S", "x", 1) != sign("T", "x", 1) and sign("S", "x", 1) != sign("S", "y", 1) and sign("S", "x", 1) != sign("S", "x", 2)


# --------------------------------------------------------------------------------------------- настройки и права
def test_broadcast_room_has_live_on_and_links_regular_room_has_it_off(lv):
    b, r = room_live(lv), make_room(lv, moderators=LEADERS)
    assert get_room(lv, b["id"]).live_enabled and get_room(lv, b["id"]).live_token and get_room(lv, b["id"]).live_view_access != get_room(lv, b["id"]).live_stream_access
    assert not get_room(lv, r["id"]).live_enabled and get_room(lv, r["id"]).live_token is None, "обычная комната: трансляция по желанию владельца"
    login(lv, "carol")
    assert lv.get(f"/api/v1/rooms/{r['id']}/live").json()["enabled"] is False
    assert patch(lv, r, live_enabled=True).json()["view_path"].startswith("/live/"), "в обычной комнате владелец включает трансляцию сам"


def test_only_room_leaders_manage_live_and_other_rooms_are_not_revealed(lv):
    room, other = room_live(lv), room_live(lv)
    assert patch(lv, room, user="bob", live_title="X").status_code == 404, "рядовой участник"
    assert patch(lv, room, user="carol", live_title="Квартальный отчёт").json()["title"] == "Квартальный отчёт"
    login(lv, "carol")
    assert lv.get(f"/api/v1/rooms/{other['id']}/live").status_code == 200, "carol руководит обеими (общая группа)"
    solo = make_room(lv, name="Чужая")
    login(lv, "carol")
    assert lv.get(f"/api/v1/rooms/{solo['id']}/live").status_code == 404, "комната без прав: не раскрывается"
    assert patch(lv, room, user="root", live_start="auto").json()["start"] == "auto", "администратор — всегда"


def test_e2ee_and_encrypted_storage_refuse_live_and_quality_is_fixed_while_live(lv):
    e2ee = make_room(lv, moderators=LEADERS, e2ee_mode="full")
    assert patch(lv, e2ee, live_enabled=True).status_code == 409
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    assert start(lv, mid).status_code == 200
    r = patch(lv, room, live_quality="1080p")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "live_running"
    assert patch(lv, room, live_title="Новое название").status_code == 200, "название можно менять и во время эфира"


# --------------------------------------------------------------------------------------------- запуск и остановка
def test_start_explains_each_refusal_and_does_not_touch_the_conference(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    put_settings(lv, "live", enabled=False)
    r = start(lv, mid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "live_disabled"
    put_settings(lv, "live", enabled=True, min_free_gb=1)
    patch(lv, room, live_enabled=False)
    assert start(lv, mid).json()["detail"]["code"] == "live_forbidden"
    patch(lv, room, live_enabled=True, live_own_hls=False)
    r = start(lv, mid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_outputs", "нечего вещать: нет ни HLS, ни площадок"
    patch(lv, room, live_own_hls=True)
    lv.present["set"] = set()
    assert start(lv, mid).json()["detail"]["code"] == "room_empty"
    lv.present["set"] = {"u-1"}
    lv.fake.fail_start = True
    r = start(lv, mid)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "egress_unavailable" and broadcasts(lv, mid) == []
    assert (lv.dir / "acc").is_dir() and (lv.dir / "b").is_dir(), "каталоги тома подготовлены"


def test_start_creates_one_egress_with_all_outputs_links_access_and_is_idempotent(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    assert start(lv, mid).status_code == 200 and start(lv, mid).status_code == 200
    assert len(lv.fake.started) == 1, "повторное нажатие — не второй эфир"
    req = lv.fake.started[0]
    assert kinds(req) == ["file", "segments", "stream"]
    (b,) = broadcasts(lv, mid)
    assert b.status == "starting" and b.egress_id == "EG_test1" and b.hls_dir and b.hls_dir in req.outputs[1].segments.filename_prefix
    rows = call(lv, lambda db: db.execute(select(Recording).where(Recording.meeting_id == uuid.UUID(mid))))
    rec = list(rows.scalars())
    assert len(rec) == 1 and rec[0].kind == "mix_video" and rec[0].egress_id == "EG_test1", "MP4 входит в тот же процесс и попадает в историю"
    r = get_room(lv, room["id"])
    for acc in (r.live_view_access, r.live_stream_access):
        link = lv.dir / "acc" / acc
        assert link.is_symlink() and os.readlink(link) == f"../b/{b.hls_dir}"
    assert (lv.dir / "nginx" / "secret.conf").read_text() == f'set $hls_secret "{hls_secret(lv.app_obj.state.settings)}";\n'
    _join(lv, "bob", room["id"])
    st = lv.get(f"/api/v1/meetings/{mid}/live").json()
    assert st["live"] and "links" not in st and st["can_control"] is False, "рядовым участникам ссылки и площадки не показываются"


def test_second_broadcast_is_refused_by_the_server_limit_while_conferences_go_on(lv):
    a, b = room_live(lv), room_live(lv)
    ma = _join(lv, "alice", a["id"])["meeting_id"]
    mb = _join(lv, "alice", b["id"])["meeting_id"]
    assert start(lv, ma).status_code == 200
    r = start(lv, mb)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "live_busy" and "Обычные конференции" in r.json()["detail"]["message"]
    put_settings(lv, "live", max_concurrent=2)
    assert start(lv, mb).status_code == 200


def test_stop_ends_with_egress_event_and_recording_is_finalized_by_the_video_module(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    assert start(lv, mid, enabled=False).status_code == 200 and lv.fake.stopped == ["EG_test1"]
    assert broadcasts(lv, mid)[0].status == "ending"
    ev = finish_event("EG_test1", duration_s=60)

    async def go(db):
        await lv.app_obj.state.video.on_egress_event(db, ev)
        await lv.app_obj.state.live.on_egress_event(db, ev)
    call(lv, go)
    b = broadcasts(lv, mid)[0]
    assert b.status == "ended" and b.ended_at is not None
    rec = list(call(lv, lambda db: db.execute(select(Recording).where(Recording.meeting_id == uuid.UUID(mid)))).scalars())[0]
    assert rec.status in ("ready", "failed"), "запись закрыта тем же событием (файла на диске в тесте нет)"
    assert start(lv, mid).status_code == 200 and len(lv.fake.started) == 2, "после остановки эфир можно начать снова"


def test_live_recording_cannot_be_stopped_alone_and_video_does_not_run_twice(lv):
    room = room_live(lv)
    put_settings(lv, "video", enabled=True, min_free_gb=1)
    login(lv, "carol")
    assert lv.patch(f"/api/v1/rooms/{room['id']}/manage", json={"video_mode": "manual"}).status_code == 200
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    login(lv, "carol")
    assert lv.post(f"/api/v1/meetings/{mid}/video-recording", json={"enabled": True}).status_code == 200
    assert len(lv.fake.started) == 1
    assert start(lv, mid).status_code == 200
    assert lv.fake.stopped == ["EG_test1"] and len(lv.fake.started) == 2, "отдельная запись закрыта: MP4 теперь часть эфира, второй браузер не нужен"
    login(lv, "carol")
    r = lv.post(f"/api/v1/meetings/{mid}/video-recording", json={"enabled": False})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "live_active"
    r = lv.post(f"/api/v1/meetings/{mid}/video-recording", json={"enabled": True})
    assert r.status_code == 200 and len(lv.fake.started) == 2, "запись уже идёт в составе эфира — второй процесс не запускается"


def test_auto_mode_starts_on_first_join_unless_the_leader_stopped_it(lv):
    room = room_live(lv)
    patch(lv, room, live_start="auto")
    mid = _join(lv, "alice", room["id"])["meeting_id"]

    async def joined(db):                                  # первое подключение к LiveKit (webhook participant_joined) вызывает этот же хук
        await lv.app_obj.state.live.on_join(db, await db.get(Meeting, uuid.UUID(mid)))
    call(lv, joined)
    assert len(lv.fake.started) == 1 and broadcasts(lv, mid)[0].status == "starting"
    call(lv, joined)
    assert len(lv.fake.started) == 1, "второй вошедший новый эфир не запускает"
    start(lv, mid, enabled=False)
    call(lv, joined)
    assert len(lv.fake.started) == 1, "ручная остановка не перебивается автозапуском"


# --------------------------------------------------------------------------------------------- сверка и аварии
def test_reconcile_marks_live_when_the_playlist_appears_and_never_leaves_a_dead_broadcast(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    (b,) = broadcasts(lv, mid)
    lv.fake.infos["EG_test1"] = eg.EgressInfo(egress_id="EG_test1", status=eg.EgressStatus.EGRESS_ACTIVE)
    reconcile(lv)
    assert broadcasts(lv, mid)[0].status == "starting", "плейлиста ещё нет — зрителям рано"
    d = lv.dir / "b" / b.hls_dir
    d.mkdir(parents=True)
    (d / "index.m3u8").write_text("#EXTM3U\n")
    reconcile(lv)
    assert broadcasts(lv, mid)[0].status == "live" and broadcasts(lv, mid)[0].live_at is not None
    lv.fake.infos["EG_test1"] = eg.EgressInfo(egress_id="EG_test1", status=eg.EgressStatus.EGRESS_FAILED, error="rtmp: connection refused")
    reconcile(lv)
    b = broadcasts(lv, mid)[0]
    assert b.status == "failed" and "connection refused" in b.error and b.ended_at is not None, "эфир не остаётся «идущим» бесконечно"


def test_dead_egress_service_is_detected_by_the_missing_recorder_participant(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)

    async def age(db):
        b = (await db.execute(select(Broadcast))).scalars().first()
        b.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()
    call(lv, age)
    lv.fake.infos["EG_test1"] = eg.EgressInfo(egress_id="EG_test1", status=eg.EgressStatus.EGRESS_ACTIVE)
    lv.fake.alive = False
    reconcile(lv)
    b = broadcasts(lv, mid)[0]
    assert b.status == "failed" and "перестала отвечать" in b.error


def test_empty_room_ends_the_broadcast_after_the_grace_and_meeting_end_stops_it(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    lv.fake.infos["EG_test1"] = eg.EgressInfo(egress_id="EG_test1", status=eg.EgressStatus.EGRESS_ACTIVE)

    async def empty(db):
        m = await db.get(Meeting, uuid.UUID(mid))
        m.empty_since = datetime.now(timezone.utc) - timedelta(seconds=live_mod.EMPTY_GRACE_S + 30)
        await db.commit()
    call(lv, empty)
    reconcile(lv)
    assert lv.fake.stopped == ["EG_test1"] and broadcasts(lv, mid)[0].status == "ending", "выступающие ушли — эфир заканчивается сам"
    mid2 = _join(lv, "alice", room_live(lv)["id"])["meeting_id"]
    put_settings(lv, "live", max_concurrent=2)
    start(lv, mid2)
    login(lv, "root")
    assert lv.post(f"/api/v1/meetings/{mid2}/end").status_code in (200, 204)
    assert "EG_test2" in lv.fake.stopped, "завершение встречи останавливает эфир"


def test_janitor_removes_old_segments_of_a_running_broadcast_and_ended_directories(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    (b,) = broadcasts(lv, mid)
    d = lv.dir / "b" / b.hls_dir
    d.mkdir(parents=True)
    old, new = d / "seg_00001.ts", d / "seg_00002.ts"
    old.write_bytes(b"x"), new.write_bytes(b"y"), (d / "index.m3u8").write_text("#EXTM3U\n")
    t = time.time() - 3600
    os.utime(old, (t, t))
    lv.fake.infos["EG_test1"] = eg.EgressInfo(egress_id="EG_test1", status=eg.EgressStatus.EGRESS_ACTIVE)
    reconcile(lv)
    assert not old.exists() and new.exists() and (d / "index.m3u8").exists(), "диск не растёт на всё время эфира"
    lv.fake.infos["EG_test1"] = eg.EgressInfo(egress_id="EG_test1", status=eg.EgressStatus.EGRESS_COMPLETE)

    async def finish(db):
        await lv.app_obj.state.redis.delete("live:sweep")
        bb = (await db.execute(select(Broadcast))).scalars().first()
        bb.status, bb.ended_at = "ended", datetime.now(timezone.utc) - timedelta(hours=2)
        await db.commit()
    call(lv, finish)
    reconcile(lv)
    assert not d.exists() and not (lv.dir / "acc" / get_room(lv, room["id"]).live_view_access).exists(), "каталог завершённого эфира и висячая ссылка удалены"


# --------------------------------------------------------------------------------------------- страница и ссылки зрителя
def viewer(client, token):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    return client.get(f"/api/v1/live/{token}")


def test_viewer_page_needs_no_login_and_unknown_or_disabled_links_are_one_404(lv):
    room = room_live(lv, name="Вебинар")
    token = get_room(lv, room["id"]).live_token
    r = viewer(lv, token)
    assert r.status_code == 200 and r.json()["title"] == "Вебинар" and r.json()["status"] == "waiting" and r.json()["password_required"] is False
    assert viewer(lv, "no-such-token").status_code == 404
    patch(lv, room, live_enabled=False)
    assert viewer(lv, token).status_code == 404, "трансляция выключена — ссылка не раскрывает, что комната есть"
    body = r.json()
    assert "slug" not in body and "id" not in body and "room" not in body, "страница зрителя ничего не знает о внутренностях комнаты"


def test_status_goes_waiting_starting_live_ended(lv):
    room = room_live(lv)
    token = get_room(lv, room["id"]).live_token
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    assert viewer(lv, token).json()["status"] == "starting"
    (b,) = broadcasts(lv, mid)
    (lv.dir / "b" / b.hls_dir).mkdir(parents=True)
    (lv.dir / "b" / b.hls_dir / "index.m3u8").write_text("#EXTM3U\n")
    assert viewer(lv, token).json()["status"] == "live"
    lv.fake.infos["EG_test1"] = eg.EgressInfo(egress_id="EG_test1", status=eg.EgressStatus.EGRESS_COMPLETE)
    reconcile(lv)
    r = viewer(lv, token).json()
    assert r["status"] == "ended" and r["ended_at"]


def access(client, token, **body):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    return client.post(f"/api/v1/live/{token}/access", json=body)


def test_access_without_password_returns_a_signed_playlist_path(lv):
    room = room_live(lv)
    rm = get_room(lv, room["id"])
    r = access(lv, rm.live_token)
    assert r.status_code == 200
    j = r.json()
    path = j["path"]
    _, hls, acc, exp, sig, name = path.split("/")
    assert acc == rm.live_view_access and name == "index.m3u8" and sig == sign(hls_secret(lv.app_obj.state.settings), acc, int(exp)) and int(exp) > time.time() + 800
    assert "pass" not in j and j["refresh_in"] >= 60


def test_viewer_password_protects_the_stream_link_not_only_the_page(lv):
    room = room_live(lv)
    login(lv, "carol")
    assert lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": "short"}).status_code == 422
    r = lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": "zritel-2026"})
    assert r.status_code == 200 and r.json()["password_enabled"] and "password" not in str(r.json()).replace("password_enabled", "").replace("has_password", "").replace("speaker_password_set", "")
    rm = get_room(lv, room["id"])
    assert rm.live_password_hash and "zritel" not in rm.live_password_hash and viewer(lv, rm.live_token).json()["password_required"] is True
    assert access(lv, rm.live_token).json()["detail"]["code"] == "password_required", "без пароля подписанной ссылки на видео нет"
    assert access(lv, rm.live_token, password="не тот").json()["detail"]["code"] == "password_invalid"
    ok = access(lv, rm.live_token, password="zritel-2026")
    assert ok.status_code == 200 and ok.json()["pass"] and ok.json()["path"].startswith("/hls/")
    again = access(lv, rm.live_token, **{"pass": ok.json()["pass"]})
    assert again.status_code == 200 and "pass" not in again.json(), "продление ссылки без повторного пароля"
    assert access(lv, rm.live_token, **{"pass": ok.json()["pass"][:-3] + "AAA"}).status_code == 403, "поддельный пропуск"
    # смена пароля: прежний пропуск и прежний идентификатор доступа обесценены сразу
    old_acc = rm.live_view_access
    login(lv, "carol")
    assert lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": "novyi-parol-1"}).status_code == 200
    assert get_room(lv, room["id"]).live_view_access != old_acc
    assert access(lv, rm.live_token, **{"pass": ok.json()["pass"]}).json()["detail"]["code"] == "password_required"
    assert access(lv, rm.live_token, password="zritel-2026").status_code == 403
    assert access(lv, rm.live_token, password="novyi-parol-1").status_code == 200


def test_wrong_viewer_passwords_are_throttled_and_logged_without_the_password(lv):
    room = room_live(lv)
    login(lv, "carol")
    lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": "zritel-2026"})
    token = get_room(lv, room["id"]).live_token
    codes = [access(lv, token, password=f"bad-{i}").status_code for i in range(12)]
    assert codes[:8] == [403] * 8 and 429 in codes[8:], "перебор пароля останавливается"
    assert access(lv, token, password="zritel-2026").status_code == 429, "даже верный пароль — после блокировки адреса"
    login(lv, "root")
    rows = lv.get("/api/v1/admin/audit?limit=50").json() if lv.get("/api/v1/admin/audit?limit=1").status_code == 200 else []
    assert "bad-" not in str(rows) and "zritel-2026" not in str(rows)


def test_viewer_and_speaker_passwords_must_differ_in_both_directions(lv):
    room = room_live(lv)
    login(lv, "carol")
    assert lv.patch(f"/api/v1/rooms/{room['id']}/manage", json={"password": "speaker-pass-1"}).status_code == 200
    r = lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": "speaker-pass-1"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "same_as_speaker"
    assert lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": "viewer-pass-9"}).status_code == 200
    assert lv.patch(f"/api/v1/rooms/{room['id']}/manage", json={"password": "viewer-pass-9"}).status_code == 422
    r = lv.put(f"/api/v1/rooms/{room['id']}/live/password", json={"password": None})
    assert r.status_code == 200 and r.json()["password_enabled"] is False and r.json()["has_password"] is False


def test_revoking_the_link_gives_a_new_address_and_removes_the_old_access_at_once(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    before = get_room(lv, room["id"])
    login(lv, "carol")
    r = lv.post(f"/api/v1/rooms/{room['id']}/live/links/view/revoke")
    assert r.status_code == 200 and r.json()["view_path"] != f"/live/{before.live_token}"
    after = get_room(lv, room["id"])
    assert viewer(lv, before.live_token).status_code == 404, "старая ссылка зрителя недействительна"
    assert not (lv.dir / "acc" / before.live_view_access).exists(), "ссылка доступа к каталогу эфира удалена: прежние адреса HLS перестали открываться"
    assert (lv.dir / "acc" / after.live_view_access).is_symlink() and (lv.dir / "acc" / before.live_stream_access).is_symlink(), "ссылка на поток не затронута"
    login(lv, "carol")
    assert lv.post(f"/api/v1/rooms/{room['id']}/live/links/stream/revoke").status_code == 200
    assert not (lv.dir / "acc" / before.live_stream_access).exists() and (lv.dir / "acc" / after.live_view_access).is_symlink()
    login(lv, "bob")
    assert lv.post(f"/api/v1/rooms/{room['id']}/live/links/all/revoke").status_code == 404


def test_viewer_counter_counts_recent_players_only_and_rejects_garbage(lv):
    room = room_live(lv)
    token = get_room(lv, room["id"]).live_token
    lv.cookies.clear()
    for i in range(3):
        assert lv.post(f"/api/v1/live/{token}/ping", json={"vid": f"v{i}abc"}).json()["viewers"] == i + 1
    assert lv.post(f"/api/v1/live/{token}/ping", json={"vid": "v0abc"}).json()["viewers"] == 3, "тот же зритель — не второй"
    assert lv.post(f"/api/v1/live/{token}/ping", json={"vid": "x y/../"}).status_code == 422


# --------------------------------------------------------------------------------------------- вход выступающих
def test_speaker_password_is_asked_from_speakers_but_not_from_the_owner_and_leaders(lv):
    room = room_live(lv, password="speaker-pass-1")
    login(lv, "alice")
    r = lv.post(f"/api/v1/rooms/{room['id']}/join", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "room_password_required"
    assert lv.post(f"/api/v1/rooms/{room['id']}/join", json={"password": "speaker-pass-1"}).status_code == 200
    login(lv, "carol")
    assert lv.post(f"/api/v1/rooms/{room['id']}/join", json={}).status_code == 200, "руководитель входит по своим правам"
    reg = make_room(lv, moderators=LEADERS, password="room-pass-77")
    login(lv, "carol")
    assert lv.post(f"/api/v1/rooms/{reg['id']}/join", json={}).status_code == 403, "в обычной комнате пароль нужен всем, как раньше"


def test_room_list_marks_the_live_room_with_viewers(lv):
    room = room_live(lv)
    token = get_room(lv, room["id"]).live_token
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    lv.cookies.clear()
    lv.post(f"/api/v1/live/{token}/ping", json={"vid": "aaaa1111"})
    login(lv, "bob")
    mine = [r for r in lv.get("/api/v1/rooms").json() if r["id"] == room["id"]][0]
    assert mine["live"] is True and mine["live_enabled"] is True and mine["live_viewers"] == 1 and mine["room_type"] == "broadcast"


def test_failed_broadcast_is_reported_to_the_room_with_its_reason(lv):
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    lv.fake.infos["EG_test1"] = eg.EgressInfo(egress_id="EG_test1", status=eg.EgressStatus.EGRESS_FAILED, error="rtmp: connection refused")
    reconcile(lv)
    login(lv, "carol")
    st = lv.get(f"/api/v1/meetings/{mid}/live").json()
    assert st["live"] is False and st["status"] == "failed" and "connection refused" in st["error"], "после падения эфира руководитель видит причину"
    assert st["can_start"] is True, "и может начать эфир заново"
