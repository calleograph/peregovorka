"""Видеозапись встреч (LiveKit Egress): права, лимиты, идемпотентность, пустая комната, шифрование, итог файла, сверка. Сам Egress подменён (в тестах нет браузера и кодеков):
проверяется логика приложения вокруг него, а не картинка."""
from __future__ import annotations

import asyncio
import types
import uuid
from pathlib import Path

import pytest
from livekit.protocol import egress as eg
from sqlalchemy import select

from app.models import Meeting, Recording
from app.services import video_rec
from app.services.video_rec import QUALITY, VideoError, build_request

from .conftest import login, make_room, put_settings
from .test_mail import LEADERS
from .test_transcripts import _join


class FakeEgress:
    def __init__(self):
        self.started: list[eg.StartEgressRequest] = []
        self.stopped: list[str] = []
        self.infos: dict[str, eg.EgressInfo | None] = {}
        self.fail_start = False
        self.fail_stop = ""
        self.alive = True            # есть ли в комнате скрытый участник-рекордер (False — служба Egress упала)
        self.n = 0

    async def recorder_present(self, room_name, egress_id):
        return self.alive

    async def start(self, req):
        if self.fail_start:
            raise RuntimeError("egress not connected")
        self.started.append(req)
        self.n += 1
        return eg.EgressInfo(egress_id=f"EG_test{self.n}", status=eg.EgressStatus.EGRESS_STARTING)

    async def stop(self, egress_id):
        if self.fail_stop:
            raise RuntimeError(self.fail_stop)
        self.stopped.append(egress_id)
        return eg.EgressInfo(egress_id=egress_id, status=eg.EgressStatus.EGRESS_ENDING)

    async def get(self, egress_id):
        return self.infos.get(egress_id)

    async def probe(self):
        if self.fail_start:
            raise RuntimeError("egress not connected")

    async def probe_livekit(self):
        return None

    async def cpu_load(self, health_url):
        return None


@pytest.fixture
def video(client, monkeypatch):
    fake = FakeEgress()
    client.app_obj.state.video.api = fake
    monkeypatch.setattr(client.app_obj.state.settings, "video_enabled", "yes")    # служба Egress «установлена» (сведений помощника в тестах нет)
    present = {"set": {"u-1"}}

    async def fake_present(settings, room_name):
        return present["set"]

    monkeypatch.setattr(video_rec, "list_present_identities", fake_present)
    monkeypatch.setattr(video_rec, "readable_duration", lambda p: 60)            # подставные «файлы» в тестах — не настоящий MP4
    client.fake, client.present = fake, present
    return client


def enable(client, **over):
    return put_settings(client, "video", **{"enabled": True, "min_free_gb": 1, **over})


def set_room(client, room, user="carol", **fields):
    login(client, user)
    r = client.patch(f"/api/v1/rooms/{room['id']}/manage", json=fields)
    return r


def live(client, room, mode="manual", **settings):
    enable(client, **settings)
    assert set_room(client, room, video_mode=mode).status_code == 200
    return _join(client, "alice", room["id"])["meeting_id"]


def toggle(client, mid, enabled, user="carol"):
    login(client, user)
    return client.post(f"/api/v1/meetings/{mid}/video-recording", json={"enabled": enabled})


def rows(client, mid):
    async def q():
        async with client.app_obj.state.session_maker() as db:
            return list((await db.execute(select(Recording).where(Recording.meeting_id == uuid.UUID(mid), Recording.kind == "mix_video").order_by(Recording.created_at))).scalars())
    return client.portal.call(q)


def finish_event(egress_id, *, status=eg.EgressStatus.EGRESS_COMPLETE, error="", duration_s=0, name="ended"):
    info = eg.EgressInfo(egress_id=egress_id, status=status, error=error)
    if duration_s:
        info.file_results.add(duration=duration_s * 1_000_000_000, size=1000)
    return types.SimpleNamespace(event=f"egress_{name}", egress_info=info)


