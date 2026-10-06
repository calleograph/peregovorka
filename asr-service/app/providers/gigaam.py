"""Провайдер GigaAM (v3 E2E RNNT по умолчанию).

Модель грузится ОДИН раз на процесс (load) и используется всеми участниками.
Автоматической загрузки весов нет: файлы должны быть подготовлены scripts/models.sh
(закрытая сеть). Файлы: <model>.ckpt и <model>_tokenizer.model в ASR_MODEL_DIR.

Аудио подаётся тензором напрямую (минуя gigaam.load_audio, которая запускает ffmpeg
на каждый вызов). Использованы публичные части nn.Module: forward(), decoding.decode(),
head. Версия пакета зафиксирована в Dockerfile (GIGAAM_GIT_COMMIT); при загрузке
выполняется прогревочный инференс — несовместимость API обнаружится на старте,
а не на первой реплике.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .base import SAMPLE_RATE, ModelInfo, ModelNotPreparedError, TranscriptionResult

log = logging.getLogger("asr.gigaam")


class GigaAmProvider:
    def __init__(self, model_name: str, model_dir: str, device: str = "cpu", cpu_threads: int = 0, interop_threads: int = 0):
        self._name = model_name
        self._dir = Path(model_dir)
        self._device_name = device
        self._cpu_threads = cpu_threads
        self._interop_threads = interop_threads
        self.threads: dict[str, int] = {}  # фактические значения torch после применения (для /readyz и диагностики)
        self._model = None
        self._torch = None
        self._ready = False
        self._lock = threading.Lock()
        self.info = ModelInfo(provider="gigaam", name=model_name, device=device)

    def load(self) -> None:
        ckpt = self._dir / f"{self._name}.ckpt"
        tokenizer = self._dir / f"{self._name}_tokenizer.model"
        missing = [str(p) for p in (ckpt,) + ((tokenizer,) if "e2e" in self._name else ()) if not p.is_file()]
        if missing:
            raise ModelNotPreparedError(
                "Файлы модели не найдены: " + ", ".join(missing) + ". Выполните scripts/models.sh (загрузка не выполняется автоматически)."
            )
        import torch  # noqa: PLC0415 — тяжёлый импорт только при реальной загрузке
        import gigaam  # noqa: PLC0415

        if self._device_name == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("ASR_DEVICE=cuda, но CUDA недоступна в контейнере")
        self.apply_threads(torch)
        self._torch = torch
        log.info("Загрузка модели GigaAM", extra={"model": self._name, "device": self._device_name})
        self._model = gigaam.load_model(
            self._name, fp16_encoder=self._device_name != "cpu", use_flash=False,
            device=self._device_name, download_root=str(self._dir),
        )
        self._model.eval()
        # прогрев: заодно проверяет совместимость API на старте
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.int16))
        self.info = ModelInfo(provider="gigaam", name=self._name, device=self._device_name,
                              version=getattr(gigaam, "__version__", ""))
        self._ready = True
        log.info("Модель GigaAM загружена и готова")

    def apply_threads(self, torch) -> dict[str, int]:  # noqa: ANN001
        """Применить ASR_CPU_THREADS / ASR_INTEROP_THREADS и записать в журнал то, что реально получил torch."""
        if self._cpu_threads:
            torch.set_num_threads(self._cpu_threads)
        if self._interop_threads:
            try:
                torch.set_num_interop_threads(self._interop_threads)
            except RuntimeError as exc:  # inter-op пул уже создан: менять можно только до первой работы torch
                log.warning("ASR_INTEROP_THREADS не применён", extra={"error": str(exc)[:200]})
        self.threads = {"intra": int(torch.get_num_threads()), "interop": int(torch.get_num_interop_threads())}
        log.info("Потоки torch", extra={"intra_threads": self.threads["intra"], "interop_threads": self.threads["interop"],
                                        "requested_intra": self._cpu_threads, "requested_interop": self._interop_threads})
        return self.threads

    def is_ready(self) -> bool:
        return self._ready

    def transcribe(self, pcm: np.ndarray, *, language: str | None = None) -> TranscriptionResult:
        torch, model = self._torch, self._model
        if torch is None or model is None:
            raise RuntimeError("Модель не загружена")
        param = next(model.parameters())
        with torch.inference_mode():
            wav = torch.from_numpy(pcm.astype(np.float32) / 32768.0).to(param.device).to(param.dtype).unsqueeze(0)
            length = torch.full([1], wav.shape[-1], device=param.device)
            encoded, encoded_len = model.forward(wav, length)
            decoded = model.decoding.decode(model.head, encoded, encoded_len)
        text = str(decoded[0][0]).strip()
        return TranscriptionResult(text=text, language=language or "ru")
