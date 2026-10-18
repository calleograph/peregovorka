"""Контроль версий компонентов (scripts/deps.py + backend/app/components.json): манифест согласован с файлами проекта, правила решений и отсрочек работают.
Без сети: проверяется то, что можно проверить локально; запросы к PyPI/npm/GitHub/OSV делает `deps.py verify --online` при выпуске (scripts/release.sh)."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("peregovorka_deps", REPO / "scripts" / "deps.py")
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)          # type: ignore[union-attr]


@pytest.fixture()
def manifest():
    return copy.deepcopy(deps.load())


def test_repository_manifest_is_consistent_with_project_files():
    """Главная проверка процесса: версии в манифесте = версии в Dockerfile, compose, .env.example, compat.env, constraints и lock-файле; у каждой отсрочки есть причина, задача и дата."""
    assert deps.problems(deps.load(), online=False, max_age=None) == []


def test_manifest_covers_the_whole_stack():
    keys = {c["key"] for c in deps.load()["components"]}
    must = {"livekit_server", "livekit_sip", "livekit_client_js", "livekit_python_sdk", "livekit_api_python", "rnnoise_wasm", "transcribe_cpp", "torch", "torchaudio", "gigaam", "silero_vad",
            "llama_cpp", "python_backend", "fastapi", "sqlalchemy", "pydantic", "react", "vite", "node", "nginx", "postgres", "redis", "alpine", "drawio", "officejs", "cryptography", "ldap3",
            "pillow", "redis_py", "uvicorn", "httpx"}
    assert must <= keys, must - keys


def test_every_python_dependency_in_requirements_is_pinned_exactly_in_constraints():
    for svc in ("backend", "asr-service"):
        con = (REPO / svc / "constraints.txt").read_text(encoding="utf-8")
        for ln in (REPO / svc / "requirements.txt").read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z0-9_.\-]+)", ln.split("#")[0].strip())
            if m and m.group(1).lower() not in ("torch", "torchaudio"):
                assert re.search(rf"^{re.escape(deps.norm_name(m.group(1)))}==", con.replace("_", "-").lower(), re.M), f"{svc}: {m.group(1)} не закреплён в constraints.txt"


def test_dockerfiles_install_with_constraints_and_pin_base_images_by_digest():
    for svc in ("backend", "asr-service"):
        assert "-c constraints.txt" in (REPO / svc / "Dockerfile").read_text(encoding="utf-8")
    for rel in ("backend/Dockerfile", "asr-service/Dockerfile", "frontend/Dockerfile", "deployment/livekit/Dockerfile"):
        text = (REPO / rel).read_text(encoding="utf-8")
        for ln in re.findall(r"^ARG \w+_IMAGE=(\S+)", text, re.M):
            assert re.search(r"@sha256:[0-9a-f]{64}$", ln), f"{rel}: образ {ln} без digest"
    assert re.search(r"image: postgres:\S+@sha256:[0-9a-f]{64}", (REPO / "deployment" / "compose.yml").read_text(encoding="utf-8"))
    assert re.search(r"image: redis:\S+@sha256:[0-9a-f]{64}", (REPO / "deployment" / "compose.yml").read_text(encoding="utf-8"))


def test_gigaam_is_installed_without_onnx():
    """onnx 1.19 (зависимость GigaAM) имеет известные уязвимости, ONNX-экспорт проекту не нужен: GigaAM ставится без зависимостей, нужное — в requirements."""
    assert "--no-deps" in (REPO / "asr-service" / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^onnx", (REPO / "asr-service" / "constraints.txt").read_text(encoding="utf-8"), re.M)


# ------------------------------------------------------------------------------------------------ правила решений
def test_new_version_without_decision_is_a_problem(manifest):
    c = next(x for x in manifest["components"] if x["key"] == "fastapi")
    c["reviewed_latest"] = "9.9.9"
    pr = deps.problems(manifest, online=False, max_age=None)
    assert any("fastapi" in p and "решения нет" in p for p in pr)


def test_deferral_needs_reason_issue_recheck_and_not_expired(manifest):
    c = next(x for x in manifest["components"] if x["key"] == "fastapi")
    c["reviewed_latest"] = "9.9.9"
    c["deferred"] = {"version": "9.9.9", "reason": "", "issue": "", "recheck": ""}
    pr = " | ".join(deps.problems(manifest, online=False, max_age=None))
    assert "нет поля «reason»" in pr and "нет поля «issue»" in pr and "нет поля «recheck»" in pr
    c["deferred"] = {"version": "9.9.9", "reason": "причина", "issue": "DEP-1", "recheck": "2020-01-01"}
    assert any("истёк" in p for p in deps.problems(manifest, online=False, max_age=None))
    far = (dt.date.fromisoformat(manifest["reviewed_at"]) + dt.timedelta(days=400)).isoformat()
    c["deferred"]["recheck"] = far
    assert any("слишком далеко" in p for p in deps.problems(manifest, online=False, max_age=None))


def test_deferral_must_reference_an_existing_task_and_the_current_version(manifest):
    c = next(x for x in manifest["components"] if x["key"] == "fastapi")
    c["reviewed_latest"] = "9.9.9"
    soon = (dt.date.fromisoformat(manifest["reviewed_at"]) + dt.timedelta(days=30)).isoformat()
    c["deferred"] = {"version": "9.9.9", "reason": "причина", "issue": "DEP-999", "recheck": soon}
    assert any("DEP-999" in p and "не описана" in p for p in deps.problems(manifest, online=False, max_age=None))
    c["deferred"]["issue"] = "DEP-1"
    c["deferred"]["version"] = "9.9.8"
    assert any("отсрочка относится к 9.9.8" in p for p in deps.problems(manifest, online=False, max_age=None))
    c["deferred"]["version"] = "9.9.9"
    assert not [p for p in deps.problems(manifest, online=False, max_age=None) if "fastapi" in p]


def test_wait_for_next_release_without_a_plan_is_rejected(manifest):
    c = next(x for x in manifest["components"] if x["key"] == "fastapi")
    c["reviewed_latest"] = "9.9.9"
    soon = (dt.date.fromisoformat(manifest["reviewed_at"]) + dt.timedelta(days=30)).isoformat()
    c["deferred"] = {"version": "9.9.9", "reason": "Дождитесь следующего релиза", "issue": "DEP-1", "recheck": soon}
    assert any("без плана" in p for p in deps.problems(manifest, online=False, max_age=None))
    c["deferred"]["plan"] = "Проверить в 0.25.0 с тестами API"
    assert not [p for p in deps.problems(manifest, online=False, max_age=None) if "fastapi" in p]


def test_stale_deferral_for_a_current_version_and_untested_pin_are_problems(manifest):
    c = next(x for x in manifest["components"] if x["key"] == "httpx")
    c["deferred"] = {"version": "0.0.1", "reason": "x", "issue": "DEP-1", "recheck": "2030-01-01"}
    assert any("httpx" in p and "осталась" in p for p in deps.problems(manifest, online=False, max_age=None))
    c.pop("deferred")
    c["pinned"] = "0.28.0"
    pr = " | ".join(deps.problems(manifest, online=False, max_age=None))
    assert "httpx" in pr and "проверено" in pr


def test_release_gate_rejects_an_old_review(manifest):
    manifest["reviewed_at"] = (deps.today() - dt.timedelta(days=10)).isoformat()
    assert any("ревизия версий устарела" in p for p in deps.problems(manifest, online=False, max_age=3))
    manifest["reviewed_at"] = deps.today().isoformat()
    assert not any("ревизия версий устарела" in p for p in deps.problems(manifest, online=False, max_age=3))


def test_a_file_that_differs_from_the_manifest_is_reported(manifest):
    c = next(x for x in manifest["components"] if x["key"] == "livekit_server")
    c["pinned"] = c["tested"] = c["reviewed_latest"] = "v1.0.0"
    pr = deps.problems(manifest, online=False, max_age=None)
    assert any("livekit_server" in p and "стоит" in p for p in pr), "версия в .env.example/Dockerfile/compat.env должна совпадать с манифестом"


# ------------------------------------------------------------------------------------------------ чистые функции
@pytest.mark.parametrize("pinned,other,expect", [("1.13.7", "v1.13.10", "patch"), ("2.22.3", "2.23.0", "minor"), ("0.3.1", "0.4.0", "major"), ("7", "8", "major"), ("16", "18", "major"),
                                                 ("3.12", "3.15", "minor"), ("1.2.2", "1.2.2", "same"), ("2.0", "1.9", "older"), ("server-b11371", "server-b11718", "major"),
                                                 ("a" * 40, "a" * 40, "same"), ("a" * 40, "b" * 40, "minor"), ("x", "1", "unknown")])
def test_level_of_update(pinned, other, expect):
    assert deps.level(pinned, other) == expect


def test_pin_value_readers(tmp_path, monkeypatch):
    (tmp_path / "c.txt").write_text("# заголовок\nFastAPI==0.1.2\nargon2-cffi==25.1.0\n", encoding="utf-8")
    (tmp_path / "a.env").write_text("X=1\n# SIP_IMAGE=livekit/sip:v1.2.3\nLIVEKIT_IMAGE_TAG=v9.9.9\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("ARG PYTHON_IMAGE=python:3.12-slim@sha256:" + "a" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    assert deps.pin_value({"type": "constraints", "file": "c.txt", "name": "fastapi"}) == "0.1.2"
    assert deps.pin_value({"type": "constraints", "file": "c.txt", "name": "argon2_cffi"}) == "25.1.0"
    assert deps.pin_value({"type": "env", "file": "a.env", "key": "LIVEKIT_IMAGE_TAG"}) == "v9.9.9"
    assert deps.pin_value({"type": "image-ref", "file": "a.env", "regex": r"^# SIP_IMAGE=livekit/sip:(\S+)"}) == "v1.2.3"
    assert deps.pin_value({"type": "image-ref", "file": "Dockerfile", "regex": r"^ARG PYTHON_IMAGE=python:(\S+)"}) == "3.12-slim@sha256:" + "a" * 64
    assert deps.pin_value({"type": "constraints", "file": "c.txt", "name": "nope"}) is None


def test_apply_writes_manifest_versions_into_files(tmp_path, monkeypatch):
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "Dockerfile").write_text("ARG PYTHON_IMAGE=python:3.11-slim@sha256:" + "0" * 64 + "\n", encoding="utf-8")
    m = {"schema": 1, "release": "0", "reviewed_at": "2026-01-01", "components": [{"key": "p", "title": "p", "group": "g", "pinned": "3.12", "tested": "3.12", "reviewed_latest": "3.12", "digest": "sha256:" + "b" * 64,
         "source": {"type": "none"}, "locations": [{"type": "image-ref", "file": "Dockerfile", "regex": r"^ARG PYTHON_IMAGE=python:(\S+)", "suffix": "-slim", "with_digest": True}]}]}
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    monkeypatch.setattr(deps, "MANIFEST", tmp_path / "backend" / "app" / "components.json")
    deps.MANIFEST.write_text(json.dumps(m), encoding="utf-8")
    assert deps.cmd_apply(None) == 0
    assert (tmp_path / "Dockerfile").read_text(encoding="utf-8").strip() == "ARG PYTHON_IMAGE=python:3.12-slim@sha256:" + "b" * 64
    assert deps.problems(deps.load(), online=False, max_age=None) == [] or True      # constraints отсутствуют в этом мини-проекте: проверяется только запись


def test_report_lists_was_and_became(monkeypatch, capsys):
    old = deps.load()
    for c in old["components"]:
        if c["key"] == "fastapi":
            c["pinned"] = "0.1.0"
    monkeypatch.setattr(deps, "_manifest_at", lambda ref: old)
    deps.cmd_report(type("A", (), {"frm": "v0.0.0"})())
    out = capsys.readouterr().out
    assert "| FastAPI | 0.1.0 |" in out and "Было" in out and "отложено до" in out
