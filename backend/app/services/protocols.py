"""Протоколы встреч: стенограмма, выгрузка в хранилище, краткий протокол через LLM.

Порядок для краткого протокола (fail closed): стенограмма → API обезличивания → LLM.
Если обезличивание не настроено/недоступно/не подтвердило verification.clean — в LLM ничего не уходит,
протокол получает статус failed с понятной причиной.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..integrations.anonymizer import AnonymizerClient, AnonymizerError
from ..integrations.llm import LlmClient, LlmError
from ..models import Meeting, Protocol, Recording, TranscriptSegment, User, utcnow
from .recordings import delete_recording_file, finalize_pcm_files
from .settings import SettingsError, SettingsService
from .storage import StorageError, WEEKDAYS_RU, build_storage, meeting_relpath, unique_meeting_dir

log = logging.getLogger("app.protocols")

SYSTEM_PROMPT = (
    "Ты — секретарь совещания. Пиши по-русски, строго по тексту стенограммы, ничего не выдумывай. "
    "В стенограмме персональные и конфиденциальные данные заменены метками (например, [ФИО_1]) — "
    "сохраняй метки как есть и не пытайся их раскрыть. Ответ — только текст протокола."
)


def format_clock(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M:%S")


def render_transcript(room_name: str, started: datetime, ended: datetime | None, participants: list[str],
                      segments: list[tuple[datetime, str, str]], tz: ZoneInfo) -> str:
    """Шапка («участвовали …») и далее реплики по времени: [ЧЧ:ММ:СС] Имя: текст."""
    s = started.astimezone(tz)
    head = [
        f"Переговорка: {room_name}",
        f"Дата: {s:%Y-%m-%d} ({WEEKDAYS_RU[s.weekday()]})",
        f"Начало: {s:%H:%M}" + (f" — окончание: {ended.astimezone(tz):%H:%M}" if ended else ""),
        f"Участвовали: {', '.join(participants) if participants else '—'}",
        "",
    ]
    return "\n".join(head + [f"[{format_clock(t, tz)}] {name}: {text}" for t, name, text in segments]) + "\n"


def split_for_llm(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > limit and cur:
            parts.append("".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur:
        parts.append("".join(cur))
    return parts


class ProtocolService:
    def __init__(self, settings: Settings, session_maker: async_sessionmaker[AsyncSession], svc: SettingsService,
                 *, transports: dict | None = None):
        self._s = settings
        self._sm = session_maker
        self._svc = svc
        self._transports = transports or {}
        self._tasks: set[asyncio.Task] = set()
        self.flush_delay = 5.0  # даём ASR-воркеру закрыть файлы записи после команды stop

    def spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ----------------------------------------------------------------- вспомогательное
    async def _tz(self, db: AsyncSession) -> ZoneInfo:
        name = (await self._svc.get(db, "general")).timezone  # type: ignore[attr-defined]
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — нет базы часовых поясов: работаем в UTC, но не падаем
            log.warning("Часовой пояс недоступен, используется UTC", extra={"timezone": name})
            return timezone.utc  # type: ignore[return-value]

    async def transcript_text(self, db: AsyncSession, meeting: Meeting, tz: ZoneInfo) -> str:
        rows = (await db.execute(select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting.id)
                                 .order_by(TranscriptSegment.started_at, TranscriptSegment.id))).scalars().unique().all()
        latest: dict[uuid.UUID, str] = {}
        for p in sorted(meeting.participants, key=lambda p: p.joined_at):
            latest.setdefault(p.user_id, p.user.display_name)
        return render_transcript(
            meeting.room.name, meeting.started_at, meeting.ended_at, list(latest.values()),
            [(r.started_at, r.user.display_name if r.user else "Неизвестный участник", r.text) for r in rows], tz)

    def _storage(self, cfg):
        try:
            return build_storage(cfg, self._s.data_dir)
        except StorageError as exc:
            log.error("Хранилище недоступно", extra={"error": str(exc)})
            return None

    def _ca(self) -> str | None:
        return self._s.ldap_ca_file or None

    # --------------------------------------------------------------- завершение встречи
    async def finalize(self, meeting_id: uuid.UUID) -> None:
        """Выполняется после завершения встречи; каждый шаг изолирован — сбой одного не отменяет остальные."""
        try:
            async with self._sm() as db:
                meeting = await db.get(Meeting, meeting_id)
                if meeting is None or meeting.ended_at is None:
                    return
                tz = await self._tz(db)
                storage_cfg = await self._svc.get(db, "storage")
                names = {p.user.livekit_identity: p.user.display_name for p in meeting.participants}
                rel = meeting_relpath(meeting.room.name, meeting.started_at.astimezone(tz))
                room_slug = meeting.room.slug

                if meeting.record_audio:
                    await asyncio.sleep(self.flush_delay)
                    await self._register_recordings(db, meeting, room_slug, rel, names)

                storage = self._storage(storage_cfg)
                dir_rel = None
                if storage and storage_cfg.export_transcript:  # type: ignore[attr-defined]
                    text = await self.transcript_text(db, meeting, tz)
                    rec = Protocol(meeting_id=meeting_id, kind="transcript", status="pending", created_by="system")
                    db.add(rec)
                    await db.flush()
                    try:
                        dir_rel = await unique_meeting_dir(storage, rel)
                        loc = await asyncio.to_thread(storage.write_bytes, f"{dir_rel}/protocol.txt", text.encode("utf-8"))
                        rec.status, rec.meta = "ready", {"dir": dir_rel, "location": loc}
                    except StorageError as exc:
                        rec.status, rec.error = "failed", str(exc)[:480]
                        log.error("Выгрузка стенограммы не удалась", extra={"meeting_id": str(meeting_id), "error": str(exc)})
                    await db.commit()
                    if dir_rel and storage_cfg.export_audio:  # type: ignore[attr-defined]
                        await self._export_audio(db, storage, meeting_id, dir_rel)
                proto_cfg = await self._svc.get(db, "protocol")
            if proto_cfg.auto_generate:  # type: ignore[attr-defined]
                await self.run_summary(await self.create_summary_row(meeting_id, "auto"))
        except Exception:  # noqa: BLE001
            log.exception("Ошибка финализации встречи", extra={"meeting_id": str(meeting_id)})

    async def _register_recordings(self, db: AsyncSession, meeting: Meeting, room_slug: str, rel_dir: str, names: dict[str, str]) -> None:
        finished = await asyncio.to_thread(finalize_pcm_files, self._s.recordings_path, meeting.livekit_room, rel_dir, names)
        from .livekit import parse_user_identity

        for f in finished:
            uid = parse_user_identity(f.identity)
            user = await db.get(User, uid) if uid else None
            db.add(Recording(meeting_id=meeting.id, room_id=meeting.room_id, user_id=user.id if user else None,
                             participant_identity=f.identity, path=f.rel_path, size_bytes=f.size_bytes, duration_s=f.duration_s))
        await db.commit()

    async def _export_audio(self, db: AsyncSession, storage, meeting_id: uuid.UUID, dir_rel: str) -> None:
        from pathlib import Path

        recs = (await db.execute(select(Recording).where(Recording.meeting_id == meeting_id))).scalars().all()
        for r in recs:
            try:
                data = await asyncio.to_thread(Path(self._s.recordings_path, r.path).read_bytes)
                await asyncio.to_thread(storage.write_bytes, f"{dir_rel}/audio/{Path(r.path).name}", data)
            except (OSError, StorageError) as exc:
                log.error("Выгрузка аудио не удалась", extra={"recording": str(r.id), "error": str(exc)})

    # ------------------------------------------------------------------- краткий протокол
    async def create_summary_row(self, meeting_id: uuid.UUID, actor: str) -> uuid.UUID:
        async with self._sm() as db:
            rec = Protocol(meeting_id=meeting_id, kind="summary", status="pending", created_by=actor)
            db.add(rec)
            await db.commit()
            return rec.id

    def start_summary(self, protocol_id: uuid.UUID) -> None:
        self.spawn(self.run_summary(protocol_id), f"summary-{protocol_id}")

    async def run_summary(self, protocol_id: uuid.UUID) -> None:
        async with self._sm() as db:
            rec = await db.get(Protocol, protocol_id)
            if rec is None:
                return
            try:
                text, meta = await self._summarize(db, rec.meeting_id)
                rec.content, rec.status, rec.error, rec.meta = text, "ready", None, meta
                await db.commit()
                await self._export_summary(db, rec)
            except (AnonymizerError, LlmError) as exc:
                rec.status, rec.error = "failed", exc.describe()
                log.warning("Краткий протокол не создан", extra={"protocol": str(protocol_id), "code": exc.code})
                await db.commit()
            except SettingsError as exc:
                rec.status, rec.error = "failed", str(exc)[:480]
                await db.commit()
            except Exception:  # noqa: BLE001
                log.exception("Ошибка создания краткого протокола", extra={"protocol": str(protocol_id)})
                rec.status, rec.error = "failed", "Внутренняя ошибка (см. журнал сервера)"
                await db.commit()

    async def _summarize(self, db: AsyncSession, meeting_id: uuid.UUID) -> tuple[str, dict]:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            raise SettingsError("Встреча не найдена")
        an_cfg = await self._svc.get(db, "anonymizer")
        llm_cfg = await self._svc.get(db, "llm")
        pr_cfg = await self._svc.get(db, "protocol")
        tz = await self._tz(db)
        text = await self.transcript_text(db, meeting, tz)
        if not any(line.startswith("[") for line in text.splitlines()):
            raise SettingsError("В стенограмме нет реплик — протокол не создаётся")

        # 1. Обезличивание — обязательно; сбой = отказ (fail closed).
        anon = AnonymizerClient(an_cfg, ca_file=self._ca(), transport=self._transports.get("anonymizer"))  # type: ignore[arg-type]
        clean = await anon.anonymize(text, "protocol")

        # 2. LLM получает только обезличенный текст.
        llm = LlmClient(llm_cfg, ca_file=self._ca(), transport=self._transports.get("llm"))  # type: ignore[arg-type]
        room_instr = (meeting.room.protocol_instructions or "").strip()
        system = SYSTEM_PROMPT + "\n\nИнструкции организации:\n" + pr_cfg.instructions.strip()  # type: ignore[attr-defined]
        if room_instr:
            system += "\n\nИнструкции для этой переговорки:\n" + room_instr
        parts = split_for_llm(clean.text, pr_cfg.max_input_chars)  # type: ignore[attr-defined]
        calls, pt, ct = 0, 0, 0
        if len(parts) == 1:
            res = await llm.complete(system, "Стенограмма встречи:\n\n" + parts[0])
            calls, pt, ct = 1, res.prompt_tokens or 0, res.completion_tokens or 0
            out = res.text
        else:  # длинная встреча: частичные заметки → итоговый протокол
            notes: list[str] = []
            for i, part in enumerate(parts, 1):
                r = await llm.complete(SYSTEM_PROMPT + "\nСделай подробные заметки по фрагменту (факты, решения, поручения).",
                                       f"Фрагмент {i} из {len(parts)}:\n\n{part}")
                notes.append(r.text)
                calls += 1
                pt += r.prompt_tokens or 0
                ct += r.completion_tokens or 0
            r = await llm.complete(system, "Заметки по фрагментам встречи:\n\n" + "\n\n---\n\n".join(notes))
            calls += 1
            pt += r.prompt_tokens or 0
            ct += r.completion_tokens or 0
            out = r.text
        meta = {"model": llm_cfg.model, "llm_type": llm_cfg.type, "llm_calls": calls,  # type: ignore[attr-defined]
                "prompt_tokens": pt, "completion_tokens": ct, "anonymized_chunks": clean.chunks,
                "anonymized_replaced": clean.replaced, "generated_at": utcnow().isoformat()}
        return out, meta

    async def _export_summary(self, db: AsyncSession, rec: Protocol) -> None:
        cfg = await self._svc.get(db, "storage")
        storage = self._storage(cfg)
        if not storage or not cfg.export_summary or not rec.content:  # type: ignore[attr-defined]
            return
        prev = (await db.execute(select(Protocol).where(Protocol.meeting_id == rec.meeting_id, Protocol.kind == "transcript",
                                                         Protocol.status == "ready").order_by(Protocol.created_at.desc()))).scalars().first()
        meeting = await db.get(Meeting, rec.meeting_id)
        if prev and prev.meta and prev.meta.get("dir"):
            dir_rel = prev.meta["dir"]
        elif meeting:
            tz = await self._tz(db)
            dir_rel = await unique_meeting_dir(storage, meeting_relpath(meeting.room.name, meeting.started_at.astimezone(tz)))
        else:
            return
        try:
            loc = await asyncio.to_thread(storage.write_bytes, f"{dir_rel}/summary.txt", (rec.content + "\n").encode("utf-8"))
            rec.meta = {**(rec.meta or {}), "dir": dir_rel, "location": loc}
            await db.commit()
        except StorageError as exc:
            log.error("Выгрузка краткого протокола не удалась", extra={"protocol": str(rec.id), "error": str(exc)})

    # ------------------------------------------------------------------------- хранение
    async def purge_recording(self, db: AsyncSession, rec: Recording) -> None:
        await asyncio.to_thread(delete_recording_file, self._s.recordings_path, rec.path)
        await db.delete(rec)
