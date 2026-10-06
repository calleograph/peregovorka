"""Выбор ASR-модели: каталог, переключение без падений, тест/сравнение на одном аудио, оценка качества, runtime GGUF."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.catalog import FULL_ID, Q5_ID, ModelSpec, default_catalog, file_status, load_catalog
from app.config import AsrSettings
from app.health import create_health_app
from app.model_manager import ASSETS, ModelManager, ModelNotReadyError, summarize
from app.providers.base import ModelInfo, ModelNotPreparedError, TranscriptionResult
from app.runtimes import RUNTIMES, GgufCliProvider, RuntimeUnavailableError, build_for_spec
from app.scoring import cer, normalize_words, punctuation_scores, wer

from .helpers import FakeProvider

REF = (ASSETS / "selftest_ru.txt").read_text(encoding="utf-8").strip()


class Echo(FakeProvider):
    """Возвращает заданный текст (для проверки оценки качества)."""

    def __init__(self, text: str, delay: float = 0.0):
        super().__init__(delay)
        self.text = text
        self.info = ModelInfo("fake", "echo", "cpu", runtime="fake", model_id="echo")

    def transcribe(self, pcm, *, language=None):
        super().transcribe(pcm, language=language)
        return TranscriptionResult(self.text, language)


def make_mgr(tmp_path: Path, builders: dict, busy=lambda: None) -> ModelManager:
    s = AsrSettings(asr_model_dir=str(tmp_path))
    cat = default_catalog()
    for spec in cat:
        for f in spec.files:
            (tmp_path / f).write_bytes(b"x" * 1000)
    return ModelManager(s, cat, builder=lambda spec, _s: builders[spec.id](), busy_reason=busy)


# ------------------------------------------------------------------------------------------- каталог
def test_default_catalog_has_full_pytorch_and_q5_gguf_with_expected_files():
    cat = {m.id: m for m in default_catalog()}
    full, q5 = cat[FULL_ID], cat[Q5_ID]
    assert (full.runtime, full.files, full.quant) == ("pytorch", ("v3_e2e_rnnt.ckpt", "v3_e2e_rnnt_tokenizer.model"), "")
    assert (q5.runtime, q5.files, q5.quant) == ("gguf", ("gigaam-v3-e2e-rnnt-Q5_K_M.gguf",), "Q5_K_M")
    assert full.public()["quant"] == "full" and "Full" in full.title and "Q5_K_M" in q5.title


def test_catalog_json_extends_without_code_changes_and_skips_bad_entries(tmp_path):
    (tmp_path / "catalog.json").write_text(json.dumps([
        {"id": "gigaam-v3-e2e-rnnt-q8_0", "title": "GigaAM — Q8_0", "runtime": "gguf", "quant": "Q8_0", "files": ["m-Q8_0.gguf"]},
        {"id": "bad id!", "runtime": "gguf", "files": ["x.gguf"]},
        {"id": "traversal", "runtime": "gguf", "files": ["../etc/passwd"]},
        {"runtime": "gguf"},
    ]), encoding="utf-8")
    ids = [m.id for m in load_catalog(tmp_path)]
    assert ids == [FULL_ID, Q5_ID, "gigaam-v3-e2e-rnnt-q8_0"]
    (tmp_path / "catalog.json").write_text("не json", encoding="utf-8")
    assert [m.id for m in load_catalog(tmp_path)] == [FULL_ID, Q5_ID]


def test_file_status_reports_missing_files_and_size(tmp_path):
    spec = default_catalog()[0]
    fs = file_status(spec, tmp_path)
    assert not fs.present and set(fs.missing) == set(spec.files)
    (tmp_path / spec.files[0]).write_bytes(b"abc")
    fs = file_status(spec, tmp_path)
    assert fs.size_bytes == 3 and fs.missing == (spec.files[1],)


# ------------------------------------------------------------------------------------------ оценка
def test_wer_cer_and_normalization():
    assert normalize_words("Ёлка, ЕЛКА!") == ["елка", "елка"]
    assert wer("Добрый день, коллеги.", "добрый день коллеги") == 0
    assert wer("раз два три четыре", "раз два три пять") == pytest.approx(0.25)
    assert wer("", "") == 0 and wer("", "слово") == 1
    assert cer("кот", "кит") == pytest.approx(1 / 3)


def test_punctuation_scores_reward_correct_places_and_punish_missing_marks():
    perfect = punctuation_scores(REF, REF)
    assert perfect["f1"] == 1.0 and perfect["hyp_marks"] == perfect["ref_marks"] > 3
    none = punctuation_scores(REF, " ".join(normalize_words(REF)))
    assert none["hyp_marks"] == 0 and none["f1"] == 0.0
    assert 0 < punctuation_scores("Да, конечно. Хорошо!", "Да, конечно хорошо!")["f1"] < 1
    # «.» и «?» — один класс конца предложения: выбор между ними не наказывается
    assert punctuation_scores("Мы идём.", "Мы идём?")["f1"] == 1.0


def test_builtin_test_audio_and_reference_are_consistent():
    from app.model_manager import load_wav

    pcm = load_wav(ASSETS / "selftest_ru.wav")
    assert 10 < len(pcm) / 16000 < 40 and int(np.abs(pcm).max()) > 1000
    assert len(normalize_words(REF)) >= 15


# -------------------------------------------------------------------------------------- менеджер
async def test_activate_switches_runtime_and_reports_state(tmp_path):
    m = make_mgr(tmp_path, {FULL_ID: lambda: Echo("full"), Q5_ID: lambda: Echo("q5")})
    assert not m.is_ready()
    with pytest.raises(ModelNotReadyError):
        m.transcribe(np.zeros(1600, dtype=np.int16))
    assert (await m.activate(FULL_ID))["ok"] and m.active_id == FULL_ID
    assert m.transcribe(np.zeros(1600, dtype=np.int16)).text == "full"
    res = await m.activate(Q5_ID)
    assert res["ok"] and m.active_id == Q5_ID and m.transcribe(np.zeros(1600, dtype=np.int16)).text == "q5"
    st = m.status()
    by = {x["id"]: x for x in st["models"]}
    assert st["active_id"] == Q5_ID and by[Q5_ID]["status"] == "active" and by[FULL_ID]["status"] == "available"
    assert by[FULL_ID]["size_bytes"] == 2000 and by[Q5_ID]["runtime"] == "gguf" and st["device"] == "cpu" and st["test_audio_s"] > 10
    assert m.info.model_id == "echo"


async def test_missing_model_gives_clear_error_and_keeps_working_model(tmp_path):
    m = make_mgr(tmp_path, {FULL_ID: lambda: Echo("full"), Q5_ID: lambda: Echo("q5")})
    await m.activate(FULL_ID)
    (tmp_path / "gigaam-v3-e2e-rnnt-Q5_K_M.gguf").unlink()
    res = await m.activate(Q5_ID)
    assert not res["ok"] and "gigaam-v3-e2e-rnnt-Q5_K_M.gguf" in res["error"]
    assert m.active_id == FULL_ID and m.is_ready(), "Full не отключается из-за отсутствия другой модели"
    st = {x["id"]: x for x in m.status()["models"]}[Q5_ID]
    assert st["status"] == "missing" and st["missing"] == ["gigaam-v3-e2e-rnnt-Q5_K_M.gguf"] and "scripts/models.sh" in st["error"]
    assert m.failed_desired == Q5_ID


async def test_failed_load_does_not_crash_or_replace_active_model(tmp_path):
    def boom():
        class Bad(Echo):
            def load(self):
                raise RuntimeUnavailableError("Runtime GGUF недоступен: бинарник не найден")
        return Bad("x")

    m = make_mgr(tmp_path, {FULL_ID: lambda: Echo("full"), Q5_ID: boom})
    await m.activate(FULL_ID)
    res = await m.activate(Q5_ID)
    assert not res["ok"] and "Runtime GGUF недоступен" in res["error"]
    assert m.active_id == FULL_ID and m.transcribe(np.zeros(1600, dtype=np.int16)).text == "full"
    assert {x["id"]: x for x in m.status()["models"]}[Q5_ID]["status"] == "error"
    assert (await m.activate("нет-такой"))["ok"] is False


async def test_start_without_any_model_stays_alive_and_not_ready(tmp_path):
    s = AsrSettings(asr_model_dir=str(tmp_path))
    m = ModelManager(s, default_catalog())
    res = await m.activate(FULL_ID)
    assert not res["ok"] and not m.is_ready() and m.status()["active_id"] is None


# ----------------------------------------------------------------------------- тест и сравнение
async def test_run_test_measures_speed_cpu_ram_quality_and_punctuation(tmp_path):
    m = make_mgr(tmp_path, {FULL_ID: lambda: Echo(REF, delay=0.02), Q5_ID: lambda: Echo(" ".join(normalize_words(REF)), delay=0.01)})
    await m.activate(FULL_ID)
    r = await m.run_test(None, repeat=2)
    assert r["ok"] and r["model_id"] == FULL_ID and r["device"] == "cpu" and r["runtime"] == "pytorch"
    for k in ("inference_ms", "audio_duration_ms", "rtf", "cpu_s", "wer", "cer", "punctuation", "text", "reference", "load_ms"):
        assert k in r, k
    assert r["wer"] == 0 and r["punctuation"]["f1"] == 1.0 and r["inference_ms"] >= 15 and r["audio_duration_ms"] > 10000
    assert r["rtf"] == pytest.approx(r["inference_ms"] / r["audio_duration_ms"], abs=0.001)


async def test_compare_runs_all_installed_models_on_same_audio_and_summarizes(tmp_path):
    m = make_mgr(tmp_path, {FULL_ID: lambda: Echo(REF, delay=0.04), Q5_ID: lambda: Echo(" ".join(normalize_words(REF)), delay=0.01)})
    await m.activate(FULL_ID)
    res = await m.compare(repeat=1)
    assert res["ok"] and [r["model_id"] for r in res["results"]] == [FULL_ID, Q5_ID]
    assert all(r["ok"] and r["audio_s"] == res["results"][0]["audio_s"] for r in res["results"])
    assert m.active_id == FULL_ID, "сравнение не переключает рабочую модель"
    text = " ".join(res["summary"])
    assert "Быстрее: GigaAM v3 e2e RNNT — Q5_K_M" in text and "Пунктуация лучше у: GigaAM v3 e2e RNNT — Full" in text and "Качество слов одинаково" in text


async def test_compare_skips_missing_models_and_is_blocked_during_meetings(tmp_path):
    m = make_mgr(tmp_path, {FULL_ID: lambda: Echo(REF), Q5_ID: lambda: Echo(REF)}, busy=lambda: "Идёт встреча")
    await m.activate(FULL_ID)
    blocked = await m.compare()
    assert not blocked["ok"] and "встреча" in blocked["error"].lower()
    forced = await m.compare(force=True, repeat=1)
    assert forced["ok"]
    (tmp_path / "gigaam-v3-e2e-rnnt-Q5_K_M.gguf").unlink()
    res = await m.compare(force=True, repeat=1)
    skipped = [r for r in res["results"] if r.get("skipped")]
    assert len(skipped) == 1 and "не установлена" in skipped[0]["error"]
    assert summarize(res["results"]) == ["Для сравнения нужны минимум две установленные и работающие модели."]


# ---------------------------------------------------------------------------------- runtime GGUF
def test_gguf_runtime_reports_clear_error_when_binary_or_file_is_missing(tmp_path):
    spec = default_catalog()[1]
    s = AsrSettings(asr_model_dir=str(tmp_path), asr_gguf_bin="definitely-not-installed-transcribe")
    p = build_for_spec(spec, s)
    with pytest.raises(ModelNotPreparedError, match="не найден"):
        p.load()  # нет файла модели
    (tmp_path / spec.files[0]).write_bytes(b"gguf")
    with pytest.raises(RuntimeUnavailableError, match="transcribe.cpp"):
        p.load()  # файл есть, движка нет


def test_gguf_cli_adapter_runs_external_command_and_parses_text(tmp_path):
    spec = default_catalog()[1]
    (tmp_path / spec.files[0]).write_bytes(b"gguf")
    script = tmp_path / "fake_transcribe.py"
    script.write_text(textwrap.dedent("""
        import sys, wave
        model, wav = sys.argv[1], sys.argv[2]
        with wave.open(wav, "rb") as w:
            n = w.getnframes()
        print("[00:00.000 --> 00:01.000] Привет, мир.")
        print(f"Кадров: {n}")
    """), encoding="utf-8")
    s = AsrSettings(asr_model_dir=str(tmp_path), asr_gguf_bin=sys.executable, asr_gguf_args=f"{script.as_posix()} {{model}} {{wav}}")
    p = GgufCliProvider(spec, s)
    p.load()
    assert p.is_ready() and p.info.runtime == "gguf" and p.info.model_id == Q5_ID
    res = p.transcribe(np.zeros(16000, dtype=np.int16))
    assert res.text == "Привет, мир. Кадров: 16000"


def test_runtimes_registry_is_extensible_and_unknown_runtime_is_explained(tmp_path):
    assert {"pytorch", "gguf"} <= set(RUNTIMES)
    with pytest.raises(RuntimeUnavailableError, match="onnx"):
        build_for_spec(ModelSpec(id="x1", title="X", runtime="onnx", files=("a.onnx",)), AsrSettings(asr_model_dir=str(tmp_path)))


# ------------------------------------------------------------------------------- HTTP-интерфейс
async def test_http_endpoints_require_token_for_changes(tmp_path):
    m = make_mgr(tmp_path, {FULL_ID: lambda: Echo(REF), Q5_ID: lambda: Echo(REF)})
    await m.activate(FULL_ID)
    app = create_health_app(lambda: {"model_loaded": True}, None, models=m, authorized=lambda t: t == "secret")
    c = TestClient(app)
    assert c.get("/models").json()["active_id"] == FULL_ID
    assert c.post("/models/activate", json={"model_id": Q5_ID}).status_code == 401
    assert c.post("/models/test", json={}).status_code == 401
    assert c.post("/models/compare", json={}).status_code == 401
    ok = c.post("/models/activate", json={"model_id": Q5_ID}, headers={"X-Internal-Token": "secret"})
    assert ok.status_code == 200 and m.active_id == Q5_ID
    bad = c.post("/models/activate", json={"model_id": "нет"}, headers={"X-Internal-Token": "secret"})
    assert bad.status_code == 409 and "Неизвестная" in bad.json()["error"]
    t = c.post("/models/test", json={"repeat": 1}, headers={"X-Internal-Token": "secret"}).json()
    assert t["ok"] and t["model_id"] == Q5_ID
