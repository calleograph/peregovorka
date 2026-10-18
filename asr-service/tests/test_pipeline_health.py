"""Регрессия 0.24.1: ошибка создания обработчика дорожки (нет onnxruntime у Silero VAD) должна переводить конвейер в состояние ошибки, а не оставлять «готово»."""
import pytest

from app.pipeline_health import PipelineHealth


def test_failure_is_recorded_with_cause_and_recovers():
    h = PipelineHealth()
    assert h.snapshot()["ready"] is True
    h.fail("vad", ModuleNotFoundError("No module named 'onnxruntime'"))
    s = h.snapshot()
    assert s["ready"] is False and "onnxruntime" in s["error"] and s["stage"] == "vad"
    h.ok("vad")
    assert h.snapshot()["ready"] is True


def test_selfcheck_reports_vad_failure(monkeypatch):
    from app import selfcheck

    def boom(*a, **k):
        raise ModuleNotFoundError("No module named 'onnxruntime'")

    pytest.importorskip("numpy")
    monkeypatch.setattr("app.audio.vad.SileroVad", boom, raising=False)
    assert "onnxruntime" in selfcheck.vad_problem()


def test_readyz_503_when_pipeline_broken():
    from fastapi.testclient import TestClient

    from app.health import create_health_app

    snap = {"model_loaded": True, "pipeline": {"ready": False, "error": "x"}}
    r = TestClient(create_health_app(lambda: snap, None, models=None, authorized=lambda t: True)).get("/readyz")
    assert r.status_code == 503
