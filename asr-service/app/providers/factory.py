from __future__ import annotations

from ..config import AsrSettings
from .base import AsrProvider


def build_provider(settings: AsrSettings) -> AsrProvider:
    """Единственное место, знающее о конкретных провайдерах. Новый ASR — новая ветка здесь."""
    name = settings.asr_provider.lower()
    if name == "gigaam":
        from .gigaam import GigaAmProvider

        return GigaAmProvider(settings.asr_model_name, settings.asr_model_dir, settings.asr_device, settings.asr_cpu_threads)
    raise ValueError(f"Неизвестный ASR_PROVIDER: {settings.asr_provider!r} (поддерживается: gigaam)")
