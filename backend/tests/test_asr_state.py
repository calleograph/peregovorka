"""Регрессия 0.24.1: «модель загружена» ≠ «транскрибация работает»."""
from app.services import asr_state


def test_model_ready_but_pipeline_broken_is_not_ready():
    hb = {"model_loaded": True, "pipeline": {"ready": False, "error": "ModuleNotFoundError: No module named 'onnxruntime'"}}
    s = asr_state.state(hb)
    assert s["model_ready"] is True and s["ok"] is False and "onnxruntime" in s["pipeline_error"]


def test_all_good_and_no_heartbeat():
    assert asr_state.state({"model_loaded": True, "pipeline": {"ready": True, "error": None}})["ok"] is True
    assert asr_state.state(None)["ok"] is False
