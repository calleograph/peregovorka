"""Живые события приложения: Redis pub/sub → WebSocket.

Один общий подписчик Redis на процесс (`EventHub`), а не отдельное соединение и опрос на каждый WebSocket: при тысяче зрителей презентации
это одно соединение вместо тысячи и одна разборка каждого сообщения вместо тысячи. У каждого WebSocket своя ОГРАНИЧЕННАЯ очередь:
медленный получатель не копит память бесконечно — при переполнении его соединение закрывается (код 4408), клиент переподключается и
догружает состояние сам (как после обрыва сети).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from redis.asyncio import Redis

log = logging.getLogger("app.events")

QUEUE_MAX = 512                  # сообщений в очереди одного WebSocket; больше — получатель слишком медленный
SLOW_CLOSE_CODE = 4408


def channel(meeting_id: uuid.UUID | str) -> str:
    return f"meeting:{meeting_id}:events"


async def publish(redis: Redis, meeting_id: uuid.UUID | str, event: dict) -> None:
    await redis.publish(channel(meeting_id), json.dumps(event, ensure_ascii=False, default=str))


class Subscriber:
    """Получатель событий (один WebSocket). `queue.get()` возвращает текст сообщения; `None` — получатель отключён хабом (переполнение)."""

    __slots__ = ("queue", "leader_channels", "channels", "dropped")

    def __init__(self, maxsize: int = QUEUE_MAX) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=maxsize + 1)       # +1: место под метку «отключён» при переполнении
        self.leader_channels: set[str] = set()          # каналы, где получатель — руководитель (ему отдаются события `leaders_only`)
        self.channels: set[str] = set()
        self.dropped = False


class EventHub:
    def __init__(self, redis: Redis, *, queue_max: int = QUEUE_MAX) -> None:
        self._r = redis
        self._queue_max = queue_max
        self._pubsub = None
        self._subs: dict[str, set[Subscriber]] = {}
        self._task: asyncio.Task | None = None
        self.stats = {"messages": 0, "delivered": 0, "slow_dropped": 0}

    # ------------------------------------------------------------------------------------------ жизненный цикл
    def start(self) -> None:
        if self._task is None:
            self._pubsub = self._r.pubsub()
            self._task = asyncio.create_task(self._run(), name="event-hub")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None

    # ------------------------------------------------------------------------------------------ подписки
    def new_subscriber(self) -> Subscriber:
        return Subscriber(self._queue_max)

    async def subscribe(self, sub: Subscriber, ch: str, *, leader: bool = False) -> None:
        if leader:
            sub.leader_channels.add(ch)
        group = self._subs.setdefault(ch, set())
        first = not group
        group.add(sub)
        sub.channels.add(ch)
        if first and self._pubsub is not None:
            await self._pubsub.subscribe(ch)

    async def unsubscribe(self, sub: Subscriber, ch: str) -> None:
        sub.channels.discard(ch)
        sub.leader_channels.discard(ch)
        group = self._subs.get(ch)
        if group is None:
            return
        group.discard(sub)
        if not group:
            self._subs.pop(ch, None)
            if self._pubsub is not None:
                with contextlib.suppress(Exception):
                    await self._pubsub.unsubscribe(ch)

    async def release(self, sub: Subscriber) -> None:
        for ch in list(sub.channels):
            await self.unsubscribe(sub, ch)

    # ------------------------------------------------------------------------------------------ доставка
    def _deliver(self, ch: str, text: str) -> None:
        group = self._subs.get(ch)
        if not group:
            return
        leaders_only = False
        if "leaders_only" in text:           # закрытая доска: схема и её изменения не уходят никому, кроме руководителей
            try:
                leaders_only = bool(json.loads(text).get("leaders_only"))
            except ValueError:
                pass
        for sub in list(group):
            if sub.dropped or (leaders_only and ch not in sub.leader_channels):
                continue
            if sub.queue.qsize() >= self._queue_max:
                # получатель не успевает: очередь очищается, остаётся метка «отключён»; WebSocket закроется, клиент догрузит состояние сам
                sub.dropped = True
                self.stats["slow_dropped"] += 1
                while not sub.queue.empty():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        sub.queue.get_nowait()
                sub.queue.put_nowait(None)
                continue
            sub.queue.put_nowait(text)
            self.stats["delivered"] += 1

    async def _run(self) -> None:
        while True:
            try:
                pubsub = self._pubsub
                if pubsub is None or not pubsub.subscribed:
                    await asyncio.sleep(0.2)
                    continue
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    raw, ch = msg["data"], msg.get("channel")
                    self.stats["messages"] += 1
                    self._deliver(ch.decode() if isinstance(ch, bytes) else ch, raw.decode() if isinstance(raw, bytes) else raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — обрыв Redis не должен останавливать рассылку навсегда
                log.warning("Сбой чтения событий Redis", extra={"error": type(exc).__name__})
                await asyncio.sleep(1.0)

    def snapshot(self) -> dict:
        """Для диагностики: сколько каналов и получателей, сколько сообщений доставлено и сколько медленных получателей отключено."""
        subs = {s for g in self._subs.values() for s in g}
        return {"channels": len(self._subs), "subscribers": len(subs), **self.stats, "queued": sum(s.queue.qsize() for s in subs)}
