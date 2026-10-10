"""Перенос записей между локальным диском и внешним хранилищем записей (SMB или папка-профиль) — фоновое задание администратора.

Главный принцип: в любой момент — в том числе при сбое, обрыве сети и перезапуске сервера — остаётся хотя бы одна проверенная копия файла, а запись в базе
всегда указывает на копию, которая точно существует.

Каждый файл переносится независимо от остальных:
  1. проверяется, что файл можно трогать: встреча завершена и «устоялась», финализация, сведение общей записи и выгрузка по встрече не идут, файл не писался недавно;
  2. проверяется источник, свободное место назначения и то, что назначение — действительно подключённый том (метка тома);
  3. файл копируется потоком (в память не читается) во временное имя, затем размер и SHA-256 назначения сверяются с источником;
  4. только после этого запись в базе переключается на новое место (ссылки в истории, права и воспроизведение не меняются — сервер сам находит файл);
  5. источник удаляется НЕ сразу, а по истечении отсрочки `GRACE_S` (чтобы не оборвать уже идущее воспроизведение), и только если копия на новом месте на месте и совпадает по размеру.
Сбой между шагами: после копирования до переключения — на прежнем месте остался источник (на назначении — лишний файл, который перезапишется при повторе); после переключения до
удаления — копии есть в обоих местах (лишняя удалит уборка); копирование оборвано — источник цел, временный файл перезапишется. Задание возобновляемо: состояние каждого файла
хранится в базе, после перезапуска «выполняется» превращается в «в очереди».
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Meeting, Recording, StorageTransfer, StorageTransferItem, utcnow
from .recordings import delete_recording_file
from .storage import CHUNK, LocalStorage, StorageError, StorageNotFound

log = logging.getLogger("app.transfer")

RESERVE = 64 << 20          # запас свободного места на назначении сверх размера файла
RECENT_S = 120              # файл, изменённый позже, считается «ещё записывается»
SETTLE_S = 300              # встреча должна быть завершена не менее стольких секунд назад (финализация, сведение и выгрузка успевают закончиться)
GRACE_S = 300               # через сколько после переключения удалять источник
READ_QUIET_S = 120          # сколько секунд после последнего чтения файл считается «слушаемым» и источник не удаляется
DIRECTIONS = ("to_external", "to_local")


class TransferError(Exception):
    """Ошибка одного файла (понятный текст для администратора)."""


class Unavailable(TransferError):
    """Хранилище недоступно или не хватает места: продолжать остальные файлы бессмысленно — задание останавливается, его можно возобновить."""


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def sha256_storage(storage, rel: str, size: int) -> str:
    h = hashlib.sha256()
    if size > 0:
        for chunk in storage.read_range(rel, 0, size - 1):
            h.update(chunk)
    return h.hexdigest()


def _skip(text: str) -> TransferError:
    e = TransferError(text)
    e.skip = True  # type: ignore[attr-defined]
    return e


class TransferService:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession], protocols, journal=None,
                 audit: Callable[..., Awaitable[None]] | None = None, idle_s: float = 5.0):
        self._sm, self._ps, self.journal, self._audit, self._idle = session_maker, protocols, journal, audit, idle_s
        self._wake = asyncio.Event()

    @property
    def _root(self) -> Path:
        return Path(self._ps._s.recordings_path)

    # ------------------------------------------------------------------------------------------- задания
    async def create(self, db: AsyncSession, direction: str, *, meeting_id: uuid.UUID | None, actor: str) -> StorageTransfer:
        if direction not in DIRECTIONS:
            raise TransferError("Направление: to_external или to_local")
        cfg = await self._ps._svc.get(db, "audio_storage")
        if not getattr(cfg, "enabled", False):
            raise TransferError("Внешнее хранилище записей не настроено: включите его в «Файловые хранилища» и «Аудиозаписи».")
        if not getattr(cfg, "profile_id", ""):
            raise TransferError("Перенос работает с хранилищем из раздела «Файловые хранилища»: выберите профиль в «Аудиозаписи».")
        busy = (await db.execute(select(StorageTransfer.id).where(StorageTransfer.state.in_(("queued", "running"))).limit(1))).first()
        if busy:
            raise TransferError("Уже выполняется другой перенос — дождитесь его завершения или остановите.")
        stmt = select(Recording.id, Recording.size_bytes).where(Recording.status == "ready")
        stmt = stmt.where(Recording.export_status != "exported") if direction == "to_external" else stmt.where(Recording.export_status == "exported")
        if meeting_id is not None:
            stmt = stmt.where(Recording.meeting_id == meeting_id)
        rows = (await db.execute(stmt.order_by(Recording.created_at, Recording.id))).all()
        job = StorageTransfer(direction=direction, scope="meeting" if meeting_id else "all", meeting_id=meeting_id, state="queued", total=len(rows),
                              bytes_total=sum(int(r[1] or 0) for r in rows), created_by=actor[:200])
        db.add(job)
        await db.flush()
        for rid, size in rows:
            db.add(StorageTransferItem(transfer_id=job.id, recording_id=rid, state="pending", bytes=int(size or 0)))
        if self._audit is not None:
            await self._audit(db, actor=actor, action="storage.transfer.start", target_id=str(job.id),
                              details={"direction": direction, "files": len(rows), "bytes": job.bytes_total, "meeting": str(meeting_id) if meeting_id else None})
        await db.commit()
        self._wake.set()
        return job

    async def cancel(self, db: AsyncSession, job_id: uuid.UUID, actor: str) -> StorageTransfer | None:
        job = await db.get(StorageTransfer, job_id)
        if job is None:
            return None
        if job.state in ("queued", "running", "failed"):
            job.state, job.finished_at = "cancelled", utcnow()
            if self._audit is not None:
                await self._audit(db, actor=actor, action="storage.transfer.cancel", target_id=str(job.id), details={"done": job.done, "total": job.total})
            await db.commit()
        return job

    async def resume(self, db: AsyncSession, job_id: uuid.UUID, actor: str) -> StorageTransfer | None:
        """Продолжить остановленное (из-за недоступности хранилища или места) задание: перенесённые файлы пропускаются, остальные пробуются снова."""
        job = await db.get(StorageTransfer, job_id)
        if job is None or job.state != "failed":
            return job
        busy = (await db.execute(select(StorageTransfer.id).where(StorageTransfer.state.in_(("queued", "running")), StorageTransfer.id != job.id).limit(1))).first()
        if busy:
            raise TransferError("Уже выполняется другой перенос.")
        await db.execute(update(StorageTransferItem).where(StorageTransferItem.transfer_id == job.id, StorageTransferItem.state.in_(("failed", "skipped"))).values(state="pending", error=None))
        job.state, job.error, job.finished_at, job.skipped, job.failed = "queued", None, None, 0, 0
        if self._audit is not None:
            await self._audit(db, actor=actor, action="storage.transfer.resume", target_id=str(job.id), details={"done": job.done, "total": job.total})
        await db.commit()
        self._wake.set()
        return job

    # ------------------------------------------------------------------------------------------- воркер
    async def recover(self) -> None:
        """После перезапуска сервера: «выполняется» → «в очереди» (продолжится с оставшихся файлов; уборку источников подхватит `sweep`)."""
        async with self._sm() as db:
            await db.execute(update(StorageTransfer).where(StorageTransfer.state == "running").values(state="queued"))
            await db.commit()

    async def run(self) -> None:
        await self.recover()
        while True:
            try:
                await self.sweep()
                async with self._sm() as db:
                    job_id = (await db.execute(select(StorageTransfer.id).where(StorageTransfer.state == "queued").order_by(StorageTransfer.created_at).limit(1))).scalar_one_or_none()
                if job_id is None:
                    try:
                        await asyncio.wait_for(self._wake.wait(), self._idle)
                    except asyncio.TimeoutError:
                        pass
                    self._wake.clear()
                    continue
                await self.process(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — воркер не должен падать из-за одного задания
                log.exception("Ошибка воркера переноса")
                await asyncio.sleep(self._idle)

    async def process(self, job_id: uuid.UUID) -> None:
        async with self._sm() as db:
            job = await db.get(StorageTransfer, job_id)
            if job is None or job.state != "queued":
                return
            job.state, job.started_at = "running", job.started_at or utcnow()
            await db.commit()
        stop_error: str | None = None
        while True:
            async with self._sm() as db:
                job = await db.get(StorageTransfer, job_id)
                if job.state != "running":                      # остановлено администратором
                    return
                item = (await db.execute(select(StorageTransferItem).where(StorageTransferItem.transfer_id == job_id, StorageTransferItem.state == "pending")
                                         .order_by(StorageTransferItem.id).limit(1))).scalar_one_or_none()
                if item is None:
                    break
                try:
                    note = await self._one(db, job, item)
                    item.state, item.error = "done", note
                    job.done += 1
                    job.bytes_done += item.bytes
                except Unavailable as exc:
                    item.state, item.error = "pending", str(exc)[:480]
                    stop_error = str(exc)[:480]
                except TransferError as exc:
                    skipped = bool(getattr(exc, "skip", False))
                    item.state, item.error = ("skipped" if skipped else "failed"), str(exc)[:480]
                    job.skipped += skipped
                    job.failed += not skipped
                    if not skipped:
                        self._emit("storage_transfer_file_failed", f"Перенос записи не удался: {exc}", rec=str(item.recording_id), job=str(job.id))
                except Exception as exc:  # noqa: BLE001
                    log.exception("Сбой переноса файла", extra={"recording": str(item.recording_id)})
                    item.state, item.error = "failed", f"Непредвиденная ошибка: {type(exc).__name__}"
                    job.failed += 1
                await db.commit()
                if stop_error:
                    break
        async with self._sm() as db:
            job = await db.get(StorageTransfer, job_id)
            if job.state != "running":
                return
            job.finished_at = utcnow()
            if stop_error:
                job.state, job.error = "failed", stop_error
            else:
                job.state = "done"
            if self._audit is not None:
                await self._audit(db, actor="система (перенос данных)", action="storage.transfer.finish", target_id=str(job.id),
                                  details={"state": job.state, "done": job.done, "skipped": job.skipped, "failed": job.failed, "total": job.total, "error": job.error})
            await db.commit()
            self._emit("storage_transfer_finished", f"Перенос записей: {job.state}, перенесено {job.done} из {job.total}, пропущено {job.skipped}, ошибок {job.failed}",
                       level="warn" if job.state != "done" or job.failed else "info", job=str(job.id))

    def _emit(self, event: str, message: str, level: str = "error", **data) -> None:
        if self.journal is not None:
            self.journal.emit("storage", event, level=level, message=message[:300], data=data)

    # ------------------------------------------------------------------------------------------- один файл
    async def _one(self, db: AsyncSession, job: StorageTransfer, item: StorageTransferItem) -> str | None:
        rec = await db.get(Recording, item.recording_id)
        if rec is None:
            raise _skip("запись уже удалена")
        if rec.status != "ready" or rec.file_state == "missing":
            raise _skip("файл не готов или отсутствует в хранилище")
        meeting = await db.get(Meeting, rec.meeting_id)
        if meeting is None or meeting.ended_at is None:
            raise _skip("встреча ещё идёт")
        if (utcnow() - meeting.ended_at).total_seconds() < SETTLE_S:
            raise _skip("встреча завершилась только что: материалы ещё обрабатываются")
        if self._ps.is_busy(rec.meeting_id):
            raise _skip("файлы встречи обрабатываются (финализация, сведение общей записи или выгрузка)")
        building = (await db.execute(select(Recording.id).where(Recording.meeting_id == rec.meeting_id, Recording.kind == "mix_audio", Recording.status == "processing").limit(1))).first()
        if building:
            raise _skip("общая запись этой встречи ещё собирается")
        if job.direction == "to_external":
            if rec.export_status == "exported":
                if rec.sha256 and (self._root / rec.path).is_file():
                    item.cleanup_at = utcnow()                  # «хвост» прерванного переноса: копия во внешнем хранилище уже есть — уборка проверит и удалит лишнюю локальную
                return "уже во внешнем хранилище"
            return await self._to_external(db, rec, item)
        if rec.export_status != "exported":
            return "уже на локальном диске"
        return await self._to_local(db, rec, item)

    async def _storage(self, db: AsyncSession):
        storage = await self._ps._audio_storage(db)
        if storage is None:
            raise Unavailable("Внешнее хранилище записей недоступно или выключено.")
        base = getattr(storage, "_base", storage)
        if isinstance(base, LocalStorage) and not getattr(base, "_marker", None):
            raise Unavailable("Хранилище-папка не отмечено как подключённый том: нажмите «Проверить» у хранилища (ставится метка), иначе при отключении сетевой папки файлы попали бы на локальный диск.")
        try:
            await asyncio.to_thread(storage.probe)
        except StorageError as exc:
            raise Unavailable(f"Внешнее хранилище недоступно: {exc}") from None
        return storage

    async def _to_external(self, db: AsyncSession, rec: Recording, item: StorageTransferItem) -> str | None:
        src = self._root / rec.path
        if not src.is_file():
            raise TransferError("локальный файл не найден")
        st = src.stat()
        if time.time() - st.st_mtime < RECENT_S:
            raise _skip("файл изменён меньше двух минут назад (возможно, ещё записывается)")
        storage = await self._storage(db)
        size = st.st_size
        try:
            _, free = await asyncio.to_thread(storage.volume)
        except (StorageError, OSError, NotImplementedError):
            free = None                                                   # том не сообщает свободное место — не блокируем, ошибка записи всё равно будет поймана
        if free is not None and free < size + RESERVE:
            raise Unavailable(f"На внешнем хранилище не хватает места: нужно {size + RESERVE} байт, свободно {free}.")
        digest = await asyncio.to_thread(sha256_file, src)
        try:
            loc = await asyncio.to_thread(storage.copy_in, rec.path, str(src))
            got = await asyncio.to_thread(storage.size_of, rec.path)
            if got != size:
                raise TransferError(f"размер на назначении ({got}) не совпал с источником ({size})")
            if await asyncio.to_thread(sha256_storage, storage, rec.path, size) != digest:
                raise TransferError("контрольная сумма на назначении не совпала с источником")
            if os.stat(src).st_size != size or await asyncio.to_thread(sha256_file, src) != digest:
                raise TransferError("файл изменился во время копирования")
        except StorageError as exc:
            await self._drop(storage, rec.path)
            raise Unavailable(f"Не удалось записать во внешнее хранилище: {exc}") from None
        except TransferError:
            await self._drop(storage, rec.path)
            raise
        rec.export_status, rec.export_location, rec.export_error, rec.exported_at, rec.sha256, rec.size_bytes = "exported", loc, None, utcnow(), digest, size
        item.cleanup_at = utcnow() + timedelta(seconds=GRACE_S)
        await db.commit()                                                 # переключение в базе — до удаления источника; источник уберёт sweep после отсрочки
        return None

    async def _to_local(self, db: AsyncSession, rec: Recording, item: StorageTransferItem) -> str | None:
        storage = await self._storage(db)
        try:
            size = await asyncio.to_thread(storage.size_of, rec.path)
        except StorageError as exc:
            if not await asyncio.to_thread(storage.exists, rec.path):
                raise TransferError("файл не найден во внешнем хранилище") from None
            raise Unavailable(f"Внешнее хранилище недоступно: {exc}") from None
        root = self._root
        free = shutil.disk_usage(root if root.exists() else root.parent).free
        if free < size + RESERVE:
            raise Unavailable(f"На локальном диске не хватает места: нужно {size + RESERVE} байт, свободно {free}.")
        dst = root / rec.path
        tmp = dst.with_name(dst.name + ".transfer")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(storage.copy_out, rec.path, str(tmp))
            if tmp.stat().st_size != size:
                raise TransferError(f"размер на назначении ({tmp.stat().st_size}) не совпал с источником ({size})")
            digest = await asyncio.to_thread(sha256_file, tmp)
            want = rec.sha256 or await asyncio.to_thread(sha256_storage, storage, rec.path, size)
            if digest != want:
                raise TransferError("контрольная сумма на назначении не совпала с источником")
            await asyncio.to_thread(os.replace, tmp, dst)
        except StorageError as exc:
            tmp.unlink(missing_ok=True)
            raise Unavailable(f"Не удалось прочитать из внешнего хранилища: {exc}") from None
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        rec.export_status, rec.export_location, rec.export_error, rec.exported_at, rec.sha256, rec.size_bytes = "local", None, None, None, digest, size
        item.cleanup_at = utcnow() + timedelta(seconds=GRACE_S)
        await db.commit()
        return None

    @staticmethod
    async def _drop(storage, rel: str) -> None:
        try:
            await asyncio.to_thread(storage.delete, rel)
        except Exception:  # noqa: BLE001 — недописанную копию на назначении убираем, если получится; источник цел
            pass

    # ------------------------------------------------------------------------------------------- уборка источников
    async def sweep(self) -> int:
        """Удалить источники перенесённых файлов, у которых истекла отсрочка. Удаляется только если запись в базе указывает на новое место и копия там есть
        (проверка размера); иначе источник остаётся. Недоступное хранилище — уборка откладывается."""
        removed = 0
        async with self._sm() as db:
            due = (await db.execute(select(StorageTransferItem, StorageTransfer.direction).join(StorageTransfer, StorageTransfer.id == StorageTransferItem.transfer_id)
                                    .where(StorageTransferItem.cleanup_at.is_not(None), StorageTransferItem.cleanup_at <= utcnow()).order_by(StorageTransferItem.id).limit(100))).all()
            for item, direction in due:
                rec = await db.get(Recording, item.recording_id)
                try:
                    if rec is None:
                        item.cleanup_at = None
                    elif self._ps.is_busy(rec.meeting_id) or self._ps.is_read(rec.id, READ_QUIET_S):
                        item.cleanup_at = utcnow() + timedelta(seconds=60)     # файл читают (слушают запись) или обрабатывают — повторим позже
                    elif await self._clean_one(db, rec, direction):
                        removed += 1
                        item.cleanup_at = None
                    else:
                        item.cleanup_at = None
                except Unavailable:
                    item.cleanup_at = utcnow() + timedelta(seconds=300)
                except (OSError, StorageError) as exc:
                    item.error = f"перенесено; источник удалить не удалось: {exc}"[:480]
                    item.cleanup_at = utcnow() + timedelta(seconds=600)
                await db.commit()
        return removed

    async def _clean_one(self, db: AsyncSession, rec: Recording, direction: str) -> bool:
        """Удалить источник, ТОЛЬКО убедившись заново (размер и SHA-256), что копия на новом месте цела. Если копия испорчена, а источник цел — запись в базе возвращается на источник:
        проверенная копия остаётся всегда."""
        local = self._root / rec.path
        if direction == "to_external":
            if rec.export_status != "exported" or not rec.sha256 or not local.is_file():
                return False
            storage = await self._storage(db)
            ok = False
            try:
                ok = await asyncio.to_thread(storage.size_of, rec.path) == rec.size_bytes and await asyncio.to_thread(sha256_storage, storage, rec.path, rec.size_bytes) == rec.sha256
            except StorageNotFound:
                ok = False
            if not ok:
                if local.stat().st_size == rec.size_bytes and await asyncio.to_thread(sha256_file, local) == rec.sha256:
                    rec.export_status, rec.export_location, rec.exported_at = "local", None, None          # внешняя копия испорчена или пропала — «живая» копия локальная
                    rec.export_error = "перенос отменён: копия во внешнем хранилище не прошла проверку"
                    self._emit("storage_transfer_copy_corrupt", "Копия во внешнем хранилище не прошла проверку контрольной суммы — запись возвращена на локальный диск", rec=str(rec.id))
                    return False
                raise Unavailable("копия во внешнем хранилище не прошла проверку, а локальный файл изменился — ничего не удалено")
            await asyncio.to_thread(delete_recording_file, self._ps._s.recordings_path, rec.path)
            return True
        if rec.export_status != "local" or not local.is_file():
            return False
        storage = await self._storage(db)
        if local.stat().st_size != rec.size_bytes or await asyncio.to_thread(sha256_file, local) != rec.sha256:
            if await asyncio.to_thread(storage.exists, rec.path):
                rec.export_status, rec.export_error = "exported", "перенос отменён: локальная копия не прошла проверку"       # локальная копия испорчена — «живая» внешняя
                self._emit("storage_transfer_copy_corrupt", "Локальная копия не прошла проверку контрольной суммы — запись оставлена во внешнем хранилище", rec=str(rec.id))
            return False
        if not await asyncio.to_thread(storage.exists, rec.path):
            return False
        await asyncio.to_thread(storage.delete, rec.path)
        return True

    # ------------------------------------------------------------------------------------------- для интерфейса
    async def summary(self, db: AsyncSession, job: StorageTransfer) -> dict:
        counts = dict((await db.execute(select(StorageTransferItem.state, func.count()).where(StorageTransferItem.transfer_id == job.id).group_by(StorageTransferItem.state))).all())
        cleanup = (await db.execute(select(func.count()).select_from(StorageTransferItem).where(StorageTransferItem.transfer_id == job.id, StorageTransferItem.cleanup_at.is_not(None)))).scalar_one()
        return {"id": str(job.id), "direction": job.direction, "scope": job.scope, "meeting_id": str(job.meeting_id) if job.meeting_id else None, "state": job.state,
                "total": job.total, "done": job.done, "skipped": job.skipped, "failed": job.failed, "pending": int(counts.get("pending", 0)), "cleanup_pending": int(cleanup),
                "bytes_total": job.bytes_total, "bytes_done": job.bytes_done, "error": job.error, "created_by": job.created_by,
                "created_at": job.created_at, "started_at": job.started_at, "finished_at": job.finished_at}
