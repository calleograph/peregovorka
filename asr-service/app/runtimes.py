"""Runtime-движки ASR: как именно запускается модель. Регистрируются по имени; менеджер моделей знает только это имя.

Добавить новый runtime (ONNX и др.) — написать фабрику `(spec, settings) -> AsrProvider` и зарегистрировать её декоратором.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import shlex
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .catalog import ModelSpec
from .config import AsrSettings
from .providers.base import SAMPLE_RATE, AsrProvider, ModelInfo, ModelNotPreparedError, TranscriptionResult

log = logging.getLogger("asr.runtimes")

RuntimeFactory = Callable[[ModelSpec, AsrSettings], AsrProvider]
RUNTIMES: dict[str, RuntimeFactory] = {}


class RuntimeUnavailableError(ModelNotPreparedError):
    """Файлы модели есть, но движок (runtime) в этом образе недоступен."""


def register_runtime(name: str) -> Callable[[RuntimeFactory], RuntimeFactory]:
    def deco(fn: RuntimeFactory) -> RuntimeFactory:
        RUNTIMES[name] = fn
        return fn
    return deco


def build_for_spec(spec: ModelSpec, settings: AsrSettings) -> AsrProvider:
    factory = RUNTIMES.get(spec.runtime)
    if factory is None:
        raise RuntimeUnavailableError(f"Runtime «{spec.runtime}» не поддерживается этой версией ASR (доступны: {', '.join(sorted(RUNTIMES))}).")
    return factory(spec, settings)


@register_runtime("pytorch")
def _pytorch(spec: ModelSpec, s: AsrSettings) -> AsrProvider:
    from .providers.gigaam import GigaAmProvider  # noqa: PLC0415 — тяжёлый импорт только при выборе этого runtime

    return GigaAmProvider(str(spec.params.get("model_name", s.asr_model_name)), s.asr_model_dir, s.asr_device, s.asr_cpu_threads,
                          s.asr_interop_threads, model_id=spec.id, quant=spec.quant)


class GgufNativeProvider:
    """GGUF через Python-привязки transcribe.cpp (пакет `transcribe-cpp`, готовые колёса для Linux/Windows/macOS).

    Модель загружается ОДИН раз и остаётся в памяти (как PyTorch-модель), поэтому нет запуска процесса на каждую реплику.
    Для параллельных распознаваний создаётся пул сессий (по числу ASR_MAX_CONCURRENT_INFERENCE): сессия не потокобезопасна,
    а модель разделяется между ними. Проверено на реальной модели gigaam-v3-e2e-rnnt-Q5_K_M.gguf.
    """

    def __init__(self, spec: ModelSpec, s: AsrSettings):
        self._spec = spec
        self._path = Path(s.asr_model_dir) / spec.files[0]
        self._threads = s.asr_cpu_threads  # 0 = все ядра (по умолчанию библиотеки)
        self._sessions_n = max(1, int(s.asr_max_concurrent_inference))
        self._language = s.asr_language or "ru"
        self._backend = "cpu" if s.asr_device == "cpu" else "auto"
        self._model = None
        self._pool: queue.Queue = queue.Queue()
        self._sessions: list = []
        self._ready = False
        self.threads = {"intra": self._threads, "interop": 0}
        self.model_id, self.runtime, self.quant = spec.id, "gguf", spec.quant
        self.info = ModelInfo(provider="gguf", name=spec.title, device=spec.device, runtime="gguf", model_id=spec.id, quant=spec.quant)

    def load(self) -> None:
        if not self._path.is_file():
            raise ModelNotPreparedError(f"Файл модели не найден: {self._path.name}. Положите его в каталог моделей (scripts/models.sh --gguf).")
        try:
            import transcribe_cpp  # noqa: PLC0415 — тяжёлая нативная библиотека, только при выборе этой модели
        except ImportError as exc:
            raise RuntimeUnavailableError(f"Пакет transcribe-cpp не установлен в образе ASR ({exc}). Пересоберите образ: scripts/update.sh --force-build.") from None
        try:
            self._model = transcribe_cpp.Model(str(self._path), backend=self._backend)
            for _ in range(self._sessions_n):
                sess = self._model.session(n_threads=self._threads)
                self._sessions.append(sess)
                self._pool.put(sess)
        except Exception as exc:  # noqa: BLE001 — нативная библиотека: любой сбой — понятное сообщение, а не падение сервиса
            self.close()
            raise RuntimeUnavailableError(f"Не удалось загрузить GGUF-модель ({type(exc).__name__}: {str(exc)[:200]}).") from None
        self._ready = True
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.int16))  # прогрев: первый вызов инициализирует буферы

    def is_ready(self) -> bool:
        return self._ready

    def transcribe(self, pcm: np.ndarray, *, language: str | None = None) -> TranscriptionResult:
        if self._model is None:
            raise RuntimeError("GGUF-модель не загружена")
        audio = pcm.astype(np.float32) / 32768.0 if pcm.dtype != np.float32 else pcm
        sess = self._pool.get()
        try:
            res = sess.run(np.ascontiguousarray(audio), language=language or self._language)
        finally:
            self._pool.put(sess)
        return TranscriptionResult(text=(res.text or "").strip(), language=language or self._language)

    def close(self) -> None:
        for sess in self._sessions:
            try:
                sess.close()
            except Exception:  # noqa: BLE001
                pass
        self._sessions.clear()
        self._pool = queue.Queue()
        model, self._model = self._model, None
        try:
            if model is not None:
                model.close()
        except Exception:  # noqa: BLE001
            pass
        self._ready = False


class GgufCliProvider:
    """GGUF через внешний исполняемый файл (transcribe.cpp) — адаптер командной строки.

    ВНИМАНИЕ: точный интерфейс transcribe.cpp на момент написания не проверялся (бинарник в образ не входит). Команда и аргументы
    задаются переменными ASR_GGUF_BIN / ASR_GGUF_ARGS (шаблон с подстановками {model} {wav} {threads}); вывод — текст в stdout.
    Если бинарник не найден, модель получает статус «runtime недоступен» с понятным сообщением — сервис не падает.
    Запуск процесса на каждый сегмент медленнее резидентного движка: при наличии python-привязки/серверного режима реализуйте
    отдельную фабрику runtime и зарегистрируйте её (интерфейс админки не меняется).
    """

    def __init__(self, spec: ModelSpec, s: AsrSettings):
        self._spec = spec
        self._path = Path(s.asr_model_dir) / spec.files[0]
        self._bin = s.asr_gguf_bin
        self._args = s.asr_gguf_args
        self._threads = s.asr_cpu_threads or (os.cpu_count() or 2)
        self._timeout = float(spec.params.get("timeout", 60))
        self._ready = False
        self.threads = {"intra": self._threads, "interop": 0}
        self.model_id, self.runtime, self.quant = spec.id, "gguf", spec.quant
        self.info = ModelInfo(provider="gguf", name=spec.title, device=spec.device, runtime="gguf", model_id=spec.id, quant=spec.quant)

    def load(self) -> None:
        if not self._path.is_file():
            raise ModelNotPreparedError(f"Файл модели не найден: {self._path.name}. Положите его в каталог моделей (scripts/models.sh).")
        if shutil.which(self._bin) is None and not Path(self._bin).is_file():
            raise RuntimeUnavailableError(
                f"Runtime GGUF недоступен: исполняемый файл «{self._bin}» (transcribe.cpp) не найден в образе ASR. Установите его в образ или "
                "задайте ASR_GGUF_BIN; пока используйте модель Full (PyTorch).")
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.int16))  # прогрев/проверка запуска
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    def _command(self, wav_path: str) -> list[str]:
        parts = shlex.split(self._args)
        return [self._bin] + [p.format(model=str(self._path), wav=wav_path, threads=self._threads) for p in parts]

    def transcribe(self, pcm: np.ndarray, *, language: str | None = None) -> TranscriptionResult:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="asr-")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm.astype("<i2", copy=False).tobytes())
            proc = subprocess.run(self._command(wav_path), capture_output=True, timeout=self._timeout, check=False,  # noqa: S603
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            if proc.returncode != 0:
                raise RuntimeError(f"transcribe завершился с кодом {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[-200:]}")
            lines = [re.sub(r"^\s*\[[^\]]*\]\s*", "", ln).strip() for ln in proc.stdout.decode("utf-8", "replace").splitlines()]
            return TranscriptionResult(text=" ".join(x for x in lines if x).strip(), language=language or "ru")
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def _native_available() -> bool:
    try:
        import importlib.util  # noqa: PLC0415

        return importlib.util.find_spec("transcribe_cpp") is not None
    except (ImportError, ValueError):
        return False


@register_runtime("gguf")
def _gguf(spec: ModelSpec, s: AsrSettings) -> AsrProvider:
    """Основной путь — Python-привязки transcribe.cpp (модель резидентна). Внешний бинарник — запасной вариант, если привязок нет, а команда задана."""
    if _native_available() or not (shutil.which(s.asr_gguf_bin) or Path(s.asr_gguf_bin).is_file()):
        return GgufNativeProvider(spec, s)
    return GgufCliProvider(spec, s)
