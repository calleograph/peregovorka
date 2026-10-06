"""Менеджер ASR-моделей: какая модель активна, переключение «загрузить новую → переключить → освободить старую», тест и сравнение.

 * Неудачная загрузка не роняет сервис и не отключает работающую модель: прежняя остаётся активной, у выбранной виден понятный статус.
 * Если выбранной модели нет на диске, ошибка называет недостающие файлы.
 * Менеджер сам выглядит как провайдер (transcribe / is_ready / info / threads), поэтому очередь инференса о смене модели не знает.
 * Тест и сравнение гоняют встроенный тестовый WAV (русская речь, синтез) с эталонным текстом и считают скорость, CPU, RAM, RTF,
   WER/CER и сохранность пунктуации; результат — на одном и том же аудио для всех моделей.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .catalog import ModelSpec, file_status
from .config import AsrSettings
from .providers.base import SAMPLE_RATE, AsrProvider, ModelInfo
from .runtimes import RUNTIMES, build_for_spec
from .scoring import cer, punctuation_scores, wer

log = logging.getLogger("asr.models")
ASSETS = Path(__file__).parent / "assets"


class ModelNotReadyError(RuntimeError):
    """Нет активной загруженной модели (выбрана отсутствующая или загрузка не удалась)."""


@dataclass
class ModelState:
    status: str = "available"   # available | loading | active | error
    error: str | None = None
    load_ms: int | None = None


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != SAMPLE_RATE:
            raise ValueError("Тестовый WAV должен быть 16 кГц, моно, 16 бит")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()


def _rss_bytes() -> int | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def _children_maxrss() -> int | None:
    try:
        import resource  # noqa: PLC0415 — только Linux/macOS

        return int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) * 1024
    except (ImportError, ValueError):
        return None


def _cpu_seconds() -> float:
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system


def summarize(results: list[dict]) -> list[str]:
    """Короткие выводы простым языком по результатам сравнения (только по успешным прогонам)."""
    ok = [r for r in results if r.get("ok")]
    if len(ok) < 2:
        return ["Для сравнения нужны минимум две установленные и работающие модели."] if len(results) > len(ok) else []
    out: list[str] = []
    fast = min(ok, key=lambda r: r["inference_ms"])
    slow = max(ok, key=lambda r: r["inference_ms"])
    if fast is not slow and slow["inference_ms"] > 0:
        out.append(f"Быстрее: {fast['title']} — {fast['inference_ms']} мс против {slow['inference_ms']} мс "
                   f"({round((1 - fast['inference_ms'] / slow['inference_ms']) * 100)} % быстрее), RTF {fast['rtf']} против {slow['rtf']}.")
    cpu = [r for r in ok if r.get("cpu_s") is not None]
    if len(cpu) >= 2:
        lo = min(cpu, key=lambda r: r["cpu_s"])
        out.append(f"Меньше процессорного времени: {lo['title']} ({lo['cpu_s']} с CPU на прогон).")
    ram = [r for r in ok if r.get("ram_mb") is not None]
    if len(ram) >= 2:
        lo = min(ram, key=lambda r: r["ram_mb"])
        out.append(f"Меньше памяти: {lo['title']} ({lo['ram_mb']} МБ).")
    best = min(ok, key=lambda r: (r["wer"], r["cer"]))
    worst = max(ok, key=lambda r: (r["wer"], r["cer"]))
    if best is not worst:
        out.append(f"Точнее по словам: {best['title']} (WER {round(best['wer'] * 100, 1)} % против {round(worst['wer'] * 100, 1)} %).")
    else:
        out.append(f"Качество слов одинаково (WER {round(best['wer'] * 100, 1)} %).")
    pf = max(ok, key=lambda r: r["punctuation"]["f1"])
    out.append(f"Пунктуация лучше у: {pf['title']} (F1 {pf['punctuation']['f1']}, знаков {pf['punctuation']['hyp_marks']} из {pf['punctuation']['ref_marks']} в эталоне).")
    return out


class ModelManager:
    def __init__(self, settings: AsrSettings, catalog: list[ModelSpec], *, builder: Callable[[ModelSpec, AsrSettings], AsrProvider] = build_for_spec,
                 busy_reason: Callable[[], str | None] = lambda: None, wav_path: Path | None = None):
        self._s = settings
        self._builder = builder
        self.catalog: dict[str, ModelSpec] = {m.id: m for m in catalog}
        self._state: dict[str, ModelState] = {m.id: ModelState() for m in catalog}
        self._provider: AsrProvider | None = None
        self.active_id: str | None = None
        self.loading_id: str | None = None
        self.failed_desired: str | None = None  # выбранная и не загрузившаяся модель: автоповтор не делаем, пока выбор не изменится
        self._busy_reason = busy_reason
        self._wav = wav_path or ASSETS / "selftest_ru.wav"
        self._lock: asyncio.Lock | None = None

    # ------------------------------------------------------------ интерфейс провайдера (для очереди)
    @property
    def info(self) -> ModelInfo:
        if self._provider is not None:
            return self._provider.info
        return ModelInfo(provider="none", name="не загружена", device=self._s.asr_device)

    @property
    def threads(self) -> dict:
        return dict(getattr(self._provider, "threads", {}) or {})

    def is_ready(self) -> bool:
        return self._provider is not None and self._provider.is_ready()

    def transcribe(self, pcm: np.ndarray, *, language: str | None = None):
        p = self._provider
        if p is None or not p.is_ready():
            raise ModelNotReadyError("Нет активной загруженной модели распознавания")
        return p.transcribe(pcm, language=language)

    def _op_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ------------------------------------------------------------------------------ состояние
    def _view(self, spec: ModelSpec) -> dict:
        fs = file_status(spec, self._s.asr_model_dir)
        st = self._state[spec.id]
        runtime_ok = spec.runtime in RUNTIMES
        status = ("active" if spec.id == self.active_id else "loading" if spec.id == self.loading_id
                  else "missing" if not fs.present else "unsupported" if not runtime_ok else "error" if st.status == "error" else "available")
        return {**spec.public(), "present": fs.present, "missing": list(fs.missing), "size_bytes": fs.size_bytes, "status": status,
                "error": st.error if status in ("error", "missing", "unsupported") or st.error else None, "load_ms": st.load_ms,
                "active": spec.id == self.active_id, "runtime_available": runtime_ok}

    def status(self) -> dict:
        return {"active_id": self.active_id, "loading_id": self.loading_id, "device": self._s.asr_device, "ready": self.is_ready(),
                "threads": self.threads, "models": [self._view(s) for s in self.catalog.values()], "test_audio_s": self._audio_seconds()}

    def _audio_seconds(self) -> float | None:
        try:
            with wave.open(str(self._wav), "rb") as w:
                return round(w.getnframes() / w.getframerate(), 1)
        except (OSError, wave.Error):
            return None

    # --------------------------------------------------------------------------- переключение
    async def activate(self, model_id: str) -> dict:
        spec = self.catalog.get(model_id)
        if spec is None:
            return {"ok": False, "error": f"Неизвестная модель «{model_id}»."}
        if model_id == self.active_id and self.is_ready():
            return {"ok": True, "active_id": model_id, "note": "уже активна"}
        fs = file_status(spec, self._s.asr_model_dir)
        st = self._state[model_id]
        if not fs.present:
            st.error = f"Файлы модели не найдены в каталоге моделей: {', '.join(fs.missing)}. Положите их туда (scripts/models.sh) — загрузка не выполняется автоматически."
            self.failed_desired = model_id
            return {"ok": False, "error": st.error, "missing": list(fs.missing)}
        async with self._op_lock():
            self.loading_id, st.error = model_id, None
            t0 = time.monotonic()
            try:
                provider = self._builder(spec, self._s)
                await asyncio.to_thread(provider.load)
            except Exception as exc:  # noqa: BLE001 — любая ошибка загрузки не должна ронять сервис
                msg = str(exc) if isinstance(exc, RuntimeError) and str(exc) else f"{type(exc).__name__}: {exc}"
                st.status, st.error = "error", msg[:600]
                self.failed_desired = model_id
                log.error("Модель не загружена; прежняя остаётся активной", extra={"model": model_id, "error": st.error})
                return {"ok": False, "error": st.error, "active_id": self.active_id}
            finally:
                self.loading_id = None
            old = self._provider
            self._provider, self.active_id = provider, model_id
            st.status, st.load_ms, self.failed_desired = "active", int((time.monotonic() - t0) * 1000), None
            for mid, s in self._state.items():
                if mid != model_id and s.status == "active":
                    s.status = "available"
            close = getattr(old, "close", None)
            if callable(close):
                close()
            del old
            gc.collect()
            log.info("Модель активирована", extra={"model": model_id, "runtime": spec.runtime, "load_ms": st.load_ms})
            return {"ok": True, "active_id": model_id, "load_ms": st.load_ms}

    # -------------------------------------------------------------------------- тест и сравнение
    def reference_text(self) -> str:
        return (self._wav.with_suffix(".txt")).read_text(encoding="utf-8").strip()

    async def _measure(self, spec: ModelSpec, repeat: int) -> dict:
        """Один прогон теста: загрузка (если модель не активна), прогрев, `repeat` замеров на эталонном аудио, оценка текста."""
        pcm = load_wav(self._wav)
        audio_s = len(pcm) / SAMPLE_RATE
        base = {"model_id": spec.id, "title": spec.title, "runtime": spec.runtime, "quant": spec.quant or "full", "device": self._s.asr_device, "audio_s": round(audio_s, 1)}
        fs = file_status(spec, self._s.asr_model_dir)
        if not fs.present:
            return {**base, "ok": False, "error": f"Нет файлов модели: {', '.join(fs.missing)}"}
        temp: AsrProvider | None = None
        rss0 = _rss_bytes()
        load_ms: int | None = None
        try:
            if spec.id == self.active_id and self._provider is not None:
                provider = self._provider
            else:
                t0 = time.monotonic()
                temp = provider = self._builder(spec, self._s)
                await asyncio.to_thread(provider.load)
                load_ms = int((time.monotonic() - t0) * 1000)
            rss_loaded = _rss_bytes()

            def run() -> dict:
                provider.transcribe(pcm)  # прогрев: первый вызов всегда медленнее
                cpu0, wall0, ch0 = _cpu_seconds(), time.monotonic(), _children_maxrss()
                text = ""
                for _ in range(max(1, repeat)):
                    text = provider.transcribe(pcm).text
                wall = time.monotonic() - wall0
                return {"text": text, "wall": wall, "cpu": _cpu_seconds() - cpu0, "ch": _children_maxrss(), "ch0": ch0, "rss": _rss_bytes()}

            m = await asyncio.to_thread(run)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc) if isinstance(exc, RuntimeError) and str(exc) else f"{type(exc).__name__}: {exc}"
            return {**base, "ok": False, "error": msg[:600]}
        finally:
            if temp is not None:
                close = getattr(temp, "close", None)
                if callable(close):
                    close()
                del temp
                gc.collect()
        n = max(1, repeat)
        infer_ms = int(m["wall"] / n * 1000)
        ref = self.reference_text()
        ram = m["rss"] or 0
        child = m["ch"] if (m["ch"] and m["ch"] > (m["ch0"] or 0) - 1) else None
        ram_bytes = max(ram, child or 0) if (ram or child) else None
        return {
            **base, "ok": True, "load_ms": load_ms, "inference_ms": infer_ms, "audio_duration_ms": int(audio_s * 1000),
            "rtf": round(infer_ms / (audio_s * 1000), 3), "cpu_s": round(m["cpu"] / n, 2),
            "cpu_cores_avg": round(m["cpu"] / m["wall"], 2) if m["wall"] > 0 else None,
            "ram_mb": round(ram_bytes / 1048576) if ram_bytes else None,
            "ram_delta_mb": round((rss_loaded - rss0) / 1048576) if (load_ms is not None and rss_loaded and rss0) else None,
            "text": m["text"], "reference": ref, "wer": round(wer(ref, m["text"]), 3), "cer": round(cer(ref, m["text"]), 3),
            "punctuation": punctuation_scores(ref, m["text"]), "repeat": n,
        }

    async def run_test(self, model_id: str | None = None, repeat: int = 3, force: bool = False) -> dict:
        spec = self.catalog.get(model_id or self.active_id or "")
        if spec is None:
            return {"ok": False, "error": "Не выбрана модель для теста."}
        busy = self._busy_reason()
        if busy and not force and spec.id != self.active_id:
            return {"ok": False, "error": busy}
        async with self._op_lock():
            res = await self._measure(spec, repeat)
        res["during_meeting"] = bool(busy)
        return res

    async def compare(self, model_ids: list[str] | None = None, repeat: int = 3, force: bool = False) -> dict:
        busy = self._busy_reason()
        if busy and not force:
            return {"ok": False, "error": busy}
        specs = [self.catalog[i] for i in (model_ids or list(self.catalog)) if i in self.catalog]
        results = []
        async with self._op_lock():
            for spec in specs:
                if not file_status(spec, self._s.asr_model_dir).present:
                    results.append({"model_id": spec.id, "title": spec.title, "runtime": spec.runtime, "ok": False, "skipped": True,
                                    "error": "Модель не установлена (нет файлов) — пропущена."})
                    continue
                results.append(await self._measure(spec, repeat))
        return {"ok": True, "audio_s": self._audio_seconds(), "reference": self.reference_text(), "results": results, "summary": summarize(results),
                "note": "Все модели прогнаны на одном и том же аудио (синтезированная русская речь). CPU — процессорное время одного прогона; RAM — размер процесса "
                        "(для внешнего runtime — максимум дочернего процесса). Не запускайте во время встреч: нагрузка искажает цифры."}
