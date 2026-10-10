"""Общая запись встречи и просмотр в браузере: сведение дорожек по времени, формирование в фоне, список материалов, потоковая отдача с Range, права доступа, локальное и «внешнее» хранилище.
Тесты со сведением звука требуют ffmpeg (в CI он есть; локально — переменная FFMPEG_BIN либо установленный ffmpeg), иначе они пропускаются, а не «проходят»."""
from __future__ import annotations

import math
import struct
import subprocess
import uuid
from pathlib import Path

import pytest

from app.api.media import parse_range
from app.services import mixdown
from app.services.recordings import wav_header

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_admin_features import _drain
from .test_transcripts import _join

FF = mixdown.ffmpeg_path()
needs_ffmpeg = pytest.mark.skipif(FF is None, reason="ffmpeg недоступен")
RATE = 16000


def tone(seconds: float, freq: float, amp: float) -> bytes:
    n = int(seconds * RATE)
    return struct.pack(f"<{n}h", *(int(amp * 32767 * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)))


def write_wav(path: Path, pcm: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_header(len(pcm)) + pcm)
    return path


def decode(path: Path) -> list[int]:
    """Расшифровка итогового файла обратно в отсчёты (ffmpeg → s16le 16 кГц)."""
    raw = subprocess.run([FF, "-v", "error", "-nostdin", "-i", str(path), "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-"], capture_output=True, check=True).stdout
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def rms(samples: list[int], a: float, b: float) -> float:
    seg = samples[int(a * RATE):int(b * RATE)]
    return math.sqrt(sum(x * x for x in seg) / max(1, len(seg)))


# ----------------------------------------------------------------------------------------------------- план и команда (без ffmpeg)
def test_plan_places_tracks_by_their_start_time_not_one_after_another(tmp_path):
    a, b, c = tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "c.wav"
    p = mixdown.plan([mixdown.Track(a, 1000.0), mixdown.Track(b, 1002.5), mixdown.Track(c, None)])
    assert p.start == 1000.0
    assert [d for _, d in p.items] == [0, 2500, 0], "смещение — от самого раннего начала; дорожка без отметки времени ставится в начало"


def test_command_has_delays_mix_without_normalization_and_a_limiter(tmp_path):
    p = mixdown.plan([mixdown.Track(tmp_path / "a.wav", 10.0), mixdown.Track(tmp_path / "b.wav", 12.0)])
    cmd = mixdown.build_command("ffmpeg", p, tmp_path / "out.m4a")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "adelay=0:all=1" in graph and "adelay=2000:all=1" in graph and "amix=inputs=2" in graph and "normalize=0" in graph and "alimiter" in graph
    assert cmd.count("-i") == 2 and "aac" in cmd and "+faststart" in cmd
    assert not any(" " in a and a.startswith("[") for a in cmd), "пути передаются отдельными аргументами"
    solo = mixdown.build_command("ffmpeg", mixdown.plan([mixdown.Track(tmp_path / "a.wav", 5.0)]), tmp_path / "o.m4a")
    assert "alimiter" in solo[solo.index("-filter_complex") + 1] and "amix" not in solo[solo.index("-filter_complex") + 1]
    with pytest.raises(mixdown.MixError):
        mixdown.build_command("ffmpeg", mixdown.plan([]), tmp_path / "x.m4a")


def test_range_header_parsing():
    assert parse_range(None, 1000) is None
    assert parse_range("bytes=0-99", 1000) == (0, 99)
    assert parse_range("bytes=900-", 1000) == (900, 999)
    assert parse_range("bytes=-100", 1000) == (900, 999)
    assert parse_range("bytes=500-5000", 1000) == (500, 999), "конец за пределами файла обрезается"
    for bad in ("bytes=1000-", "bytes=9-3", "bytes=-", "bytes=-0", "items=0-5", "bytes=a-b"):
        assert parse_range(bad, 1000) == "bad", bad


