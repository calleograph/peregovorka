"""GGUF-модель через Python-привязки transcribe.cpp: подставная библиотека, проверяется только наша обвязка (загрузка, пул сессий, ошибки)."""
from __future__ import annotations

import sys
import threading
import time
import types

import numpy as np
import pytest

from app.catalog import default_catalog
from app.config import AsrSettings
from app.providers.base import ModelNotPreparedError
from app.runtimes import GgufCliProvider, GgufNativeProvider, RuntimeUnavailableError, build_for_spec


def fake_lib(*, fail_load: bool = False, delay: float = 0.0):
    calls = {"sessions": 0, "runs": 0, "threads": [], "langs": [], "max_parallel": 0, "closed": 0}
    lock = threading.Lock()
    busy = {"n": 0}

    class Result:
        def __init__(self, text):
            self.text = text

    class Session:
        def __init__(self):
            self._in_use = False

        def run(self, pcm, *, language=None, **kw):
            assert pcm.dtype == np.float32 and pcm.ndim == 1 and np.abs(pcm).max() <= 1.0
            assert not self._in_use, "одна сессия не должна использоваться двумя потоками сразу"
            self._in_use = True
            with lock:
                busy["n"] += 1
                calls["max_parallel"] = max(calls["max_parallel"], busy["n"])
                calls["runs"] += 1
                calls["langs"].append(language)
            time.sleep(delay)
            with lock:
                busy["n"] -= 1
            self._in_use = False
            return Result(f"  текст {len(pcm)}  ")

        def close(self):
            calls["closed"] += 1

    class Model:
        def __init__(self, path, *, backend="auto", device=None):
            if fail_load:
                raise RuntimeError("bad gguf")
            self.path, self.backend = path, backend

        def session(self, *, n_threads=0, **kw):
            calls["sessions"] += 1
            calls["threads"].append(n_threads)
            return Session()

        def close(self):
            calls["closed"] += 1

    return types.SimpleNamespace(Model=Model), calls


def spec():
    return default_catalog()[1]


def settings(tmp_path, **kw):
    return AsrSettings(asr_model_dir=str(tmp_path), **kw)


def put_model(tmp_path):
    (tmp_path / spec().files[0]).write_bytes(b"gguf")


def test_native_runtime_loads_once_warms_up_and_transcribes(tmp_path, monkeypatch):
    lib, calls = fake_lib()
    monkeypatch.setitem(sys.modules, "transcribe_cpp", lib)
    put_model(tmp_path)
    p = build_for_spec(spec(), settings(tmp_path, asr_cpu_threads=3, asr_language="ru"))
    assert isinstance(p, GgufNativeProvider)
    p.load()
    assert p.is_ready() and calls["sessions"] == 1 and calls["threads"] == [3] and calls["runs"] == 1, "один прогрев при загрузке"
    res = p.transcribe(np.ones(16000, dtype=np.int16))
    assert res.text == "текст 16000" and res.language == "ru" and calls["langs"][-1] == "ru"
    assert p.info.runtime == "gguf" and p.info.quant == "Q5_K_M" and p.threads["intra"] == 3
    p.close()
    assert not p.is_ready() and calls["closed"] == 2


def test_missing_file_and_missing_library_are_explained(tmp_path, monkeypatch):
    lib, _ = fake_lib()
    monkeypatch.setitem(sys.modules, "transcribe_cpp", lib)
    p = GgufNativeProvider(spec(), settings(tmp_path))
    with pytest.raises(ModelNotPreparedError, match="не найден"):
        p.load()
    put_model(tmp_path)
    monkeypatch.setitem(sys.modules, "transcribe_cpp", None)  # импорт невозможен
    with pytest.raises(RuntimeUnavailableError, match="transcribe-cpp"):
        GgufNativeProvider(spec(), settings(tmp_path)).load()


def test_broken_model_file_does_not_crash_the_service(tmp_path, monkeypatch):
    lib, calls = fake_lib(fail_load=True)
    monkeypatch.setitem(sys.modules, "transcribe_cpp", lib)
    put_model(tmp_path)
    p = GgufNativeProvider(spec(), settings(tmp_path))
    with pytest.raises(RuntimeUnavailableError, match="Не удалось загрузить GGUF"):
        p.load()
    assert not p.is_ready()


def test_parallel_calls_use_separate_sessions(tmp_path, monkeypatch):
    lib, calls = fake_lib(delay=0.05)
    monkeypatch.setitem(sys.modules, "transcribe_cpp", lib)
    put_model(tmp_path)
    p = GgufNativeProvider(spec(), settings(tmp_path, asr_max_concurrent_inference=2))
    p.load()
    assert calls["sessions"] == 2
    ts = [threading.Thread(target=p.transcribe, args=(np.zeros(8000, dtype=np.int16),)) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert calls["max_parallel"] == 2, "не больше двух одновременно (по числу сессий) и никакая сессия не делится"


def test_factory_prefers_native_binding_and_falls_back_to_cli(tmp_path, monkeypatch):
    lib, _ = fake_lib()
    monkeypatch.setitem(sys.modules, "transcribe_cpp", lib)
    assert isinstance(build_for_spec(spec(), settings(tmp_path)), GgufNativeProvider)
    # привязок нет, но внешняя команда существует — запасной вариант
    monkeypatch.delitem(sys.modules, "transcribe_cpp")
    monkeypatch.setattr("app.runtimes._native_available", lambda: False)
    assert isinstance(build_for_spec(spec(), settings(tmp_path, asr_gguf_bin=sys.executable)), GgufCliProvider)
    # ни привязок, ни команды — родное сообщение «установите пакет», а не «нет transcribe»
    monkeypatch.setitem(sys.modules, "transcribe_cpp", None)
    p = build_for_spec(spec(), settings(tmp_path, asr_gguf_bin="definitely-missing-binary"))
    assert isinstance(p, GgufNativeProvider)
    put_model(tmp_path)
    with pytest.raises(RuntimeUnavailableError, match="transcribe-cpp"):
        p.load()
