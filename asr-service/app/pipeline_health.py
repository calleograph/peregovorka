"""Состояние ПОЛНОГО конвейера транскрибации (LiveKit → Silero VAD → модель) — отдельно от «модель загружена».
Ошибка создания обработчика дорожки (например, нет onnxruntime у Silero VAD) раньше терялась в обработчике событий LiveKit, а сервис оставался «healthy» и «готов»."""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("asr.pipeline")


class PipelineHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.error: str | None = None
        self.stage: str | None = None
        self.at: float | None = None
        self.checked = False                      # хотя бы одна проверка VAD/обработчика выполнена

    def ok(self, stage: str = "vad") -> None:
        with self._lock:
            self.checked = True
            if self.error is not None and self.stage == stage:
                log.info("Конвейер транскрибации восстановлен", extra={"stage": stage})
                self.error, self.stage = None, None
            self.at = time.time()

    def fail(self, stage: str, exc: BaseException) -> None:
        msg = f"{type(exc).__name__}: {str(exc)[:200]}"
        with self._lock:
            self.checked, self.error, self.stage, self.at = True, msg, stage, time.time()
        log.error("Конвейер транскрибации не работает", extra={"stage": stage, "error": msg})

    def snapshot(self) -> dict:
        with self._lock:
            return {"ready": self.error is None, "checked": self.checked, "error": self.error, "stage": self.stage, "at": self.at}


HEALTH = PipelineHealth()
