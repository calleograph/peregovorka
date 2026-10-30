"""Этапы общей записи для интерфейса: идёт запись → файл закрывается → готово/переносится в хранилище; ошибка формата не выдаётся за временное отсутствие файла."""
from types import SimpleNamespace as NS

from app.api.media import phase_of


def rec(**kw):
    base = dict(status="ready", kind="mix_video", file_state="ok", export_status="local")
    base.update(kw)
    return NS(**base)


def test_phases_of_a_video_recording_from_start_to_finish():
    assert phase_of(rec(status="processing")) == "recording"
    assert phase_of(rec(status="finishing")) == "finalizing"
    assert phase_of(rec(status="ready", export_status="pending")) == "saving"
    assert phase_of(rec(status="ready", export_status="exported")) == "ready"
    assert phase_of(rec(status="ready", export_status="local")) == "ready"
    assert phase_of(rec(status="failed")) == "failed"
    assert phase_of(rec(status="ready", file_state="missing")) == "missing"


def test_audio_mix_that_is_being_built_is_finalizing_not_recording():
    assert phase_of(rec(kind="mix_audio", status="processing")) == "finalizing"
