"""Фоновый шаг автоматического обновления (см. services/autoupdate.py)."""
from __future__ import annotations

import asyncio
import logging

from ..services.autoupdate import AutoUpdater

log = logging.getLogger("app.autoupdate")


async def run_autoupdate(svc: AutoUpdater, interval: float = 30.0) -> None:
    while True:
        try:
            await svc.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка шага автообновления")
        await asyncio.sleep(interval)
