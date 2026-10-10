"""Данные для плеера записей: волновая форма (пики громкости) и субтитры на шкале времени записи.

Волна считается один раз и хранится в базе; субтитры — это уже имеющиеся реплики стенограммы, пересчитанные во время записи (без повторного распознавания).
Для общей записи — все участники, для записи участника — только его реплики; права те же, что у воспроизведения.
"""
from __future__ import annotations

import base64
import datetime as dt
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.models import Recording, RecordingWaveform, TranscriptSegment, User
from app.services import waveforms as wf

from .conftest import login, make_settings, running_app
from .test_meeting_media import _meeting_with_recordings, needs_ffmpeg, tone, write_wav

EPOCH_A = 5000.0          # те же значения t0, что в _meeting_with_recordings: у второго участника файл начинается на 2 с позже


def db_run(c, fn):
    async def go():
        async with c.app_obj.state.session_maker() as db:
            return await fn(db)
    return c.portal.call(go)


def utc(sec: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(sec, tz=dt.timezone.utc)


# ----------------------------------------------------------------------------------------------------- чистая логика
def test_db_to_byte_scale_and_parsing():
    assert wf.db_to_byte(float("-inf")) == 0 and wf.db_to_byte(-90.3) == 0 and wf.db_to_byte(0.0) == 255
    assert wf.db_to_byte(-30.0) == pytest.approx(128, abs=1), "шкала логарифмическая: −30 дБ — середина высоты"
    lines = ["frame:0 pts:0 pts_time:0", "lavfi.astats.Overall.Peak_level=-inf", "frame:1", "lavfi.astats.Overall.Peak_level=-12.5", "lavfi.astats.Overall.Peak_level=nan"]
    assert list(wf.parse_peaks(lines)) == [0, wf.db_to_byte(-12.5), 0]


@needs_ffmpeg
def test_peaks_follow_the_real_loudness_of_the_audio(tmp_path):
    pcm = b"\x00\x00" * 16000 * 2 + tone(2, 440, 0.5) + b"\x00\x00" * 16000 * 1 + tone(1, 440, 0.02)      # 2 с тишины, 2 с громко, 1 с тишины, 1 с очень тихо
    peaks = wf.compute_peaks(write_wav(tmp_path / "a.wav", pcm))
    assert 55 <= len(peaks) <= 62, "около 10 столбиков в секунду"
    p = list(peaks)
    avg = lambda a, b: sum(p[int(a * 10):int(b * 10)]) / max(1, int(b * 10) - int(a * 10))
    assert avg(0.2, 1.8) < 10 and avg(2.2, 3.8) > 200 and avg(4.2, 4.8) < 10 and 40 < avg(5.2, 5.8) < 160, "тишина низко, громкая речь высоко, тихий звук посередине"


# ----------------------------------------------------------------------------------------------------- через приложение
@needs_ffmpeg
def test_waveform_is_built_once_stored_and_served_with_the_recording_rights(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        login(c, "root")
        media = c.get(f"/api/v1/meetings/{mid}/media").json()
        mix, parts = media["mixes"][0], media["participants"]
        w = c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform")
        assert w.status_code == 200, "волна построена сразу при завершении встречи, а не при открытии плеера"
        j = w.json()
        raw = base64.b64decode(j["peaks"])
        assert j["status"] == "ready" and j["bucket_ms"] == 100 and 45 <= len(raw) <= 56
        assert max(raw) > 150
        for p in parts:
            assert c.get(f"/api/v1/meetings/{mid}/media/{p['id']}/waveform").status_code == 200, "волна есть и у индивидуальных записей"
        # права: общая запись — участнику встречи; файл участника — только администратору; чужие и без входа — нет
        login(c, "bob")
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform").status_code == 200
        assert c.get(f"/api/v1/meetings/{mid}/media/{parts[0]['id']}/waveform").status_code == 404
        login(c, "carol")
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform").status_code == 404
        c.cookies.clear()
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform").status_code == 401
        # старая запись без волны: первый запрос запускает фоновое построение (202), потом данные готовы; повторно строить не нужно
        login(c, "root")

        async def forget(db):
            await db.execute(RecordingWaveform.__table__.delete())
            await db.commit()
        db_run(c, forget)
        first = c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform")
        assert first.status_code == 202 and first.json() == {"status": "processing"}
        c.portal.call(lambda: c.app_obj.state.waveforms.drain())
        again = c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform")
        assert again.status_code == 200 and again.json()["status"] == "ready"
        # удаление записей уносит и волну
        assert c.delete(f"/api/v1/meetings/{mid}/recordings").status_code == 204
        assert db_run(c, lambda db: _count(db)) == 0


async def _count(db):
    return len((await db.execute(select(RecordingWaveform))).scalars().all())


@needs_ffmpeg
def test_waveform_survives_a_transfer_to_the_other_storage(tmp_path, directory):
    from .conftest import put_settings

    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        put_settings(c, "audio_storage", enabled=True, local_path=str(tmp_path / "arch"), keep_local_copy=False)
        mid = _meeting_with_recordings(c, s)                                  # файлы только во внешнем хранилище, локальных копий нет
        assert not list(Path(s.recordings_path).rglob("*.m4a"))
        login(c, "root")
        mix = c.get(f"/api/v1/meetings/{mid}/media").json()["mixes"][0]
        j = c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform").json()
        assert j["status"] == "ready" and j["peaks"], "волна построена до выгрузки и хранится в базе, а не рядом с файлом"

        async def forget(db):
            await db.execute(RecordingWaveform.__table__.delete())
            await db.commit()
        db_run(c, forget)
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform").status_code == 202
        c.portal.call(lambda: c.app_obj.state.waveforms.drain())
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/waveform").json()["status"] == "ready", "для записи во внешнем хранилище пересчёт читает файл оттуда"


# ----------------------------------------------------------------------------------------------------- субтитры
def seed_segments(c, mid: str, ident_a: str, ident_b: str, user_a, user_b):
    async def go(db):
        meeting_room = (await db.execute(select(Recording.room_id).where(Recording.meeting_id == uuid.UUID(mid)))).scalars().first()
        rows = [(ident_a, user_a, 5000.5, 5002.0, "Предлагаю начать"), (ident_b, user_b, 5002.5, 5004.0, "Согласен"), (ident_a, user_a, 5002.8, 5003.4, "Одновременно")]
        for a, u, st, en, text in rows:
            db.add(TranscriptSegment(segment_uid=uuid.uuid4(), meeting_id=uuid.UUID(mid), room_id=meeting_room, user_id=u, participant_identity=a, started_at=utc(st), ended_at=utc(en), text=text))
        await db.commit()
    db_run(c, go)


def identities(c, mid: str):
    async def go(db):
        rows = (await db.execute(select(Recording).where(Recording.meeting_id == uuid.UUID(mid), Recording.kind == "participant").order_by(Recording.started_at))).scalars().all()
        return [(r.participant_identity, r.user_id, r.id, r.started_at) for r in rows]
    return db_run(c, go)


@needs_ffmpeg
def test_subtitles_use_the_existing_transcript_on_the_timeline_of_each_recording(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        (ia, ua, ra, sa), (ib, ub, rb, sb) = identities(c, mid)
        assert sa == utc(5000.0) and sb == utc(5002.0), "начало файла участника сохраняется в записи (для привязки реплик)"
        seed_segments(c, mid, ia, ib, ua, ub)
        login(c, "root")
        media = c.get(f"/api/v1/meetings/{mid}/media").json()
        mix = media["mixes"][0]
        sub = c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/subtitles").json()
        assert sub["available"] and sub["scope"] == "meeting"
        got = [(x["start"], x["speaker"] != "", x["text"]) for x in sub["segments"]]
        assert got == [(0.5, True, "Предлагаю начать"), (2.5, True, "Согласен"), (2.8, True, "Одновременно")], "время реплики = её время минус момент начала записи; одновременные реплики сохраняются обе"
        assert all(x["end"] > x["start"] and x["speaker"] for x in sub["segments"])
        # запись участника B: её нулевая секунда — 5002, чужих реплик нет, а время — по шкале ЭТОГО файла
        only_b = c.get(f"/api/v1/meetings/{mid}/media/{rb}/subtitles").json()
        assert only_b["scope"] == "participant" and [(x["start"], x["text"]) for x in only_b["segments"]] == [(0.5, "Согласен")]
        only_a = c.get(f"/api/v1/meetings/{mid}/media/{ra}/subtitles").json()
        assert [x["text"] for x in only_a["segments"]] == ["Предлагаю начать", "Одновременно"] and "Согласен" not in str(only_a)
        # права
        login(c, "bob")
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/subtitles").json()["available"] is True
        assert c.get(f"/api/v1/meetings/{mid}/media/{ra}/subtitles").status_code == 404, "субтитры файла участника — только администратору, как и сам файл"
        login(c, "carol")
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/subtitles").status_code == 404
        c.cookies.clear()
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/subtitles").status_code == 401


@needs_ffmpeg
def test_subtitles_say_why_they_are_unavailable_instead_of_guessing(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        login(c, "root")
        media = c.get(f"/api/v1/meetings/{mid}/media").json()
        mix = media["mixes"][0]
        none = c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/subtitles").json()
        assert none["available"] is False and none["reason"] == "no_transcript" and none["segments"] == [], "встреча без транскрибации"
        (ia, ua, ra, _), (ib, ub, rb, _) = identities(c, mid)
        seed_segments(c, mid, ia, ib, ua, ub)

        async def old(db):                                                   # запись без привязки ко времени (сделана до этой версии)
            await db.execute(update(Recording).where(Recording.id == ra).values(started_at=None))
            await db.commit()
        db_run(c, old)
        r = c.get(f"/api/v1/meetings/{mid}/media/{ra}/subtitles").json()
        assert r["available"] is False and r["reason"] == "no_timeline" and "недоступны" in r["message"] and r["segments"] == []

        async def far(db):                                                   # реплики, не попадающие в эту запись
            await db.execute(update(Recording).where(Recording.id == rb).values(started_at=utc(90000.0)))
            await db.commit()
        db_run(c, far)
        assert c.get(f"/api/v1/meetings/{mid}/media/{rb}/subtitles").json()["reason"] == "no_overlap"
