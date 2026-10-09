"""Раздаёт выбор ASR-модели из админки (БД) сервису распознавания через Redis (`asr:desired_model`).

Выбор хранится централизованно в настройках, а не в compose: при перезапуске ASR или Redis ключ восстанавливается этим воркером.
"""
from __future__ import annotations

import asyncio
import json
import logging

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..services.settings import RETIRED_ASR_MODELS, SettingsService

log = logging.getLogger("app.asr_sync")
DESIRED_MODEL_KEY = "asr:desired_model"
VAD_CONFIG_KEY = "asr:vad_config"


async def publish_desired(db: AsyncSession, svc: SettingsService, redis: Redis) -> str:
    """Записать выбранную модель в Redis. Пустой выбор (модель по умолчанию из .env) ключ удаляет."""
    cfg = await svc.get(db, "asr")
    model = cfg.active_model  # type: ignore[attr-defined]
    if model in RETIRED_ASR_MODELS:
        model = ""            # квантованная модель снята с вооружения: работаем на штатной полной
    vad = {k.removeprefix("vad_"): getattr(cfg, k) for k in ("vad_threshold", "vad_end_silence_ms", "vad_min_speech_ms", "vad_pad_ms", "vad_max_segment_seconds")
           if getattr(cfg, k, None) is not None}
    if vad:
        await redis.set(VAD_CONFIG_KEY, json.dumps(vad))
    else:
        await redis.delete(VAD_CONFIG_KEY)
    if model:
        await redis.set(DESIRED_MODEL_KEY, model)
    else:
        await redis.delete(DESIRED_MODEL_KEY)
    return model


async def run_asr_sync(session_maker: async_sessionmaker[AsyncSession], svc: SettingsService, redis: Redis, interval: float = 30.0) -> None:
    while True:
        try:
            async with session_maker() as db:
                await publish_desired(db, svc, redis)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("Не удалось опубликовать выбор модели ASR", exc_info=True)
        await asyncio.sleep(interval)
