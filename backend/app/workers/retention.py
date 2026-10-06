"""Фоновая очистка просроченных данных. Сроки текста и аудио раздельные, задаются в комнате.

 * текст: реплики и протоколы (стенограмма/краткий) встречи, завершённой раньше срока; сама встреча
   (метаданные: комната, время, участники) остаётся в истории;
 * аудио: файлы записей и строки recordings старше срока (по дате создания записи);
 * срок 0 = удалить после обработки (встреча завершена не менее RETENTION_GRACE назад — успеть выгрузить);
 * пустой срок (NULL) = бессрочно.
Файлы, уже выгруженные в хранилище (локальный каталог/SMB), приложением не удаляются — это внешнее хранилище.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Meeting, Protocol, Recording, Room, TranscriptSegment, utcnow
from ..services.protocols import ProtocolService

log = logging.getLogger("app.retention")
RETENTION_GRACE = timedelta(minutes=15)


async def run_retention_once(session_maker: async_sessionmaker[AsyncSession], protocols: ProtocolService) -> dict[str, int]:
    now = utcnow()
    stats = {"segments": 0, "protocols": 0, "recordings": 0}
    async with session_maker() as db:
        rooms = (await db.execute(select(Room))).scalars().all()
        for room in rooms:
            if room.text_retention_days is not None:
                cutoff = now - (RETENTION_GRACE if room.text_retention_days == 0 else timedelta(days=room.text_retention_days))
                ids = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_not(None),
                                                                 Meeting.ended_at < cutoff))).scalars().all()
                if ids:
                    r1 = await db.execute(delete(TranscriptSegment).where(TranscriptSegment.meeting_id.in_(ids)))
                    r2 = await db.execute(delete(Protocol).where(Protocol.meeting_id.in_(ids), Protocol.kind == "summary"))
                    stats["segments"] += r1.rowcount or 0
                    stats["protocols"] += r2.rowcount or 0
            if room.audio_retention_days is not None:
                cutoff = now - (RETENTION_GRACE if room.audio_retention_days == 0 else timedelta(days=room.audio_retention_days))
                recs = (await db.execute(select(Recording).where(Recording.room_id == room.id, Recording.created_at < cutoff))).scalars().all()
                for rec in recs:
                    await protocols.purge_recording(db, rec)
                    stats["recordings"] += 1
        await db.commit()
    if any(stats.values()):
        log.info("Очистка по срокам хранения", extra=stats)
    return stats


async def run_retention(session_maker: async_sessionmaker[AsyncSession], protocols: ProtocolService, interval: float = 3600.0) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await run_retention_once(session_maker, protocols)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очистки по срокам хранения")
        await asyncio.sleep(interval)