# ----------------------------------------------------------------------------------------------------- сведение реальным ffmpeg
@needs_ffmpeg
def test_mix_follows_the_timeline_pauses_and_overlap_without_clipping(tmp_path):
    """Алиса: 0–2 с; Боб: 3–5 с (пауза 1 с — тишина); Карол говорит одновременно с Бобом 3–4 с, обе дорожки громкие — сумма не должна обрезаться."""
    a = write_wav(tmp_path / "a.wav", tone(2, 440, 0.5))
    b = write_wav(tmp_path / "b.wav", tone(2, 880, 0.9))
    c = write_wav(tmp_path / "c.wav", tone(1, 660, 0.9))
    out = tmp_path / "mix.m4a"
    plan, dur = mixdown.mix([mixdown.Track(a, 100.0), mixdown.Track(b, 103.0), mixdown.Track(c, 103.0)], out)
    assert plan.start == 100.0 and abs(dur - 5) <= 1
    s = decode(out)
    assert abs(len(s) / RATE - 5) < 0.3
    assert rms(s, 0.2, 1.8) > 3000, "Алиса слышна в начале"
    assert rms(s, 2.2, 2.8) < 400, "пауза — тишина, а не склейка файлов подряд"
    assert rms(s, 3.2, 3.8) > 4000 and rms(s, 4.2, 4.8) > 3000, "Боб слышен позже — по своему времени"
    assert sum(1 for x in s if abs(x) >= 32700) <= len(s) * 0.001, "одновременная речь не перегружена (ограничитель + запас под выбросы AAC)"
    assert out.stat().st_size < 200_000 and out.read_bytes()[4:8] == b"ftyp", "компактный MP4/M4A, который понимают браузеры"


# ----------------------------------------------------------------------------------------------------- весь путь через приложение
def _meeting_with_recordings(c, s, *, mode="audio"):
    c.app_obj.state.protocols.flush_delay = 0
    room = make_room(c, name="Совещание", record_audio=True)
    if mode != "audio":
        login(c, "root")
        assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"recording_mode": mode}).status_code == 200
    a = _join(c, "alice", room["id"])
    b = _join(c, "bob", room["id"])
    pcm = Path(s.recordings_path) / a["livekit_room"]
    pcm.mkdir(parents=True)
    (pcm / f"{a['identity']}.pcm").write_bytes(tone(3, 440, 0.4))
    (pcm / f"{b['identity']}.pcm").write_bytes(tone(3, 880, 0.4))
    (pcm / f"{a['identity']}.t0").write_text("5000.000")
    (pcm / f"{b['identity']}.t0").write_text("5002.000")
    login(c, "alice")
    c.post(f"/api/v1/meetings/{a['meeting_id']}/end")
    _drain(c)
    return a["meeting_id"]