def call(client, fn):
    async def run():
        async with client.app_obj.state.session_maker() as db:
            return await fn(db)
    return client.portal.call(run)


# --------------------------------------------------------------------------------------------- запрос к Egress
def test_request_uses_start_egress_template_h264_aac_mp4_and_the_chosen_quality():
    for q, (w, h, kbps) in QUALITY.items():
        req = build_request("room-x", "/out/a/b.mp4", q)
        assert req.room_name == "room-x" and req.template.layout == "speaker" and not req.template.custom_base_url
        assert (req.advanced.width, req.advanced.height, req.advanced.video_bitrate) == (w, h, kbps)
        from livekit.protocol import models as lkm
        assert req.advanced.video_codec == lkm.VideoCodec.H264_HIGH and req.advanced.audio_codec == lkm.AudioCodec.AAC, "H.264 High и AAC (по именам, а не числам)"
        out = req.outputs[0].file
        assert out.filepath == "/out/a/b.mp4" and out.file_type == eg.EncodedFileType.MP4
    assert build_request("r", "/out/x.mp4", "720p", template_url="https://tpl.example.org/t").template.custom_base_url == "https://tpl.example.org/t"
    assert (1280, 720) == QUALITY["720p"][:2] and (1920, 1080) == QUALITY["1080p"][:2]


