"""Протоколы встреч: стенограмма, выгрузка в хранилище, краткий протокол через LLM.

Порядок: стенограмма → (обезличивание, если оно включено для этой комнаты) → LLM.
Обезличивание ВЫКЛЮЧЕНО в общих настройках или в комнате (`anonymize_mode=off`) — текст уходит в LLM как есть, это не ошибка и ничему не мешает;
факт отражается в метаданных протокола (`anonymized=false`) и в журнале. Если оно ВКЛЮЧЕНО (`inherit` + включено глобально, либо `on`), то работает
fail closed: не настроено/недоступно/не подтвердило verification.clean — в LLM ничего не уходит, протокол получает статус failed с понятной причиной.
API (LLM и обезличивания) выбирается по профилю комнаты или общему профилю по умолчанию (services/api_profiles.py).
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
from ..integrations.anonymizer import Anonymized, AnonymizerClient, AnonymizerError
from ..integrations.llm import LlmClient, LlmError
from .llm_choice import resolve_llm
from .local_llm import LocalLlm
from ..models import GuestParticipant, Meeting, MeetingWhiteboard, Protocol, Recording, TranscriptSegment, User, utcnow
from .api_profiles import ProfileService
from .filestore import AUDIO, BOARDS, CHAT, PROTOCOLS, TRANSCRIPTS, FileStore
from .materials import SOURCES_PROMPT, build_materials, chat_files_map, chat_messages, render_chat
from .recordings import delete_recording_file, finalize_pcm_files
from .segments import author_name
from .settings import SettingsError, SettingsService
from .storage import StorageError, WEEKDAYS_RU, meeting_relpath, unique_meeting_dir

log = logging.getLogger("app.protocols")


class NothingToProcess(SettingsError):
    """В встрече нет ни реплик, ни чата, ни схемы: это штатный случай (пустая встреча), а не сбой — в журнал ошибкой не пишется."""

SYSTEM_PROMPT = (
    "Ты — секретарь совещания. Пиши по-русски, строго по тексту стенограммы, ничего не выдумывай. "
    "В стенограмме персональные и конфиденциальные данные заменены метками (например, [ФИО_1]) — "
    "сохраняй метки как есть и не пытайся их раскрыть. Ответ — только текст протокола."
)
# Дополнение для облегчённой локальной модели: простые, проверяемые формулировки, без домыслов
LOCAL_HINT = ("\n\nПиши коротко и только по тексту встречи: решения, задачи, ответственные, сроки. Не выдумывай факты, имена и даты; если сведений нет — так и напиши. "
              "Отвечай по-русски.")
SYSTEM_PROMPT_WITH_SOURCES = SYSTEM_PROMPT + " " + SOURCES_PROMPT


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
        self.profiles = ProfileService(svc)
        self.local_llm = LocalLlm(settings, transport=self._transports.get("local_llm"))   # встроенная локальная модель (Qwen3 0.6B)
        self.files = FileStore(svc, settings.data_dir)
        self.chat_files = None  # services.chat_files.ChatFilesService; задаётся при запуске приложения
        self.after_finalize = None  # async (meeting_id) -> None: рассылка материалов после завершения; задаётся при запуске приложения
        self.journal = None  # services.journal.Journal; задаётся при запуске приложения
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
        guests = (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id == meeting.id)
                                   .order_by(GuestParticipant.joined_at))).scalars().all()
        names = list(latest.values()) + [f"{g.display_name} (гость)" for g in guests]
        return render_transcript(
            meeting.room.name, meeting.started_at, meeting.ended_at, names,
            [(r.started_at, author_name(r) or "Неизвестный участник", r.text) for r in rows], tz)

    async def has_materials(self, meeting_id: uuid.UUID) -> bool:
        """Есть ли что обрабатывать: реплики, сообщения чата или схема на доске."""
        from sqlalchemy import func  # noqa: PLC0415

        from ..models import MeetingChatMessage  # noqa: PLC0415

        async with self._sm() as db:
            if (await db.execute(select(func.count()).select_from(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id))).scalar_one():
                return True
            if (await db.execute(select(func.count()).select_from(MeetingChatMessage).where(MeetingChatMessage.meeting_id == meeting_id))).scalar_one():
                return True
            board = await db.get(MeetingWhiteboard, meeting_id)
            return bool(board and board.shapes > 0)

    async def _open(self, db: AsyncSession, group: str, folder: str, *, legacy_prefix: str = ""):
        """Хранилище функции (профиль → подпапка) или None, если выгрузка выключена/недоступна; недоступность пишется в лог и журнал."""
        try:
            return await self.files.backend(db, group, folder, legacy_prefix=legacy_prefix)
        except StorageError as exc:
            log.error("Хранилище недоступно", extra={"error": str(exc), "folder": folder})
            if self.journal is not None:
                self.journal.emit("storage", "storage_unavailable", level="error", message=str(exc)[:300], data={"folder": folder})
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
                for g in (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id == meeting_id))).scalars():
                    names[g.livekit_identity] = g.label
                rel = meeting_relpath(meeting.room.name, meeting.started_at.astimezone(tz))
                room_slug = meeting.room.slug

                if meeting.record_audio or meeting.room.record_audio:  # запись могли включать и выключать во встрече — файлы собираем, если комната её допускает
                    await asyncio.sleep(self.flush_delay)
                    await self._register_recordings(db, meeting, room_slug, rel, names)

                storage = await self._open(db, "storage", TRANSCRIPTS)
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
                        await self._export_collab(db, meeting, dir_rel, tz)
                    except StorageError as exc:
                        rec.status, rec.error = "failed", str(exc)[:480]
                        log.error("Выгрузка стенограммы не удалась", extra={"meeting_id": str(meeting_id), "error": str(exc)})
                    await db.commit()
                proto_cfg = await self._svc.get(db, "protocol")
            if (proto_cfg.auto_generate or proto_cfg.auto_summary) and not await self.has_materials(meeting_id):  # type: ignore[attr-defined]
                # пустая встреча: автоматический протокол не создаётся и ошибок в журнале не плодит
                if self.journal is not None:
                    self.journal.emit("llm", "protocol_skipped", meeting_id=str(meeting_id), message="Автоматический протокол не создан: в встрече нет реплик, чата и схемы")
                return
            if proto_cfg.auto_generate:  # type: ignore[attr-defined]
                await self.run_protocol(await self.create_protocol_row(meeting_id, "protocol", "auto", None))
            if proto_cfg.auto_summary:  # type: ignore[attr-defined]
                await self.run_protocol(await self.create_protocol_row(meeting_id, "summary", "auto", None))
            if self.after_finalize is not None:
                await self.after_finalize(meeting_id)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка финализации встречи", extra={"meeting_id": str(meeting_id)})

    async def _export_collab(self, db: AsyncSession, meeting: Meeting, dir_rel: str, tz: ZoneInfo) -> None:
        """Чат (chat.txt) и схема доски (whiteboard.drawio — её можно открыть и продолжить редактировать) рядом со стенограммой.
        Сбой этого шага не отменяет остальную финализацию."""
        try:
            msgs = await chat_messages(db, meeting.id)
            chat = await self._open(db, "storage", CHAT)
            if msgs and chat is not None:
                await asyncio.to_thread(chat.write_bytes, f"{dir_rel}/chat.txt", render_chat(msgs, tz, await chat_files_map(db, msgs)).encode("utf-8"))
            board = await db.get(MeetingWhiteboard, meeting.id)
            boards = await self._open(db, "storage", BOARDS) if board is not None and board.xml and board.shapes > 0 else None
            if boards is not None and board is not None:
                await asyncio.to_thread(boards.write_bytes, f"{dir_rel}/whiteboard.drawio", board.xml.encode("utf-8"))
        except StorageError as exc:
            log.error("Выгрузка чата/схемы не удалась", extra={"meeting_id": str(meeting.id), "error": str(exc)})

    async def _register_recordings(self, db: AsyncSession, meeting: Meeting, room_slug: str, rel_dir: str, names: dict[str, str]) -> None:
        finished = await asyncio.to_thread(finalize_pcm_files, self._s.recordings_path, meeting.livekit_room, rel_dir, names)
        from .livekit import parse_user_identity

        recs: list[Recording] = []
        for f in finished:
            uid = parse_user_identity(f.identity)
            user = await db.get(User, uid) if uid else None
            rec = Recording(meeting_id=meeting.id, room_id=meeting.room_id, user_id=user.id if user else None,
                            participant_identity=f.identity, path=f.rel_path, size_bytes=f.size_bytes, duration_s=f.duration_s)
            db.add(rec)
            recs.append(rec)
        await db.commit()
        for rec in recs:
            await self.export_recording(db, rec)

    async def _audio_storage(self, db: AsyncSession):
        """Записи аудио: профиль → Audio/…; старые настройки → audio/… (как раньше)."""
        try:
            return await self.files.backend(db, "audio_storage", AUDIO, legacy_prefix="audio")
        except StorageError as exc:
            log.error("Хранилище записей недоступно", extra={"error": str(exc)})
            return None

    async def export_recording(self, db: AsyncSession, rec: Recording) -> None:
        """Выгрузка WAV во внешнее хранилище записей. Запись НЕ теряется: при сбое остаётся на локальном томе
        в статусе failed и выгружается повторно (retry_pending_exports)."""
        cfg = await self._svc.get(db, "audio_storage")
        if not cfg.enabled:  # type: ignore[attr-defined]
            rec.export_status = "local"
            await db.commit()
            return
        storage = await self._audio_storage(db)
        from pathlib import Path

        src = Path(self._s.recordings_path, rec.path)
        try:
            if storage is None:
                raise StorageError("хранилище записей недоступно или настроено некорректно")
            data = await asyncio.to_thread(src.read_bytes)
            loc = await asyncio.to_thread(storage.write_bytes, rec.path, data)
            rec.export_status, rec.export_location, rec.export_error, rec.exported_at = "exported", loc, None, utcnow()
            if not cfg.keep_local_copy:  # type: ignore[attr-defined]
                await asyncio.to_thread(delete_recording_file, self._s.recordings_path, rec.path)
        except (OSError, StorageError) as exc:
            rec.export_status, rec.export_error = "failed", str(exc)[:480]
            log.error("Выгрузка записи не удалась — файл сохранён локально, будет повтор",
                      extra={"recording": str(rec.id), "error": str(exc)})
            if self.journal is not None:
                self.journal.emit("storage", "recording_export_failed", level="error", meeting_id=str(rec.meeting_id),
                                  message=str(exc)[:300], data={"recording": str(rec.id), "size_bytes": rec.size_bytes})
        await db.commit()

    async def retry_pending_exports(self, db: AsyncSession) -> int:
        cfg = await self._svc.get(db, "audio_storage")
        if not cfg.enabled:  # type: ignore[attr-defined]
            return 0
        recs = (await db.execute(select(Recording).where(Recording.export_status.in_(("pending", "failed")), Recording.file_state != "missing"))).scalars().all()
        n = 0
        for rec in recs:
            await self.export_recording(db, rec)
            n += rec.export_status == "exported"
        return n

    async def read_recording(self, db: AsyncSession, rec: Recording) -> bytes:
        """Содержимое WAV: локальный файл, а если его нет (keep_local_copy=false) — из внешнего хранилища."""
        from pathlib import Path

        root = Path(self._s.recordings_path).resolve()
        full = (root / rec.path).resolve()
        if root in full.parents and full.is_file():
            return await asyncio.to_thread(full.read_bytes)
        cfg = await self._svc.get(db, "audio_storage")
        storage = await self._audio_storage(db) if rec.export_status == "exported" else None
        if storage is None:
            raise StorageError("Файл записи недоступен")
        return await asyncio.to_thread(storage.read_bytes, rec.path)

    async def delete_recording(self, db: AsyncSession, rec: Recording) -> None:
        """Удаляет файл (локальный и во внешнем хранилище) и строку. Сбой внешнего удаления не скрывается."""
        await asyncio.to_thread(delete_recording_file, self._s.recordings_path, rec.path)
        if rec.export_status == "exported":
            storage = await self._audio_storage(db)
            if storage is not None:
                try:
                    await asyncio.to_thread(storage.delete, rec.path)
                except StorageError as exc:
                    log.error("Не удалось удалить файл записи во внешнем хранилище", extra={"recording": str(rec.id), "error": str(exc)})
        await db.delete(rec)

    # ------------------------------------------------------- протокол и краткое резюме (LLM)
    async def default_instruction(self, db: AsyncSession, meeting: Meeting, kind: str) -> str:
        """Инструкция по умолчанию: общая (протокол/резюме) + дополнения конкретной переговорки."""
        cfg = await self._svc.get(db, "protocol")
        base = (cfg.summary_instructions if kind == "summary" else cfg.instructions).strip()  # type: ignore[attr-defined]
        extra = (meeting.room.protocol_instructions or "").strip()
        return base + (f"\n\nДополнительно для этой переговорки: {extra}" if extra else "")

    async def create_protocol_row(self, meeting_id: uuid.UUID, kind: str, actor: str, instruction: str | None) -> uuid.UUID:
        async with self._sm() as db:
            meeting = await db.get(Meeting, meeting_id)
            instr = (instruction or "").strip() or (await self.default_instruction(db, meeting, kind) if meeting else None)
            rec = Protocol(meeting_id=meeting_id, kind=kind, status="pending", created_by=actor, instruction=instr)
            db.add(rec)
            await db.commit()
            return rec.id

    def start_protocol(self, protocol_id: uuid.UUID) -> None:
        self.spawn(self.run_protocol(protocol_id), f"protocol-{protocol_id}")

    async def run_protocol(self, protocol_id: uuid.UUID) -> None:
        async with self._sm() as db:
            rec = await db.get(Protocol, protocol_id)
            if rec is None:
                return
            started = datetime.now(timezone.utc)
            try:
                text, meta = await self._generate(db, rec.meeting_id, rec.kind, rec.instruction or "")
                rec.content, rec.status, rec.error, rec.meta = text, "ready", None, meta
                await db.commit()
                self._emit("llm", "protocol_ready", rec, started, data={k: meta.get(k) for k in (
                    "model", "llm_profile", "anonymizer_profile", "anonymized", "llm_calls", "prompt_tokens", "completion_tokens")})
                await self._export_generated(db, rec)
            except (AnonymizerError, LlmError) as exc:
                rec.status, rec.error = "failed", exc.describe()
                log.warning("Протокол не создан", extra={"protocol": str(protocol_id), "code": exc.code})
                await db.commit()
                self._emit("llm", "protocol_failed", rec, started, level="error", message=rec.error, data={"code": exc.code, "stage": "anonymizer" if isinstance(exc, AnonymizerError) else "llm"})
            except SettingsError as exc:
                rec.status, rec.error = "failed", str(exc)[:480]
                await db.commit()
                if isinstance(exc, NothingToProcess):  # штатное отсутствие данных — не ошибка
                    self._emit("llm", "protocol_skipped", rec, started, level="info", message=rec.error, data={"stage": "empty"})
                else:
                    self._emit("llm", "protocol_failed", rec, started, level="error", message=rec.error, data={"stage": "settings"})
            except Exception:  # noqa: BLE001
                log.exception("Ошибка создания протокола", extra={"protocol": str(protocol_id)})
                rec.status, rec.error = "failed", "Внутренняя ошибка (см. журнал сервера)"
                await db.commit()
                self._emit("llm", "protocol_failed", rec, started, level="error", message=rec.error, data={"stage": "internal"})

    async def plan(self, db: AsyncSession, meeting: Meeting) -> dict:
        """Что произойдёт при создании протокола в этой комнате: готова ли LLM и будет ли текст обезличен (для окна подтверждения)."""
        room = meeting.room
        llm = await resolve_llm(self.profiles, self.local_llm, db, room, meeting)    # системная → комната → встреча
        eff, is_local = self.local_llm.effective(llm.settings)
        mode = room.anonymize_mode if room.anonymize_mode in ("inherit", "on", "off") else "inherit"
        an = None if mode == "off" else await self.profiles.resolve(db, "anonymizer", room)
        # Локальная модель данные наружу не отправляет — обезличивание по умолчанию не нужно (только если включено явно для комнаты)
        anonymize = mode == "on" or (mode == "inherit" and not is_local and an is not None and bool(an.settings.enabled))
        out = {"llm_ready": bool(eff.enabled) and llm.available, "llm_profile": llm.name, "llm_source": llm.source, "llm_note": llm.note, "llm_reason": llm.reason,
               "anonymize": anonymize,
               "anonymizer_profile": an.name if (anonymize and an) else None,
               "anonymizer_ready": (not anonymize) or bool(an and an.settings.enabled),
               "llm_local": is_local, "llm_model": eff.model if eff.enabled else None, "warnings": [], "input_chars": None}
        lm = self.local_llm.limits(llm.settings)
        if lm is not None and llm.available:
            fs = await asyncio.to_thread(self.local_llm.file_state, lm)
            if fs["state"] != "ok":
                out["llm_ready"] = False
                out["warnings"].append("Локальная языковая модель не загружена или повреждена. Администратор может скачать её в разделе «Языковая модель (LLM)».")
            else:
                chars = len(await self.transcript_text(db, meeting, await self._tz(db)))
                out["input_chars"] = chars
                if lm.light and chars > lm.warn_input_chars:
                    out["warnings"].append(f"Стенограмма длинная ({chars:,} знаков)".replace(",", " ") + f". Локальная модель {lm.title} — облегчённая: на длинных встречах качество может быть "
                                           "ниже, чем у более крупных моделей. Результат стоит проверить; для важных встреч лучше использовать внешнюю модель.")
        return out

    def _emit(self, category: str, event: str, rec: Protocol, started: datetime, *, level: str = "info", message: str | None = None,
              data: dict | None = None) -> None:
        if self.journal is None:
            return
        ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        self.journal.emit(category, event, level=level, meeting_id=str(rec.meeting_id), message=message or f"{rec.kind}: {ms} мс",
                          data={"kind": rec.kind, "duration_ms": ms, "created_by": rec.created_by, **(data or {})})

    async def _generate(self, db: AsyncSession, meeting_id: uuid.UUID, kind: str, instruction: str) -> tuple[str, dict]:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            raise SettingsError("Встреча не найдена")
        room = meeting.room
        llm_res = await resolve_llm(self.profiles, self.local_llm, db, room, meeting)   # системная → комната → встреча
        if not llm_res.available:
            raise LlmError("unavailable", llm_res.reason or "")
        llm_cfg = llm_res.settings
        eff_cfg, is_local = self.local_llm.effective(llm_cfg)
        lm = self.local_llm.limits(llm_cfg)
        pr_cfg = await self._svc.get(db, "protocol")
        tz = await self._tz(db)
        transcript = await self.transcript_text(db, meeting, tz)
        materials = await build_materials(db, meeting, tz, transcript)
        if not materials.usable:
            raise NothingToProcess("В стенограмме нет реплик, а чат и доска пусты — протокол не создаётся")
        text = materials.text

        # 1. Обезличивание — по настройке комнаты/общим настройкам. Включено → сбой = отказ (fail closed); выключено → текст идёт как есть.
        mode = room.anonymize_mode if room.anonymize_mode in ("inherit", "on", "off") else "inherit"
        an_res = None if mode == "off" else await self.profiles.resolve(db, "anonymizer", room)
        # локальная модель не отправляет данные наружу: «наследуемое» обезличивание для неё не применяется (явное «включено» для комнаты — остаётся)
        do_anonymize = mode == "on" or (mode == "inherit" and not is_local and an_res is not None and bool(an_res.settings.enabled))
        if do_anonymize:
            anon = AnonymizerClient(an_res.settings, ca_file=self._ca(), transport=self._transports.get("anonymizer"))  # type: ignore[arg-type, union-attr]
            clean = await anon.anonymize(text, "protocol")
        else:
            clean = Anonymized(text, 0, None, 0)
            log.info("Обезличивание выключено — текст передаётся в LLM как есть", extra={"meeting_id": str(meeting_id), "room_mode": mode})

        # 2. LLM получает только обезличенный текст. Инструкция — ровно та, что подтвердил пользователь.
        llm = self.local_llm.client(llm_cfg, ca_file=self._ca(), transport=self._transports.get("llm"))  # type: ignore[arg-type]
        instruction = instruction.strip() or await self.default_instruction(db, meeting, kind)
        form = ("Оформи ответ в Markdown: заголовки, списки, при необходимости таблица «Поручения» (ответственный, поручение, срок)."
                if kind == "protocol" else "Оформи ответ коротким Markdown-текстом.")
        system = (SYSTEM_PROMPT_WITH_SOURCES if (materials.chat_messages or materials.whiteboard_shapes) else SYSTEM_PROMPT) \
            + "\n\nИнструкция пользователя:\n" + instruction + "\n\n" + form
        limit = min(pr_cfg.max_input_chars, lm.max_input_chars) if lm else pr_cfg.max_input_chars   # type: ignore[attr-defined]
        warnings: list[str] = []
        if lm is not None:
            system += LOCAL_HINT
            if lm.light and len(clean.text) > lm.warn_input_chars:
                warnings.append(f"Длинная стенограмма обработана облегчённой локальной моделью {lm.title}: качество может быть ниже, чем у более крупных моделей — проверьте результат.")
        parts = split_for_llm(clean.text, limit)
        calls, pt, ct = 0, 0, 0
        if len(parts) == 1:
            res = await llm.complete(system, ("Материалы встречи:\n\n" if text is not transcript else "Стенограмма встречи:\n\n") + parts[0])
            calls, pt, ct = 1, res.prompt_tokens or 0, res.completion_tokens or 0
            out = res.text
        else:  # длинная встреча: частичные заметки → итоговый документ
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
        meta = {"model": eff_cfg.model, "llm_type": "local" if is_local else eff_cfg.type, "llm_local": is_local, "warnings": warnings, "llm_calls": calls,  # type: ignore[attr-defined]
                "prompt_tokens": pt, "completion_tokens": ct, "anonymized_chunks": clean.chunks,
                "anonymized_replaced": clean.replaced, "anonymized": do_anonymize, "llm_profile": llm_res.name, "llm_source": llm_res.source, "llm_note": llm_res.note,
                "anonymizer_profile": an_res.name if (do_anonymize and an_res) else None, "generated_at": utcnow().isoformat(),
                "sources": materials.meta()}
        return out, meta

    async def _export_generated(self, db: AsyncSession, rec: Protocol) -> None:
        cfg = await self._svc.get(db, "storage")
        if not cfg.export_summary or not rec.content:  # type: ignore[attr-defined]
            return
        storage = await self._open(db, "storage", PROTOCOLS)
        if not storage:
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
        name = "official-protocol.md" if rec.kind == "protocol" else "summary.md"
        try:
            loc = await asyncio.to_thread(storage.write_bytes, f"{dir_rel}/{name}", (rec.content + "\n").encode("utf-8"))
            rec.meta = {**(rec.meta or {}), "dir": dir_rel, "location": loc}
            await db.commit()
        except StorageError as exc:
            log.error("Выгрузка протокола не удалась", extra={"protocol": str(rec.id), "error": str(exc)})

    # ------------------------------------------------------------------------- хранение
    async def purge_recording(self, db: AsyncSession, rec: Recording) -> None:
        await self.delete_recording(db, rec)
