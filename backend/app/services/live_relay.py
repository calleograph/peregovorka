"""Клиент службы ретрансляции (`live-relay`): площадки получают поток из MediaMTX отдельными процессами, а не из Egress. Недоступна служба — эфир идёт как раньше, напрямую."""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("app")
_TTL = 5.0


class RelayClient:
    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token
        self._ok: tuple[float, bool] = (0.0, False)

    def _h(self) -> dict:
        return {"X-Relay-Token": self.token}

    async def available(self) -> bool:
        if time.monotonic() - self._ok[0] < _TTL:
            return self._ok[1]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1.5)) as c:
                ok = (await c.get(f"{self.base}/health")).status_code == 200
        except httpx.HTTPError:
            ok = False
        self._ok = (time.monotonic(), ok)
        return ok

    async def put(self, rid: str, src: str, url: str, audio: bool = False) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as c:
                return (await c.put(f"{self.base}/relays/{rid}", json={"src": src, "url": url, "audio": audio}, headers=self._h())).status_code == 204
        except httpx.HTTPError:
            return False

    async def delete(self, rid: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as c:
                return (await c.delete(f"{self.base}/relays/{rid}", headers=self._h())).status_code == 204
        except httpx.HTTPError:
            return False

    async def status(self) -> dict[str, dict] | None:
        """Состояние всех площадок по их идентификаторам; None — служба не ответила (состояния не трогаем)."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as c:
                r = await c.get(f"{self.base}/relays", headers=self._h())
            return {x["id"]: x for x in r.json()} if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
