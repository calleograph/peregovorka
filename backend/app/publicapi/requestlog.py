"""Журнал обращений к публичному API: запись пачками в фоне (не на каждый запрос) и очистка по сроку хранения."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from datetime import timedelta

from sqlalchemy import delete

from ..logging_setup import request_id_var
from ..models import ApiRequestLog, utcnow

log = logging.getLogger("app")


class RequestLogWriter:
    def __init__(self, session_maker, settings_svc, *, interval: float = 2.0, max_queue: int = 5000):
        self._sm, self._svc, self._interval = session_maker, settings_svc, interval
        self._q: deque[dict] = deque(maxlen=max_queue)      # при переполнении теряются самые старые записи: журнал не должен тормозить API
        self._task: asyncio.Task | None = None
        self._last_cleanup = 0.0

    def add(self, **row) -> None:
        rid = request_id_var.get()
        self._q.append({"at": utcnow(), "request_id": None if rid == "-" else rid[:40], **row})

    async def flush(self) -> int:
        rows: list[dict] = []
        while self._q and len(rows) < 500:
            rows.append(self._q.popleft())
        if not rows:
            return 0
        try:
            async with self._sm() as db:
                db.add_all(ApiRequestLog(**r) for r in rows)
                await db.commit()
        except Exception:  # noqa: BLE001 — потеря части журнала допустима, остановка API — нет
            log.exception("Не удалось записать журнал обращений к публичному API")
        return len(rows)

    async def cleanup(self) -> None:
        try:
            async with self._sm() as db:
                days = (await self._svc.get(db, "api")).log_retention_days
                await db.execute(delete(ApiRequestLog).where(ApiRequestLog.at < utcnow() - timedelta(days=days)))
                await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось очистить журнал обращений к публичному API")

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self._interval)
            await self.flush()
            if loop.time() - self._last_cleanup > 3600:
                self._last_cleanup = loop.time()
                await self.cleanup()

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._last_cleanup = loop.time()          # первая очистка — через час, а не сразу после запуска
        self._task = loop.create_task(self._run(), name="api-request-log")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(self._task), 3)     # остановка не должна зависеть от БД
        for _ in range(20):                       # дописать остаток очереди; не бесконечно
            if not await self.flush():
                break
