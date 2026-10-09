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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..integrations.anonymizer import Anonymized, AnonymizerClient, AnonymizerError
from ..integrations.llm import LlmClient, LlmError
from . import llm_stats
from .llm_choice import parse_once, resolve_llm
from .extraction import structured_pipeline
from .local_llm import LocalLlm, LocalModel
from ..models import GuestParticipant, Meeting, MeetingWhiteboard, Protocol, Recording, TranscriptSegment, User, utcnow
from .api_profiles import ProfileService
from .filestore import AUDIO, BOARDS, CHAT, PROTOCOLS, TRANSCRIPTS, FileStore
from .materials import SOURCES_PROMPT, build_materials, chat_files_map, chat_messages, render_chat
from .recordings import delete_recording_file, finalize_pcm_files
from .segments import author_name
from .settings import DEFAULT_PROTOCOL_INSTRUCTION, DEFAULT_SUMMARY_INSTRUCTION, SettingsError, SettingsService
from .storage import StorageError, WEEKDAYS_RU, meeting_relpath, unique_meeting_dir

log = logging.getLogger("app.protocols")


EXTERNAL_FRAGMENT_CHARS = 12000      # размер фрагмента для структурного режима внешней модели (проходы по фрагментам, а не одним огромным запросом)


class NothingToProcess(SettingsError):
    """В встрече нет ни реплик, ни чата, ни схемы: это штатный случай (пустая встреча), а не сбой — в журнал ошибкой не пишется."""

_BASE_PROMPT = "Ты — секретарь совещания. Пиши по-русски, строго по тексту стенограммы, ничего не выдумывай."
_ANON_HINT = ("В стенограмме персональные и конфиденциальные данные заменены метками (например, [ФИО_1]) — "
              "сохраняй метки как есть и не пытайся их раскрыть.")


def system_prompt(anonymized: bool = True, sources: bool = False) -> str:
    """Общая часть системного промпта. Пример метки [ФИО_1] даётся ТОЛЬКО когда текст действительно обезличен: иначе небольшие модели
    подражают примеру и выдают [ФИО_1] вместо реальных имён (проверено на Qwen3)."""
    return _BASE_PROMPT + ((" " + _ANON_HINT) if anonymized else "") + " Ответ — только текст протокола." + ((" " + SOURCES_PROMPT) if sources else "")


SYSTEM_PROMPT = system_prompt(True)            # прежнее имя (для совместимости)
SYSTEM_PROMPT_WITH_SOURCES = system_prompt(True, True)

# Промежуточный шаг для длинных встреч. Должен ИЗВЛЕКАТЬ, а не переписывать: прежнее «сделай подробные заметки (факты, решения, поручения)» небольшие модели
# выполняли дословным пересказом стенограммы и упирались в лимит длины (проверено на Qwen3 0.6B — вся середина встречи терялась).
NOTES_PROMPT = ("Ты — секретарь совещания. Из фрагмента стенограммы выпиши КРАТКИЙ список: (1) принятые решения; (2) задачи: кто ответственный, что сделать, срок "
                "(если срок не назван — «не указан»); (3) открытые вопросы. Каждый пункт — одна короткая строка своими словами. "
                "НЕ копируй реплики из стенограммы. Ничего не выдумывай (в частности, адреса, версии и числа, которых нет в тексте). Если пунктов нет — напиши «нет».")
NOTES_ANON_HINT = " Метки вроде [ФИО_1] сохраняй как есть."
NOTES_MAX_TOKENS = 600     # хороший список короткий; если модель вместо списка начинает переписывать текст, это видно по обрезке и не тратит минуты на CPU

# Короткие промпты для облегчённой локальной модели: подробный шаблон (нумерованная структура, таблицы) она возвращает пустым каркасом без содержания.
LOCAL_PROTOCOL_PROMPT = ("Ты — секретарь совещания. По стенограмме составь протокол на русском языке. Используй только то, что сказано в стенограмме, ничего не выдумывай. "
                         "Если сведений нет, пиши «не указано». Разделы: Обсуждение (по темам), Решения, Задачи (кто, что, срок), Открытые вопросы.")
