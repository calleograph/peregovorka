"""Вложения чата: проверка, имена, хранение, удаление.

Правила:
  * в базе — только метаданные (`meeting_chat_attachments`); сам файл — в общем файловом хранилище (services/filestore.py);
  * путь в хранилище формирует сервер (`<встреча>/<id вложения>.<расширение>`); имя файла от клиента — только отображаемое, очищенное;
  * тип определяется по СОДЕРЖИМОМУ (сигнатуры), а не по заголовку Content-Type от клиента; картинки показываются прямо в чате только если
    содержимое действительно png/jpeg/gif/webp; всё остальное отдаётся скачиванием (`Content-Disposition: attachment`, nosniff, sandbox);
  * размер, число файлов и допустимые расширения — в настройках («Вложения чата»); исполняемые и «активные» типы запрещены всегда.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import timedelta
from pathlib import PurePosixPath

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ChatAttachment, Meeting, utcnow
from .filestore import FileStore
from .settings import ChatFilesSettings
from .storage import StorageError

log = logging.getLogger("app.chat_files")

PENDING_TTL = timedelta(hours=2)           # загруженное, но не отправленное вложение живёт столько
INLINE_IMAGES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
_MIME = {
    "pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown", "csv": "text/csv", "zip": "application/zip",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text", "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation", "drawio": "application/vnd.jgraph.mxfile",
}
_ZIP_BASED = {"docx", "xlsx", "pptx", "odt", "ods", "odp", "zip"}
_NAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f\u202a-\u202e\u2066-\u2069]')


class AttachmentError(Exception):
    """Ошибка проверки вложения — текст безопасен для показа пользователю."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def clean_filename(raw: str | None) -> str:
    """Только имя файла (без пути), без управляющих символов и «разворота» текста, не длиннее 120 символов, расширение сохраняется."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"\s+", " ", _NAME_BAD.sub("_", name)).strip(" .")
    if not name:
        return "file"
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 119 - len(ext)] + "." + ext) if dot and len(ext) <= 10 else name[:120]
    return name


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def inspect(name: str, data: bytes, cfg: ChatFilesSettings) -> tuple[str, str, str]:
    """(расширение, mime, kind): проверка размера, расширения и соответствия содержимого. Иначе AttachmentError."""
    if not data:
        raise AttachmentError("Файл пустой")
    if len(data) > cfg.max_size_mb * 1024 * 1024:
        raise AttachmentError(f"Файл больше {cfg.max_size_mb} МБ", 413)
    ext = extension(name)
    if not ext or ext not in cfg.extensions():
        raise AttachmentError("Такой тип файла не разрешён" + (f" (.{ext})" if ext else " (у файла нет расширения)") +
                              ". Разрешены: " + ", ".join(sorted(cfg.extensions())))
    real = _sniff_image(data)
    if ext in INLINE_IMAGES:
        if real is None:
            raise AttachmentError("Содержимое файла не соответствует картинке")
        return real, INLINE_IMAGES[real], "image"        # расширение по факту содержимого
    if real is not None:
        raise AttachmentError("Содержимое файла — картинка, а расширение другое")
    if ext == "pdf" and not data.startswith(b"%PDF"):
        raise AttachmentError("Содержимое файла не соответствует PDF")
    if ext in _ZIP_BASED and not data.startswith(b"PK"):
        raise AttachmentError("Содержимое файла не соответствует заявленному типу")
    if data.startswith((b"MZ", b"\x7fELF")):
        raise AttachmentError("Исполняемые файлы загружать нельзя")
    return ext, _MIME.get(ext, "application/octet-stream"), "file"


def storage_key(meeting_id: uuid.UUID, attachment_id: uuid.UUID, ext: str) -> str:
    return f"{meeting_id}/{attachment_id}.{ext}"


class ChatFilesService:
    def __init__(self, files: FileStore, svc, journal=None):
        self._files = files
        self._svc = svc
        self.journal = journal

    async def settings(self, db: AsyncSession) -> ChatFilesSettings:
        return await self._svc.get(db, "chat_files")  # type: ignore[return-value]

    def _fail(self, event: str, exc: Exception, meeting_id: uuid.UUID | None, extra: dict | None = None) -> None:
        log.error("Вложение чата: %s", event, extra={"error": str(exc), "meeting_id": str(meeting_id) if meeting_id else None})
        if self.journal is not None:
            self.journal.emit("storage", event, level="error", meeting_id=str(meeting_id) if meeting_id else None, message=str(exc)[:300], data=extra)

    async def store(self, db: AsyncSession, meeting: Meeting, *, uploader_type: str, uploader_id: uuid.UUID, raw_name: str, data: bytes) -> ChatAttachment:
        """Проверяет и сохраняет файл. Если хранилище недоступно — понятная ошибка (503) и запись в журнале; метаданные при этом не создаются."""
        cfg = await self.settings(db)
        if not cfg.enabled:
            raise AttachmentError("Вложения в чат отключены администратором", 403)
        name = clean_filename(raw_name)
        ext, mime, kind = inspect(name, data, cfg)
        if kind == "image" and extension(name) != ext:
            name = name.rsplit(".", 1)[0] + "." + ext if "." in name else name + "." + ext
        aid = uuid.uuid4()
        try:
            backend, profile_id = await self._files.chat_backend(db)
            await asyncio.to_thread(backend.write_bytes, storage_key(meeting.id, aid, ext), data)
        except StorageError as exc:
            self._fail("chat_file_save_failed", exc, meeting.id, {"name": name, "size": len(data)})
            raise AttachmentError("Не удалось сохранить файл: хранилище вложений недоступно. Сообщите администратору — ошибка записана в журнал.", 503) from None
        att = ChatAttachment(id=aid, meeting_id=meeting.id, uploader_type=uploader_type, uploader_id=uploader_id, name=name, mime=mime, size=len(data),
                             kind=kind, storage_key=storage_key(meeting.id, aid, ext), profile_id=profile_id)
        db.add(att)
        await db.commit()
        return att

    async def read(self, db: AsyncSession, att: ChatAttachment) -> bytes:
        try:
            backend = await self._files.chat_backend_for(db, att.profile_id)
            return await asyncio.to_thread(backend.read_bytes, att.storage_key)
        except StorageError as exc:
            self._fail("chat_file_read_failed", exc, att.meeting_id, {"attachment": str(att.id)})
            raise AttachmentError("Файл сейчас недоступен: хранилище не отвечает.", 503) from None

    async def remove_files(self, db: AsyncSession, atts: list[ChatAttachment]) -> int:
        """Удаляет файлы вложений из хранилища (сбой одного не мешает остальным и пишется в журнал). Строки в базе удаляет вызывающий."""
        failed = 0
        for att in atts:
            try:
                backend = await self._files.chat_backend_for(db, att.profile_id)
                await asyncio.to_thread(backend.delete, att.storage_key)
            except StorageError as exc:
                failed += 1
                self._fail("chat_file_delete_failed", exc, att.meeting_id, {"attachment": str(att.id)})
        return failed

    async def delete_for_meetings(self, db: AsyncSession, meeting_ids: list[uuid.UUID]) -> int:
        atts = list((await db.execute(select(ChatAttachment).where(ChatAttachment.meeting_id.in_(meeting_ids)))).scalars().all()) if meeting_ids else []
        if not atts:
            return 0
        await self.remove_files(db, atts)
        await db.execute(delete(ChatAttachment).where(ChatAttachment.id.in_([a.id for a in atts])))
        return len(atts)

    async def purge_orphans(self, db: AsyncSession) -> int:
        """Загружено, но так и не отправлено (закрыли вкладку, убрали превью): удаляем файл и строку."""
        old = list((await db.execute(select(ChatAttachment).where(ChatAttachment.message_id.is_(None),
                                                                   ChatAttachment.created_at < utcnow() - PENDING_TTL))).scalars().all())
        if not old:
            return 0
        await self.remove_files(db, old)
        await db.execute(delete(ChatAttachment).where(ChatAttachment.id.in_([a.id for a in old])))
        return len(old)


def attachment_out(a: ChatAttachment) -> dict:
    return {"id": str(a.id), "name": a.name, "mime": a.mime, "size": a.size, "kind": a.kind}


def safe_key(key: str) -> bool:
    p = PurePosixPath(key)
    return not p.is_absolute() and ".." not in p.parts
