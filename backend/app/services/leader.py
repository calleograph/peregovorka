"""«Ведущий» экземпляр backend для фоновых задач. При нескольких экземплярах за балансировщиком HTTP обслуживают все, а одиночные фоновые циклы (сведение записей, очереди писем,
вебхуки, задания API, ретенция, автообновление, синхронизация хранилищ) выполняет только тот, кто держит аренду в Redis: дублей заданий и писем не возникает.
Аренда продлевается; если ведущий пропал, через `lease_s` секунд задачи подхватит другой экземпляр. Один экземпляр всегда ведущий — поведение прежнее.
shortcut: продление без Lua (GET+EXPIRE): в редчайшем окне между проверкой и продлением возможен краткий двойной ход ведущего — задачи идемпотентны (блокировки строк/состояний); при необходимости заменить на Lua.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("app.leader")
KEY = "peregovorka:background-leader"


class Leader:
    def __init__(self, redis: Any, lease_s: float = 30, instance_id: str | None = None, poll_s: float | None = None):
        # узел и процесс в идентификаторе: в «Производительности» и в Redis видно, какой контейнер и процесс сейчас ведущий
        self._r, self.lease_s = redis, (max(10, lease_s) if poll_s is None else lease_s)
        self.id = instance_id or f"{socket.gethostname()[:24]}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._poll = poll_s or self.lease_s / 3
        self._items: list[tuple[str, Callable[[], Any], Callable[[], Awaitable[None] | None] | None]] = []
        self._running: dict[str, Any] = {}
        self.is_leader = False
        self._task: asyncio.Task | None = None

    def register(self, name: str, start: Callable[[], Any], stop: Callable[[], Awaitable[None] | None] | None = None) -> None:
        """start() запускает задачу (корутинную функцию вызывать как asyncio.create_task сам); stop() — остановка (по умолчанию отмена задачи)."""
        self._items.append((name, start, stop))

    async def _acquire(self) -> bool:
        try:
            if await self._r.set(KEY, self.id, nx=True, px=int(self.lease_s * 1000)):
                return True
            if await self._r.get(KEY) == self.id:
                await self._r.pexpire(KEY, int(self.lease_s * 1000))
                return True
        except Exception:  # noqa: BLE001 — Redis недоступен: не лидер (новые фоновые задачи не стартуют, уже идущие доработают)
            log.warning("Не удалось обновить аренду ведущего экземпляра", exc_info=True)
            return self.is_leader
        return False

    async def _start_all(self) -> None:
        for name, start, _stop in self._items:
            if name not in self._running:
                self._running[name] = start()
        log.info("Экземпляр стал ведущим для фоновых задач", extra={"instance": self.id, "tasks": len(self._items)})

    async def _stop_all(self) -> None:
        for name, _start, stop in reversed(self._items):
            handle = self._running.pop(name, None)
            if handle is None:
                continue
            if stop is not None:
                res = stop()
                if inspect.isawaitable(res):
                    with contextlib.suppress(Exception):
                        await res
            elif isinstance(handle, asyncio.Task):
                handle.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await handle

    async def run(self) -> None:
        try:
            while True:
                lead = await self._acquire()
                if lead and not self.is_leader:
                    self.is_leader = True
                    await self._start_all()
                elif not lead and self.is_leader:
                    self.is_leader = False
                    log.warning("Экземпляр потерял роль ведущего: фоновые задачи остановлены", extra={"instance": self.id})
                    await self._stop_all()
                await asyncio.sleep(self._poll)
        finally:
            was = self.is_leader
            self.is_leader = False
            await self._stop_all()
            if was:
                with contextlib.suppress(Exception):
                    if await self._r.get(KEY) == self.id:
                        await self._r.delete(KEY)               # быстрая передача роли при штатной остановке