LOCAL_SUMMARY_PROMPT = ("Ты — секретарь совещания. Кратко (не более 8 строк) изложи по стенограмме: о чём говорили, какие решения приняты, кто что делает и к какому сроку. "
                        "Только то, что сказано в стенограмме, ничего не выдумывай; если сведений нет — не пиши об этом.")
LOCAL_SOURCES_HINT = " Кроме стенограммы могут быть чат и схема доски: URL, IP-адреса и имена серверов из чата переноси дословно."
LOCAL_INSTRUCTION_LIMIT = 400       # более длинную (подробный шаблон) инструкцию локальная модель не получает — пользователю показывается предупреждение
LOCAL_INSTRUCTION_IGNORED = ("Подробная инструкция не применена: облегчённая локальная модель получила упрощённую. Для протокола по сложному шаблону "
                             "(таблицы, строгая структура) выберите более сильную внешнюю модель.")
STRUCTURED_INSTRUCTION_NOTE = ("Облегчённая локальная модель собирает протокол по фиксированному шаблону (темы, решения, поручения, отклонённое, открытые вопросы с источниками): "
                               "индивидуальная инструкция к нему не применяется. Для протокола по своему шаблону выберите более сильную внешнюю модель.")
TRUNCATED_LOCAL = ("Локальная модель не смогла полностью обработать стенограмму: ответ оборвался по лимиту длины{where}. "
                   "Результат может быть неполным — проверьте его или сформируйте документ более сильной (внешней) моделью.")
TRUNCATED_EXTERNAL = ("Ответ языковой модели оборван по лимиту длины{where}: результат неполный. "
                      "Увеличьте «Максимальную длину ответа» в настройках языковой модели или сократите материалы.")


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


@dataclass
class PipelineResult:
    text: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    parts: int = 1
    truncated: bool = False
    structured: dict = field(default_factory=dict)       # проверенные пункты (темы, решения, поручения с источниками) — только для локального структурного режима
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def local_system(kind: str, instruction: str, has_sources: bool) -> tuple[str, str | None]:
    """Системный промпт для облегчённой локальной модели + предупреждение, если пользовательская инструкция не поместилась."""
    base = LOCAL_SUMMARY_PROMPT if kind == "summary" else LOCAL_PROTOCOL_PROMPT
    ins = (instruction or "").strip()
    warn = None
    if ins and ins not in {DEFAULT_PROTOCOL_INSTRUCTION.strip(), DEFAULT_SUMMARY_INSTRUCTION.strip()}:
        if len(ins) <= LOCAL_INSTRUCTION_LIMIT:
            base += "\nПожелания к документу: " + ins
        else:
            warn = LOCAL_INSTRUCTION_IGNORED
    return base + (LOCAL_SOURCES_HINT if has_sources else ""), warn