@needs_ffmpeg
def test_finished_meeting_gets_one_mix_in_the_background_and_it_plays_with_ranges(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        login(c, "alice")                                             # участник встречи — организатор
        media = c.get(f"/api/v1/meetings/{mid}/media")
        assert media.status_code == 200
        body = media.json()
        assert [m["kind"] for m in body["mixes"]] == ["mix_audio"] and body["participants"] == [], "участнику — общая запись; файлы участников — только администратору"
        mix = body["mixes"][0]
        assert mix["status"] == "ready" and mix["mime"] == "audio/mp4" and 4 <= mix["duration_s"] <= 6 and mix["can_download"] is True and mix["started_at"].startswith("1970-01-01T01:23:20")
        url = f"/api/v1/meetings/{mid}/media/{mix['id']}/stream"
        full = c.get(url)
        assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes" and len(full.content) == mix["size_bytes"] and full.headers["content-type"].startswith("audio/mp4")
        assert "attachment" not in full.headers["content-disposition"], "воспроизведение, а не скачивание"
        part = c.get(url, headers={"Range": "bytes=100-199"})
        assert part.status_code == 206 and part.content == full.content[100:200] and part.headers["content-range"] == f"bytes 100-199/{len(full.content)}"
        tail = c.get(url, headers={"Range": "bytes=-50"})
        assert tail.status_code == 206 and tail.content == full.content[-50:]
        assert c.get(url, headers={"Range": f"bytes={len(full.content) + 10}-"}).status_code == 416
        # скачать может организатор; обычный участник (bob не начинал встречу и не руководитель) — только смотреть
        assert c.get(url + "?download=true").status_code == 200
        login(c, "bob")
        assert c.get(url).status_code in (200, 404), "доступ — по правилам истории встречи"
        # администратор видит и файлы участников
        login(c, "root")
        adm = c.get(f"/api/v1/meetings/{mid}/media").json()
        assert len(adm["participants"]) == 2 and len(adm["mixes"]) == 1
        pid = adm["participants"][0]["id"]
        assert c.get(f"/api/v1/meetings/{mid}/media/{pid}/stream").status_code == 200
        audit = [a["action"] for a in c.get("/api/v1/admin/audit").json()]
        assert "recording.play" in audit and "recording.download" in audit
        # только одна общая запись и сведена она один раз: повторное открытие истории новых файлов не создаёт
        before = sorted(Path(s.recordings_path).rglob("*.m4a"))
        c.get(f"/api/v1/meetings/{mid}/media"); c.get(url)
        assert before == sorted(Path(s.recordings_path).rglob("*.m4a")) and len(before) == 1


@needs_ffmpeg
def test_media_access_is_checked_on_the_server_and_ids_cannot_be_swapped(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        login(c, "root")
        mix = c.get(f"/api/v1/meetings/{mid}/media").json()["mixes"][0]
        part = c.get(f"/api/v1/meetings/{mid}/media").json()["participants"][0]
        other = _meeting_with_recordings(c, s)
        url = f"/api/v1/meetings/{mid}/media/{mix['id']}/stream"
        login(c, "carol")                                            # сотрудник, не имеющий доступа к встрече
        assert c.get(f"/api/v1/meetings/{mid}/media").status_code == 404
        assert c.get(url).status_code == 404
        login(c, "alice")
        assert c.get(f"/api/v1/meetings/{other}/media/{mix['id']}/stream").status_code == 404, "запись другой встречи по чужому идентификатору недоступна"
        assert c.get(f"/api/v1/meetings/{mid}/media/{part['id']}/stream").status_code == 404, "файл участника обычному пользователю не отдаётся"
        assert c.get(f"/api/v1/meetings/{mid}/media/{uuid.uuid4()}/stream").status_code == 404
        c.cookies.clear()
        assert c.get(url).status_code == 401
        login(c, "bob")
        assert c.get(url + "?download=1").status_code in (403, 404), "скачивание — только администратору, руководителю и организатору"


@needs_ffmpeg
def test_recording_mode_off_creates_no_mix_and_failure_of_mixing_does_not_lose_files(tmp_path, directory, monkeypatch):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s, mode="off")
        login(c, "root")
        assert c.get(f"/api/v1/meetings/{mid}/media").json()["mixes"] == [] and len(c.get(f"/api/v1/meetings/{mid}/media").json()["participants"]) == 2
    s2 = make_settings(tmp_path / "b")
    (tmp_path / "b").mkdir()
    monkeypatch.setattr(mixdown, "ffmpeg_path", lambda: None)         # ffmpeg сломан: общая запись «failed», файлы участников целы и выгружаются
    with running_app(s2, directory) as c:
        mid = _meeting_with_recordings(c, s2)
        login(c, "root")
        m = c.get(f"/api/v1/meetings/{mid}/media").json()
        assert m["mixes"][0]["status"] == "failed" and "ffmpeg" in m["mixes"][0]["error"] and len(m["participants"]) == 2
        assert c.get(f"/api/v1/meetings/{mid}/media/{m['mixes'][0]['id']}/stream").status_code == 409


@needs_ffmpeg
def test_mix_is_served_from_the_external_storage_when_no_local_copy_is_kept(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        put_settings(c, "audio_storage", enabled=True, local_path=str(tmp_path / "arch"), keep_local_copy=False)
        mid = _meeting_with_recordings(c, s)
        assert not list(Path(s.recordings_path).rglob("*.m4a")) and list((tmp_path / "arch").rglob("*.m4a")), "локальной копии нет, файл во внешнем хранилище"
        login(c, "alice")
        mix = c.get(f"/api/v1/meetings/{mid}/media").json()["mixes"][0]
        url = f"/api/v1/meetings/{mid}/media/{mix['id']}/stream"
        r = c.get(url, headers={"Range": "bytes=0-9"})
        assert r.status_code == 206 and len(r.content) == 10 and r.content[4:8] == b"ftyp"
        assert len(c.get(url).content) == mix["size_bytes"]
        # хранилище стало недоступным — понятная ошибка, а не 500 и не путь в тексте
        import shutil

        shutil.move(str(tmp_path / "arch"), str(tmp_path / "arch-away"))
        bad = c.get(url)
        assert bad.status_code in (410, 503) and str(tmp_path) not in bad.text


# ----------------------------------------------------------------------------- прежние списки, режим видео, очистка по срокам
@needs_ffmpeg
def test_old_lists_keep_showing_only_participant_files_and_unfinished_video_mode_is_refused(tmp_path, directory):
    from .test_public_api import enable, hdr, make_client

    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        login(c, "root")
        files = c.get(f"/api/v1/meetings/{mid}/recordings").json()
        assert len(files) == 2 and all(f["identity"] != "meeting" for f in files), "список файлов участников не меняется из-за общей записи"
        allrecs = c.get("/api/v1/admin/recordings").json()
        assert sorted(r["kind"] for r in allrecs) == ["mix_audio", "participant", "participant"], "в общем списке администратора общая запись видна и отличима"
        # публичный API: общая запись не появляется в списке и не выдаётся по ссылке (контракт «файлы участников» не меняется)
        from app.publicapi import ids

        cl, key = make_client(c, name="rec", scopes=["meetings:read", "recordings:read", "recordings:download"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        listed = c.get(f"/api/public/v1/meetings/{pm}/recordings", headers=hdr(key)).json()["items"]
        assert len(listed) == 2
        mix_id = [r["id"] for r in allrecs if r["kind"] == "mix_audio"][0]
        assert c.post(f"/api/public/v1/meetings/{pm}/recordings/{ids.pub('recording', uuid.UUID(mix_id))}/download-url", headers=hdr(key)).status_code == 404
        # режим «аудио и видео» пока не принимается: пользователь не должен выбрать запись, которой не будет
        login(c, "root")
        rooms = {r["name"]: r for r in c.get("/api/v1/rooms").json()}
        rid = rooms["Совещание"]["id"]
        bad = c.patch(f"/api/v1/rooms/{rid}/manage", json={"recording_mode": "audio_video"})
        assert bad.status_code == 409 and "в разработке" in bad.text
        assert c.get(f"/api/v1/rooms/{rid}/manage").json()["recording_mode"] == "audio"
        assert c.patch(f"/api/v1/rooms/{rid}/manage", json={"recording_mode": "off"}).status_code == 200


@needs_ffmpeg
def test_retention_does_not_delete_tracks_while_the_mix_is_being_built_and_unsticks_a_dead_mix(tmp_path, directory):
    import datetime as dt

    from sqlalchemy import select, update

    from app.models import Recording, Room, utcnow
    from app.workers.retention import run_retention_once

    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        mid = _meeting_with_recordings(c, s)
        app = c.app_obj
        sm = app.state.session_maker

        async def prep(mix_age_h: float, status: str):
            async with sm() as db:
                old = utcnow() - dt.timedelta(hours=2)
                await db.execute(update(Room).values(audio_retention_days=0))
                await db.execute(update(Recording).values(created_at=old))
                await db.execute(update(Recording).where(Recording.kind == "mix_audio").values(status=status, created_at=utcnow() - dt.timedelta(hours=mix_age_h)))
                await db.commit()

        async def count():
            async with sm() as db:
                return [(r.kind, r.status) for r in (await db.execute(select(Recording))).scalars().all()]

        c.portal.call(lambda: prep(0.1, "processing"))
        stats = c.portal.call(lambda: run_retention_once(sm, app.state.protocols))
        assert stats["recordings"] == 0 and len(c.portal.call(count)) == 3, "пока идёт сведение, исходные файлы не удаляются"
        c.portal.call(lambda: prep(7, "processing"))
        stats = c.portal.call(lambda: run_retention_once(sm, app.state.protocols))
        assert stats["recordings"] == 3 and c.portal.call(count) == [], "сведение, прерванное перезапуском, не блокирует очистку навсегда"


@needs_ffmpeg
def test_reconcile_notices_a_mix_deleted_by_hand_and_playback_says_so_clearly(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        put_settings(c, "audio_storage", enabled=True, local_path=str(tmp_path / "arch"), keep_local_copy=False)
        mid = _meeting_with_recordings(c, s)
        login(c, "root")
        mix = c.get(f"/api/v1/meetings/{mid}/media").json()["mixes"][0]
        assert mix["file_state"] == "ok"
        next((tmp_path / "arch").rglob("*.m4a")).unlink()                 # файл удалили руками на файловом сервере
        rid = c.portal.call(lambda: c.app_obj.state.reconciler.run("manual", "тест", force=True))
        rep = c.get(f"/api/v1/admin/storage-sync/runs/{rid}").json()
        assert rep["missing"] >= 1
        after = c.get(f"/api/v1/meetings/{mid}/media").json()["mixes"][0]
        assert after["file_state"] == "missing"
        assert c.get(f"/api/v1/meetings/{mid}/media/{mix['id']}/stream").status_code == 410
