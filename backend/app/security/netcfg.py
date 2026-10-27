"""Настройки определения адреса клиента в памяти процесса: `client_ip()` синхронный и вызывается на каждый запрос, поэтому базу не трогает.
Начальное значение — из .env (прежнее поведение), затем фоновая задача раз в `REFRESH_S` читает группу «net_trust»; запись в админке обновляет текущий процесс сразу."""
from __future__ import annotations

import asyncio
import logging

from .ipres import NetCfg

log = logging.getLogger("app.security.netcfg")
REFRESH_S = 10


class NetCfgHolder:
    def __init__(self, settings):
        self._s = settings
        self.cfg = NetCfg.build(None, settings.trusted_proxy_cidrs, settings.trusted_proxy_hops)

    async def refresh(self, svc, session_maker) -> None:
        try:
            async with session_maker() as db:
                group = await svc.get(db, "net_trust")
        except Exception as exc:  # noqa: BLE001 — сбой базы не должен менять рабочие настройки на «никому не верим» или «верим всем»: остаётся прежнее значение
            log.warning("Не удалось обновить настройки определения адреса клиента (%s): действуют прежние", type(exc).__name__)
            return
        self.cfg = NetCfg.build(group, self._s.trusted_proxy_cidrs, self._s.trusted_proxy_hops)

    async def run(self, svc, session_maker) -> None:
        while True:
            await self.refresh(svc, session_maker)
            await asyncio.sleep(REFRESH_S)