async def run_llm_pipeline(llm: LlmClient, *, kind: str, instruction: str, text: str, limit: int, local: LocalModel | None, anonymized: bool,
                           has_sources: bool = False, mixed: bool = False, structured: bool = True, external_structured: bool = False) -> PipelineResult:
    """Текст → (при необходимости заметки по фрагментам) → документ. Каждый ответ проверяется на обрезку по лимиту длины: молча такой результат не проходит."""
    res = PipelineResult()
    if local is None and external_structured and structured:
        # Структурный режим внешней модели: тот же конвейер, что у локальной (JSON по схеме → проверка и сборка документа кодом); не получилось — обычный режим ниже
        sr = await structured_pipeline(llm, kind=kind, instruction=instruction, text=text, limit=min(limit, EXTERNAL_FRAGMENT_CHARS))
        if sr is not None and sr.ok_fragments:
            res.text, res.calls, res.parts = sr.text, sr.calls, sr.parts
            res.prompt_tokens, res.completion_tokens, res.structured = sr.prompt_tokens, sr.completion_tokens, sr.structured
            res.truncated = sr.truncated
            res.warnings += sr.warnings
            return res
        res.warnings.append("Структурный режим не сработал (модель не вернула разбираемый JSON) — использован обычный режим.")
    if local is not None:
        # Облегчённая локальная модель: фрагмент → JSON → проверка и слияние кодом → детерминированный протокол (extraction.py). Прежний текстовый путь — запасной.
        sr = await structured_pipeline(llm, kind=kind, instruction=instruction, text=text, limit=limit) if structured else None
        if sr is not None and sr.ok_fragments:
            res.text, res.calls, res.parts = sr.text, sr.calls, sr.parts
            res.prompt_tokens, res.completion_tokens, res.structured = sr.prompt_tokens, sr.completion_tokens, sr.structured
            res.truncated = sr.truncated
            res.warnings += sr.warnings
            if kind == "protocol" and (instruction or "").strip() not in {DEFAULT_PROTOCOL_INSTRUCTION.strip(), DEFAULT_SUMMARY_INSTRUCTION.strip()}:
                res.warnings.append(STRUCTURED_INSTRUCTION_NOTE)
            if sr.truncated and kind == "summary":
                res.warnings.append(TRUNCATED_LOCAL.format(where=" (резюме)"))
            return res
        if sr is not None:
            res.warnings.append("Структурное извлечение не удалось (модель не вернула разбираемый JSON ни для одного фрагмента) — использован упрощённый текстовый режим.")
        system, warn = local_system(kind, instruction, has_sources)
        if warn:
            res.warnings.append(warn)
    else:
        form = ("Оформи ответ в Markdown: заголовки, списки, при необходимости таблица «Поручения» (ответственный, поручение, срок)."
                if kind == "protocol" else "Оформи ответ коротким Markdown-текстом.")
        system = system_prompt(anonymized, has_sources) + "\n\nИнструкция пользователя:\n" + instruction + "\n\n" + form
    msg = TRUNCATED_LOCAL if local is not None else TRUNCATED_EXTERNAL

    def account(r, where: str = "") -> None:
        res.calls += 1
        res.prompt_tokens += r.prompt_tokens or 0
        res.completion_tokens += r.completion_tokens or 0
        if r.truncated:
            res.truncated = True
            res.warnings.append(msg.format(where=where))

    parts = split_for_llm(text, limit)
    res.parts = len(parts)
    if len(parts) == 1:
        r = await llm.complete(system, ("Материалы встречи:\n\n" if mixed else "Стенограмма встречи:\n\n") + parts[0])
        account(r)
        res.text = r.text
        return res
    notes_system = NOTES_PROMPT + (NOTES_ANON_HINT if anonymized else "")
    for i, part in enumerate(parts, 1):    # длинная встреча: краткое извлечение по фрагментам → итоговый документ
        r = await llm.complete(notes_system, f"Фрагмент {i} из {len(parts)}:\n\n{part}", max_tokens=NOTES_MAX_TOKENS)
        res.notes.append(r.text)
        account(r, f" (часть {i} из {len(parts)})")
    r = await llm.complete(system, "Заметки по фрагментам встречи:\n\n" + "\n\n---\n\n".join(res.notes))
    account(r, " (итоговый документ)")
    res.text = r.text
    return res


