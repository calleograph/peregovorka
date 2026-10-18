"""Готовность распознавания речи по «пульсу» ASR: отдельно МОДЕЛЬ (GigaAM загружена) и ПОЛНЫЙ КОНВЕЙЕР (LiveKit → Silero VAD → модель).
Готовая модель не доказывает работу конвейера: так, без onnxruntime у Silero VAD модель оставалась готовой, а стенограмма не появлялась (0.24.1)."""
from __future__ import annotations


def model_ready(hb: dict | None) -> bool:
    return bool(hb and hb.get("model_loaded"))


def pipeline_error(hb: dict | None) -> str | None:
    return ((hb or {}).get("pipeline") or {}).get("error") or None


def transcription_ready(hb: dict | None) -> bool:
    """Транскрибация работает: модель загружена и конвейер не сообщает об ошибке."""
    return model_ready(hb) and pipeline_error(hb) is None


def state(hb: dict | None) -> dict:
    return {"model_ready": model_ready(hb), "pipeline_ready": pipeline_error(hb) is None and bool(hb), "pipeline_error": pipeline_error(hb), "ok": transcription_ready(hb)}