# --------------------------------------------------------------------------------------------- включение и права
def test_defaults_are_off_and_start_explains_why(video):
    room = make_room(video, moderators=LEADERS)
    mid = _join(video, "alice", room["id"])["meeting_id"]
    r = toggle(video, mid, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "video_disabled" and "выключена администратором" in r.json()["detail"]["message"]
    enable(video)
    r = toggle(video, mid, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "video_forbidden", "в комнате режим «выключено»"
    assert video.fake.started == []


def test_only_room_leaders_control_but_everyone_sees_the_state(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    _join(video, "bob", room["id"])
    login(video, "bob")
    st = video.get(f"/api/v1/meetings/{mid}/video-recording")
    assert st.status_code == 200 and st.json()["recording"] is False and st.json()["can_control"] is False and st.json()["can_start"] is True
    r = toggle(video, mid, True, user="bob")
    assert r.status_code == 403 and video.fake.started == []
    assert toggle(video, mid, True).status_code == 200
    login(video, "bob")
    st = video.get(f"/api/v1/meetings/{mid}/video-recording").json()
    assert st["recording"] is True and st["fragments"] == 1 and st["since"], "индикатор виден участнику"


# --------------------------------------------------------------------------------------------- старт, повтор, фрагменты
def test_start_creates_one_processing_recording_and_repeat_is_idempotent(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    assert toggle(video, mid, True).status_code == 200
    assert toggle(video, mid, True).status_code == 200, "повторное нажатие не создаёт второй файл"
    assert len(video.fake.started) == 1
    (rec,) = rows(video, mid)
    assert rec.status == "processing" and rec.has_video and rec.egress_id == "EG_test1" and rec.mime == "video/mp4" and rec.path.endswith("/video-1.mp4")
    req = video.fake.started[0]
    assert req.outputs[0].file.filepath == f"/out/{rec.path}" and req.advanced.height == 720
    assert call(video, lambda db: db.get(Meeting, uuid.UUID(mid))).record_video is True


def test_stop_then_start_gives_a_new_fragment_and_keeps_the_first(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    toggle(video, mid, True)
    assert toggle(video, mid, False).status_code == 200 and video.fake.stopped == ["EG_test1"]
    first = rows(video, mid)[0]
    # итог первого фрагмента приходит от Egress позже; пока файла нет — запись закроется как failed, но не исчезнет
    call(video, lambda db: video.app_obj.state.video.on_egress_event(db, finish_event("EG_test1", duration_s=60)))
    assert toggle(video, mid, True).status_code == 200
    rs = rows(video, mid)
    assert [r.path.rsplit("/", 1)[-1] for r in rs] == ["video-1.mp4", "video-2.mp4"] and rs[0].id == first.id and rs[1].status == "processing"


def test_quality_comes_from_the_room_and_falls_back_to_the_default(video):
    room = make_room(video, moderators=LEADERS)
    enable(video, default_quality="1080p")
    assert set_room(video, room, video_mode="manual", video_quality="1080p").status_code == 200
    mid = _join(video, "alice", room["id"])["meeting_id"]
    toggle(video, mid, True)
    assert video.fake.started[0].advanced.height == 1080


# --------------------------------------------------------------------------------------------- защитные условия
def test_empty_room_is_not_recorded_and_unavailable_livekit_is_reported(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    video.present["set"] = set()
    r = toggle(video, mid, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "room_empty" and rows(video, mid) == []

    async def none(settings, room_name):
        return None

    video_rec.list_present_identities = none
    r = toggle(video, mid, True)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "livekit_unavailable"


def test_missing_or_broken_egress_gives_a_clear_message_and_no_recording_row(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    video.fake.fail_start = True
    r = toggle(video, mid, True)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "egress_unavailable" and "LiveKit Egress" in r.json()["detail"]["message"]
    assert rows(video, mid) == [] and call(video, lambda db: db.get(Meeting, uuid.UUID(mid))).record_video is False


def test_concurrency_limit_disk_and_quota_are_checked_before_start(video, monkeypatch):
    r1, r2 = make_room(video, moderators=LEADERS), make_room(video, moderators=LEADERS)
    m1 = live(video, r1, max_concurrent=1)
    assert set_room(video, r2, video_mode="manual").status_code == 200
    m2 = _join(video, "bob", r2["id"])["meeting_id"]
    assert toggle(video, m1, True).status_code == 200
    r = toggle(video, m2, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "video_busy" and len(video.fake.started) == 1

    enable(video, max_concurrent=5, min_free_gb=100)
    monkeypatch.setattr(video_rec.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=5 * 1024**3, total=10**12, used=0))
    r = toggle(video, m2, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "disk_low"

    monkeypatch.undo()
    monkeypatch.setattr(video_rec, "list_present_identities", lambda s, n: _ret({"u-1"}))
    enable(video, max_concurrent=5, quota_gb=1)

    async def big(db):
        rec = (await db.execute(select(Recording).where(Recording.kind == "mix_video"))).scalars().first()
        rec.size_bytes = 2 * 1024**3
        await db.commit()
    call(video, big)
    r = toggle(video, m2, True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "quota", "старые записи при превышении не удаляются, новая не начинается"


async def _ret(v):
    return v


def test_video_is_refused_with_end_to_end_encryption_or_storage_encryption(video):
    enable(video)
    room = make_room(video, moderators=LEADERS)
    r = set_room(video, room, e2ee_mode="trusted")
    if r.status_code == 200:
        r = set_room(video, room, video_mode="manual")
        assert r.status_code == 409 and "шифрован" in r.json()["detail"]
    room2 = make_room(video, moderators=LEADERS)
    assert set_room(video, room2, video_mode="manual").status_code == 200
    r = set_room(video, room2, e2ee_mode="full")
    assert r.status_code == 409 and "видеозапись" in r.json()["detail"].lower(), "нельзя включить шифрование в комнате с видеозаписью, не отключив её"


# --------------------------------------------------------------------------------------------- автозапуск и пустая комната
def test_auto_mode_starts_on_first_join_once_and_manual_stop_is_respected(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room, mode="auto")
    svc = video.app_obj.state.video

    async def join(db):
        await svc.on_join(db, await db.get(Meeting, uuid.UUID(mid)))

    call(video, join)
    assert len(video.fake.started) == 1 and rows(video, mid)[0].status == "processing"
    call(video, join)
    assert len(video.fake.started) == 1, "второй вошедший новую запись не запускает"
    assert toggle(video, mid, False).status_code == 200
    call(video, join)
    assert len(video.fake.started) == 1, "после ручной остановки автозапуск не перебивает решение руководителя"
    assert toggle(video, mid, True).status_code == 200 and len(video.fake.started) == 2


def test_room_emptying_stops_the_recording(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    toggle(video, mid, True)
    svc = video.app_obj.state.video

    async def go(db):
        m = await db.get(Meeting, uuid.UUID(mid))
        await svc.on_empty(db, m)
    call(video, go)
    assert video.fake.stopped == ["EG_test1"]
    assert call(video, lambda db: db.get(Meeting, uuid.UUID(mid))).record_video is False


# --------------------------------------------------------------------------------------------- итог файла
def test_egress_ended_makes_the_file_ready_failed_or_keeps_what_exists(video, settings):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    svc = video.app_obj.state.video
    exported: list[str] = []

    async def export(db, rec):
        exported.append(rec.path)
    svc.export = export
    toggle(video, mid, True)
    (rec,) = rows(video, mid)
    f = Path(settings.recordings_path, rec.path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x" * 4096)
    call(video, lambda db: svc.on_egress_event(db, finish_event(rec.egress_id, duration_s=125)))
    (rec,) = rows(video, mid)
    assert rec.status == "ready" and rec.size_bytes == 4096 and rec.duration_s == 125 and exported == [rec.path]
    assert call(video, lambda db: db.get(Meeting, uuid.UUID(mid))).record_video is False
    # повторное событие ничего не меняет
    call(video, lambda db: svc.on_egress_event(db, finish_event(rec.egress_id, duration_s=1)))
    assert rows(video, mid)[0].duration_s == 125 and len(exported) == 1

    toggle(video, mid, True)
    rec2 = rows(video, mid)[1]
    call(video, lambda db: svc.on_egress_event(db, finish_event(rec2.egress_id, status=eg.EgressStatus.EGRESS_FAILED, error="encoder crashed")))
    rec2 = rows(video, mid)[1]
    assert rec2.status == "failed" and "encoder crashed" in rec2.error and len(exported) == 1, "сбой Egress — запись failed с причиной, без выгрузки"


def test_reconcile_closes_orphans_and_stops_recordings_of_finished_meetings(video, settings):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    toggle(video, mid, True)
    (rec,) = rows(video, mid)
    svc = video.app_obj.state.video
    # Egress жив — ничего не трогаем
    video.fake.infos[rec.egress_id] = eg.EgressInfo(egress_id=rec.egress_id, status=eg.EgressStatus.EGRESS_ACTIVE)
    assert call(video, svc.reconcile) == 0 and rows(video, mid)[0].status == "processing"
    # встреча закончилась, а запись идёт — отправляем остановку
    login(video, "root")
    assert video.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    assert rec.egress_id in video.fake.stopped
    # Egress пропал после перезапуска (нет в списке), файл есть — запись закрывается как готовая
    f = Path(settings.recordings_path, rec.path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"y" * 2048)
    video.fake.infos[rec.egress_id] = None
    assert call(video, svc.reconcile) == 1
    got = rows(video, mid)[0]
    assert got.status == "ready" and got.size_bytes == 2048


def test_orphan_without_file_becomes_failed_not_lost(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    toggle(video, mid, True)
    video.fake.infos["EG_test1"] = None
    assert call(video, video.app_obj.state.video.reconcile) == 1
    rec = rows(video, mid)[0]
    assert rec.status == "failed" and rec.error, "запись остаётся в списке с причиной"


def test_stop_reports_unreachable_egress_but_not_when_it_already_finished(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    toggle(video, mid, True)
    video.fake.fail_stop = "connection refused"
    r = toggle(video, mid, False)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "egress_unavailable"
    video.fake.fail_stop = "egress not_found"
    assert toggle(video, mid, False).status_code == 200
    assert rows(video, mid)[0].status in ("failed", "ready"), "Egress уже закончил — запись закрыта, а не осталась «идущей»"


def test_egress_recorder_is_not_counted_as_a_participant():
    from app.services.livekit import SERVICE_IDENTITY_PREFIXES
    assert "EG_AbC123".startswith(SERVICE_IDENTITY_PREFIXES) and "asr-x".startswith(SERVICE_IDENTITY_PREFIXES) and not "u-1".startswith(SERVICE_IDENTITY_PREFIXES)


def test_presentation_room_gets_auto_video_when_enabled_and_not_when_encrypted(video):
    enable(video)
    login(video, "root")
    room = make_room(video, moderators=LEADERS, room_type="presentation")
    assert set_room(video, room, name="Презентация 2").json()["video_mode"] == "auto"
    enc = make_room(video, moderators=LEADERS, room_type="presentation", e2ee_mode="full")
    assert set_room(video, enc, name="Зашифрованная").json()["video_mode"] == "off", "с шифрованием видео не включается само"


def test_recording_longer_than_the_limit_is_closed_and_auto_mode_starts_the_next_fragment(video):
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room, mode="auto", max_hours=1)
    svc = video.app_obj.state.video

    async def join(db):
        await svc.on_join(db, await db.get(Meeting, uuid.UUID(mid)))
    call(video, join)
    first = rows(video, mid)[0]
    video.fake.infos[first.egress_id] = eg.EgressInfo(egress_id=first.egress_id, status=eg.EgressStatus.EGRESS_ACTIVE)

    async def age(db):
        r = await db.get(Recording, first.id)
        r.started_at = r.started_at.replace(year=2020)
        await db.commit()
    call(video, age)
    call(video, svc.reconcile)
    assert video.fake.stopped == [first.egress_id] and len(video.fake.started) == 2, "предельная запись закрыта, следующий фрагмент начат"


def test_custom_template_url_is_validated_and_reaches_the_egress_request(video):
    login(video, "root")
    r = video.put("/api/v1/admin/settings/video", json={"template_url": "http://x.example.org/t"})
    assert r.status_code in (400, 422), "только https"
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room, template_url="https://rec.example.org/tpl")
    assert toggle(video, mid, True).status_code == 200
    assert video.fake.started[0].template.custom_base_url == "https://rec.example.org/tpl" and video.fake.started[0].template.layout == "speaker"


def test_recording_whose_recorder_vanished_from_the_room_is_closed_not_left_running(video, settings, monkeypatch):
    """Настоящий Egress при падении службы: LiveKit продолжает считать запись активной, а рекордер из комнаты пропадает — по этому признаку запись закрывается."""
    monkeypatch.setattr(video_rec, "STALL_GRACE_S", 0)
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    toggle(video, mid, True)
    (rec,) = rows(video, mid)
    video.fake.infos[rec.egress_id] = eg.EgressInfo(egress_id=rec.egress_id, status=eg.EgressStatus.EGRESS_ACTIVE)
    svc = video.app_obj.state.video
    assert call(video, svc.reconcile) == 0, "рекордер на месте — запись идёт"
    video.fake.alive = False
    f = Path(settings.recordings_path, rec.path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"z" * 1024)
    assert call(video, svc.reconcile) == 1
    got = rows(video, mid)[0]
    assert call(video, lambda db: db.get(Meeting, uuid.UUID(mid))).record_video is False
    assert got.status == "failed" and "перестала отвечать" in got.error, "оборванная служба — запись failed с понятной причиной"


def test_file_that_cannot_be_read_is_failed_with_a_reason_and_left_on_disk(video, settings, monkeypatch):
    monkeypatch.setattr(video_rec, "readable_duration", lambda p: None)
    room = make_room(video, moderators=LEADERS)
    mid = live(video, room)
    svc = video.app_obj.state.video
    toggle(video, mid, True)
    (rec,) = rows(video, mid)
    f = Path(settings.recordings_path, rec.path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"broken" * 100)
    call(video, lambda db: svc.on_egress_event(db, finish_event(rec.egress_id, duration_s=30)))
    got = rows(video, mid)[0]
    assert got.status == "failed" and "не читается" in got.error and f.exists()
