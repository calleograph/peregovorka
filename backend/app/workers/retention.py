"""Фоновая очистка просроченных данных. Сроки текста и аудио раздельные, задаются в комнате.

 * текст: реплики, сообщения чата, доска (схема) и протоколы (стенограмма/краткий) встречи, завершённой раньше срока; сама встреча
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

from ..models import Meeting, MeetingChatMessage, MeetingWhiteboard, Protocol, Recording, Room, TranscriptSegment, utcnow
from ..services import retention_policy
from ..services.protocols import ProtocolService

log = logging.getLogger("app.retention")
RETENTION_GRACE = timedelta(minutes=15)
MIX_STALE = timedelta(hours=6)


async def run_retention_once(session_maker: async_sessionmaker[AsyncSession], protocols: ProtocolService) -> dict[str, int]:
    now = utcnow()
    stats = {"segments": 0, "protocols": 0, "recordings": 0, "chat": 0, "whiteboards": 0}
    async with session_maker() as db:
        try:
            await protocols.retry_pending_exports(db)  # повтор выгрузки записей, не дошедших до внешнего хранилища
        except Exception:  # noqa: BLE001
            log.exception("Ошибка повторной выгрузки записей")
        # Сведение, прерванное перезапуском сервера, не должно блокировать очистку навсегда: старше MIX_STALE — считаем неудавшимся.
        stuck = (await db.execute(select(Recording).where(Recording.kind == "mix_audio", Recording.status == "processing"))).scalars().all()
        mixing: set = set()
        for mix in stuck:
            if mix.created_at < now - MIX_STALE:
                mix.status, mix.error = "failed", "Сведение прервано (перезапуск сервера)"
            else:
                mixing.add(mix.meeting_id)
        settings_svc0 = getattr(protocols, "_svc", None) or getattr(protocols, "settings_svc", None)
        general = await settings_svc0.get(db, "general") if settings_svc0 is not None else None
        paused = general is not None and retention_policy.inherited_paused(general, now)
        rooms = (await db.execute(select(Room))).scalars().all()
        for room in rooms:
            if general is not None:
                eff = retention_policy.effective(room, general)
            else:                                       # без службы настроек (старые тесты) — прежнее поведение: значения комнаты
                eff = {"text": room.text_retention_days, "audio": room.audio_retention_days, "video": None, "custom": {"text": True, "audio": True, "video": True}}
            skip = {k for k in ("text", "audio", "video") if paused and not eff["custom"][k]}     # общие сроки ещё не вступили в силу
            if eff["text"] is not None and "text" not in skip:
                cutoff = now - (RETENTION_GRACE if eff["text"] == 0 else timedelta(days=eff["text"]))
                ids = (await db.execute(select(Meeting.id).where(Meeting.room_id == room.id, Meeting.ended_at.is_not(None),
                                                                 Meeting.ended_at < cutoff))).scalars().all()
                if ids:
                    if protocols.chat_files is not None:
                        await protocols.chat_files.delete_for_meetings(db, list(ids))   # файлы вложений уходят вместе с перепиской
                    r1 = await db.execute(delete(TranscriptSegment).where(TranscriptSegment.meeting_id.in_(ids)))
                    r2 = await db.execute(delete(Protocol).where(Protocol.meeting_id.in_(ids), Protocol.kind.in_(("summary", "protocol"))))
                    stats["segments"] += r1.rowcount or 0
                    stats["protocols"] += r2.rowcount or 0
                    stats["chat"] += (await db.execute(delete(MeetingChatMessage).where(MeetingChatMessage.meeting_id.in_(ids)))).rowcount or 0
                    stats["whiteboards"] += (await db.execute(delete(MeetingWhiteboard).where(MeetingWhiteboard.meeting_id.in_(ids)))).rowcount or 0
            # аудио (файлы участников и общая аудиозапись) и видео (видеозаписи и записи эфиров) — каждое по своему сроку
            for kind, days in (("audio", eff["audio"]), ("video", eff["video"])):
                if days is None or kind in skip:
                    continue
                cutoff = now - (RETENTION_GRACE if days == 0 else timedelta(days=days))
                cond = Recording.kind == "mix_video" if kind == "video" else Recording.kind != "mix_video"
                recs = (await db.execute(select(Recording).where(Recording.room_id == room.id, cond, Recording.created_at < cutoff))).scalars().all()
                for rec in recs:
                    if rec.meeting_id in mixing:
                        continue                  # общая запись ещё собирается из этих файлов — удалим при следующей очистке
                    if rec.kind == "mix_video" and rec.status in ("processing", "finishing"):
                        continue                  # видеофайл ещё пишет Egress — живую запись по сроку не удаляем
                    await protocols.purge_recording(db, rec)
                    stats["video_recordings" if kind == "video" else "recordings"] = stats.get("video_recordings" if kind == "video" else "recordings", 0) + 1
        if protocols.chat_files is not None:
            orphans = await protocols.chat_files.purge_orphans(db)   # загружены, но не отправлены
            if orphans:
                stats["attachments_orphaned"] = orphans
        await db.commit()
        settings_svc = getattr(protocols, "_svc", None) or getattr(protocols, "settings_svc", None)
        if settings_svc is not None and protocols.chat_files is not None:
            from ..services import chats  # noqa: PLC0415

            days = (await settings_svc.get(db, "chats")).retention_days       # срок хранения корпоративной переписки (0 — без срока)
            if days:
                stats["chat_messages"] = await chats.purge_expired(db, protocols.chat_files, days)
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