class ProtocolService:
    def __init__(self, settings: Settings, session_maker: async_sessionmaker[AsyncSession], svc: SettingsService,
                 *, transports: dict | None = None):
        self._s = settings
        self._sm = session_maker
        self._svc = svc
        self._transports = transports or {}
        self.profiles = ProfileService(svc)
        self.local_llm = LocalLlm(settings, transport=self._transports.get("local_llm"))   # встроенная локальная модель (Qwen3 1.7B)
        self.files = FileStore(svc, settings.data_dir)
        self.chat_files = None  # services.chat_files.ChatFilesService; задаётся при запуске приложения
        self.after_finalize = None  # async (meeting_id) -> None: рассылка материалов после завершения; задаётся при запуске приложения
        self.journal = None  # services.journal.Journal; задаётся при запуске приложения
        self.maps = None  # services.conv_map.MapService (карта разговора); задаётся при запуске приложения
        self._tasks: set[asyncio.Task] = set()
        self.flush_delay = 5.0  # даём ASR-воркеру закрыть файлы записи после команды stop

    def spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        while self._tasks:      # задачи могут порождать другие (финализация → карта разговора): ждём, пока не опустеет
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
            if self.maps is not None:
                try:
                    await self.maps.maybe_auto(meeting_id)      # карта разговора — только если включена в комнате или системно (по умолчанию выключено)
                except Exception:  # noqa: BLE001 — сбой постановки карты в очередь не должен отменять рассылку материалов
                    log.exception("Не удалось поставить карту разговора в очередь", extra={"meeting_id": str(meeting_id)})
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

    async def create_protocol_row(self, meeting_id: uuid.UUID, kind: str, actor: str, instruction: str | None, llm_once: str | None = None) -> uuid.UUID:
        async with self._sm() as db:
            meeting = await db.get(Meeting, meeting_id)
            instr = (instruction or "").strip() or (await self.default_instruction(db, meeting, kind) if meeting else None)
            rec = Protocol(meeting_id=meeting_id, kind=kind, status="pending", created_by=actor, instruction=instr,
                           meta={"llm_once": llm_once} if llm_once else None)
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
            once = (rec.meta or {}).get("llm_once")
            info: dict = {"requested": rec.created_at, "started": started}      # времена этапов и сведения о модели (заполняет _generate)
            try:
                text, meta = await self._generate(db, rec.meeting_id, rec.kind, rec.instruction or "", once=once, info=info)
                meta.update(self._timing_meta(info, datetime.now(timezone.utc)))
                if once:
                    meta["llm_once"] = once
                rec.content, rec.status, rec.error, rec.meta = text, "ready", None, meta
                await db.commit()
                self._emit("llm", "protocol_ready", rec, started, data={k: meta.get(k) for k in (
                    "model", "llm_profile", "anonymizer_profile", "anonymized", "llm_calls", "prompt_tokens", "completion_tokens")})
                await self._export_generated(db, rec)
            except (AnonymizerError, LlmError) as exc:
                rec.status, rec.error = "failed", exc.describe()
                rec.meta = self._failure_meta(info, once, exc.code)
                log.warning("Протокол не создан", extra={"protocol": str(protocol_id), "code": exc.code})
                await db.commit()
                self._emit("llm", "protocol_failed", rec, started, level="error", message=rec.error, data={"code": exc.code, "stage": "anonymizer" if isinstance(exc, AnonymizerError) else "llm"})
            except SettingsError as exc:
                rec.status, rec.error = "failed", str(exc)[:480]
                rec.meta = self._failure_meta(info, once, "settings")
                await db.commit()
                if isinstance(exc, NothingToProcess):  # штатное отсутствие данных — не ошибка
                    self._emit("llm", "protocol_skipped", rec, started, level="info", message=rec.error, data={"stage": "empty"})
                else:
                    self._emit("llm", "protocol_failed", rec, started, level="error", message=rec.error, data={"stage": "settings"})
            except Exception:  # noqa: BLE001
                log.exception("Ошибка создания протокола", extra={"protocol": str(protocol_id)})
                rec.status, rec.error = "failed", "Внутренняя ошибка (см. журнал сервера)"
                rec.meta = self._failure_meta(info, once, "internal")
                await db.commit()
                self._emit("llm", "protocol_failed", rec, started, level="error", message=rec.error, data={"stage": "internal"})

    @staticmethod
    def _aware(d: datetime | None) -> datetime | None:
        return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)

    def _timing_meta(self, info: dict, finished: datetime) -> dict:
        """Времена этапов документа: нажатие «Сформировать» → начало обработки → начало работы модели → готово; очередь, подготовка и работа модели отдельно."""
        req = self._aware(info.get("requested")) or info["started"]
        started, ls, lf = info["started"], info.get("llm_started"), info.get("llm_finished")

        def iso(d):
            return d.isoformat() if d else None

        def sec(a, b):
            return round((b - a).total_seconds(), 1) if a and b else None
        return {"requested_at": iso(req), "started_at": iso(started), "llm_started_at": iso(ls), "llm_finished_at": iso(lf), "finished_at": iso(finished),
                "queue_s": max(0.0, sec(req, started) or 0.0), "prepare_s": sec(started, ls), "llm_s": sec(ls, lf), "total_s": sec(req, finished)}

    def _failure_meta(self, info: dict, once: str | None, code: str) -> dict:
        """Метаданные неудачной попытки: модель и время — чтобы ошибки учитывались в статистике моделей."""
        m = {k: info[k] for k in ("model", "model_title", "llm_local", "llm_type", "llm_profile", "llm_source", "api_type") if k in info}
        m.update(self._timing_meta(info, datetime.now(timezone.utc)), failed=True, error_code=code)
        if "llm_started" in info and not info.get("llm_finished"):
            m["llm_s"] = round((datetime.now(timezone.utc) - info["llm_started"]).total_seconds(), 1)
        if once:
            m["llm_once"] = once
        return m

    async def plan(self, db: AsyncSession, meeting: Meeting, kind: str = "protocol", once: str | None = None) -> dict:
        """Что произойдёт при создании протокола в этой комнате: готова ли LLM и будет ли текст обезличен (для окна подтверждения)."""
        room = meeting.room
        purpose = "summary" if kind == "summary" else "protocol"
        try:
            once_choice = parse_once(once)
        except ValueError as exc:
            raise SettingsError(str(exc)) from None
        llm = await resolve_llm(self.profiles, self.local_llm, db, room, meeting, purpose, once=once_choice)    # разово → встреча → комната → система
        eff, is_local = self.local_llm.effective(llm.settings)
        mode = room.anonymize_mode if room.anonymize_mode in ("inherit", "on", "off") else "inherit"
        an = None if mode == "off" else await self.profiles.resolve(db, "anonymizer", room)
        # Локальная модель данные наружу не отправляет — обезличивание по умолчанию не нужно (только если включено явно для комнаты)
        anonymize = mode == "on" or (mode == "inherit" and not is_local and an is not None and bool(an.settings.enabled))
        out = {"llm_ready": bool(eff.enabled) and llm.available, "llm_profile": llm.name, "llm_source": llm.source, "llm_note": llm.note, "llm_reason": llm.reason,
               "anonymize": anonymize,
               "anonymizer_profile": an.name if (anonymize and an) else None,
               "anonymizer_ready": (not anonymize) or bool(an and an.settings.enabled),
               "llm_local": is_local, "llm_model": eff.model if eff.enabled else None, "warnings": [], "input_chars": None, "once": bool(once_choice),
               "llm_api_type": eff.type if not is_local else "local"}
        tokens, tnote = eff.output_limit(purpose)
        out["max_output_tokens"], out["max_output_note"] = tokens, tnote
        lm = self.local_llm.limits(llm.settings)
        if lm is not None and llm.available:
            fs = await asyncio.to_thread(self.local_llm.file_state, lm)
            if not self.local_llm.model_enabled(lm):
                out["llm_ready"] = False
                out["warnings"].append(f"Локальная модель {lm.title} не включена на сервере. Администратор включает её в разделе «Языковая модель (LLM)».")
            elif fs["state"] != "ok":
                out["llm_ready"] = False
                out["warnings"].append("Локальная языковая модель не загружена или повреждена. Администратор может скачать её в разделе «Языковая модель (LLM)».")
            else:
                chars = len(await self.transcript_text(db, meeting, await self._tz(db)))
                out["input_chars"] = chars
                if lm.light and chars > lm.warn_input_chars:
                    out["warnings"].append(f"Стенограмма длинная ({chars:,} знаков)".replace(",", " ") + f". Локальная модель {lm.title} — облегчённая: на длинных встречах качество может быть "
                                           "ниже, чем у более крупных моделей. Результат стоит проверить; для важных встреч лучше использовать внешнюю модель.")
        if out["llm_ready"] and out["input_chars"] is None:
            out["input_chars"] = len(await self.transcript_text(db, meeting, await self._tz(db)))
        if out["llm_ready"] and out["input_chars"] is not None:
            pr = await self._svc.get(db, "protocol")
            limit = min(pr.max_input_chars, lm.max_input_chars) if lm else pr.max_input_chars   # type: ignore[attr-defined]
            out["forecast"] = llm_stats.forecast(await llm_stats.recent_runs(db, 600), kind=purpose, model=eff.model, local=is_local,
                                                 profile="" if is_local else llm.name, chars=out["input_chars"], limit=limit)
        return out

    def _emit(self, category: str, event: str, rec: Protocol, started: datetime, *, level: str = "info", message: str | None = None,
              data: dict | None = None) -> None:
        if self.journal is None:
            return
        ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        self.journal.emit(category, event, level=level, meeting_id=str(rec.meeting_id), message=message or f"{rec.kind}: {ms} мс",
                          data={"kind": rec.kind, "duration_ms": ms, "created_by": rec.created_by, **(data or {})})

    async def _generate(self, db: AsyncSession, meeting_id: uuid.UUID, kind: str, instruction: str, *, once: str | None = None,
                        info: dict | None = None) -> tuple[str, dict]:
        info = info if info is not None else {}
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            raise SettingsError("Встреча не найдена")
        room = meeting.room
        purpose = "summary" if kind == "summary" else "protocol"
        try:
            once_choice = parse_once(once)
        except ValueError as exc:
            raise SettingsError(str(exc)) from None
        llm_res = await resolve_llm(self.profiles, self.local_llm, db, room, meeting, purpose, once=once_choice)   # системная → комната → встреча; для резюме своя цепочка
        if not llm_res.available:
            raise LlmError("unavailable", llm_res.reason or "")
        llm_cfg = llm_res.settings
        eff_cfg, is_local = self.local_llm.effective(llm_cfg)
        lm = self.local_llm.limits(llm_cfg)
        info.update(model=eff_cfg.model, model_title=lm.title if lm else llm_res.name, llm_local=is_local, llm_type="local" if is_local else eff_cfg.type,
                    api_type="local" if is_local else eff_cfg.type, llm_profile=llm_res.name, llm_source=llm_res.source)
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
        llm = self.local_llm.client(llm_cfg, ca_file=self._ca(), transport=self._transports.get("llm"), purpose=purpose)  # type: ignore[arg-type]
        instruction = instruction.strip() or await self.default_instruction(db, meeting, kind)
        limit = min(pr_cfg.max_input_chars, lm.max_input_chars) if lm else pr_cfg.max_input_chars   # type: ignore[attr-defined]
        warnings: list[str] = []
        if lm is not None and lm.light and len(clean.text) > lm.warn_input_chars:
            warnings.append(f"Длинная стенограмма обработана облегчённой локальной моделью {lm.title}: качество может быть ниже, чем у более крупных моделей — проверьте результат.")
        info["llm_started"] = datetime.now(timezone.utc)
        try:
            pipe = await run_llm_pipeline(llm, kind=kind, instruction=instruction, text=clean.text, limit=limit, local=lm, anonymized=do_anonymize,
                                          has_sources=bool(materials.chat_messages or materials.whiteboard_shapes), mixed=text is not transcript,
                                          external_structured=getattr(pr_cfg, "external_mode", "free") == "structured")
        finally:
            info["llm_finished"] = datetime.now(timezone.utc)
            info["llm_stats"] = dict(llm.stats)
        warnings += pipe.warnings
        out = pipe.text
        meta = {"model": eff_cfg.model, "llm_type": "local" if is_local else eff_cfg.type, "llm_local": is_local, "warnings": warnings, "truncated": pipe.truncated,  # type: ignore[attr-defined]
                "llm_calls": pipe.calls, "parts": pipe.parts, "structured": pipe.structured or None,
                "prompt_tokens": pipe.prompt_tokens, "completion_tokens": pipe.completion_tokens, "anonymized_chunks": clean.chunks,
                "anonymized_replaced": clean.replaced, "anonymized": do_anonymize, "llm_profile": llm_res.name, "llm_source": llm_res.source, "llm_note": llm_res.note,
                "anonymizer_profile": an_res.name if (do_anonymize and an_res) else None, "generated_at": utcnow().isoformat(),
                "sources": materials.meta()}
        st = llm.stats
        sstats = (pipe.structured or {}).get("stats") or {}
        meta.update(model_title=info["model_title"], api_type=info["api_type"], input_chars=len(clean.text), output_chars=len(out), chunks=pipe.parts,
                    retries=int(sstats.get("length_retries", 0)) + int(sstats.get("json_retries", 0)), length_hits=st["length"], finish=dict(st["finish"]),
                    max_tokens=st["max_tokens"] or None, limit_note=eff_cfg.output_limit(purpose)[1] or None, llm_source=llm_res.source)
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
