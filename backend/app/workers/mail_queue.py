"""Фоновая отправка писем из очереди `mail_messages`: queued → sending → sent | failed, с ограниченным числом повторов.

Работает отдельной задачей приложения; несколько экземпляров backend не мешают друг другу — письмо «захватывается» атомарным обновлением состояния.
Зависшие в `sending` дольше 10 минут (процесс остановили) возвращаются в очередь. Старые записи удаляются по сроку из настроек почты.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import MailMessage, utcnow
from ..services.mail_delivery import DeliveryService

log = logging.getLogger("app.mailqueue")
STALE_AFTER = timedelta(minutes=10)


async def process_once(sm: async_sessionmaker[AsyncSession], delivery: DeliveryService, limit: int = 5) -> int:
    """Один проход: берёт до `limit` готовых писем и отправляет. Возвращает число обработанных."""
    async with sm() as db:
        await db.execute(update(MailMessage).where(MailMessage.state == "sending", MailMessage.sending_at < utcnow() - STALE_AFTER)
                         .values(state="queued"))
        ids = (await db.execute(select(MailMessage.id).where(MailMessage.state == "queued", MailMessage.next_attempt_at <= utcnow())
                                .order_by(MailMessage.created_at).limit(limit))).scalars().all()
        claimed = []
        for mid in ids:
            res = await db.execute(update(MailMessage).where(MailMessage.id == mid, MailMessage.state == "queued").values(state="sending", sending_at=utcnow()))
            if res.rowcount == 1:
                claimed.append(mid)
        await db.commit()
    for mid in claimed:
        await delivery.deliver(mid)
    return len(claimed)


async def purge_old(sm: async_sessionmaker[AsyncSession], delivery: DeliveryService) -> int:
    async with sm() as db:
        pol = await delivery.policy(db)
        res = await db.execute(delete(MailMessage).where(MailMessage.state.in_(("sent", "failed")), MailMessage.created_at < utcnow() - timedelta(days=pol.keep_days)))
        await db.commit()
        return res.rowcount or 0


async def run_mail_queue(sm: async_sessionmaker[AsyncSession], delivery: DeliveryService, interval: float = 5.0) -> None:
    await asyncio.sleep(5)
    last_purge = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            n = await process_once(sm, delivery)
            if loop.time() - last_purge > 3600:
                last_purge = loop.time()
                await purge_old(sm, delivery)
            await asyncio.sleep(0.5 if n else interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очереди почты")
            await asyncio.sleep(interval * 2)
