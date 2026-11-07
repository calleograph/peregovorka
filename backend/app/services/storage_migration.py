"""Перенос данных между двумя профилями хранилищ (локальный том или SMB, в любой комбинации).

Главный принцип: источник не меняется, пока все файлы не скопированы, не сверены и система не работает на новом месте. Очистка источника — отдельное действие администратора.

Этапы задания:
    scanning    обход источника: список файлов, разбивка по видам данных, свободное место, замер скорости
    ready       оценка показана администратору, ничего не скопировано
    copying     файлы идут потоком между хранилищами (локальный диск сервера не нужен); SHA-256 считается при чтении и сверяется с копией
    attention   копирование закончилось с ошибками: можно повторить проблемные файлы или отменить; источник цел
    verifying   повторный обход: новые и изменившиеся файлы докопируются, сверяется количество, размер и соответствие записям базы
    switching   функции переводятся на новое хранилище, проверяется чтение и запись; при неудаче всё возвращается как было
    switched    система работает на новом месте, источник цел
    cleaning    после подтверждения администратора: дописываются файлы, успевшие попасть в источник, и удаляются только перенесённые и не изменившиеся
    cleaned     отчёт готов
    waiting     хранилище недоступно: задание само продолжит, когда оно вернётся
    failed, cancelled

Раскладка внутри профиля одинакова на всех хранилищах (Audio/, Video/, Transcripts/, …): относительные пути не меняются, поэтому записи базы, которые хранят путь, остаются верными.
Функция «Протоколы и материалы» настроена одной строкой на четыре каталога, поэтому они переносятся только вместе: иначе часть документов перестала бы открываться.
Файлы копируются байт в байт: что было зашифровано, остаётся зашифрованным тем же ключом.
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from threading import Event

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import ChatAttachment, ChatFile, Protocol, Recording, StorageMigration, StorageMigrationItem, StorageTransfer, utcnow
from .auto_backup import FOLDER as BACKUPS
from .filestore import AUDIO, BOARDS, CHAT, FileStore, LOGS, PROTOCOLS, PrefixedStorage, TRANSCRIPTS, VIDEO
from .settings import SettingsError
from .storage import CHUNK, MARKER, StorageError, StorageNotFound, StreamStopped

log = logging.getLogger("app.storage_migration")

GROUPS = {
    "audio": "Аудиозаписи встреч", "video": "Видеозаписи встреч и записи эфиров", "documents": "Протоколы, резюме, стенограммы, карты, переписка и схемы доски",
    "chat_files": "Вложения чатов и загруженные файлы", "journal": "Журнал событий", "backups": "Резервные копии настроек", "other": "Прочие файлы на хранилище",
}
CATEGORIES = {
    "audio": "Аудиозаписи", "video": "Видеозаписи и записи эфиров", "transcripts": "Стенограммы", "protocols": "Протоколы, резюме и карты разговоров", "chat": "Переписка встреч",
    "boards": "Схемы доски", "chat_files": "Вложения чатов и загруженные файлы", "journal": "Журнал событий", "backups": "Резервные копии", "other": "Прочие файлы",
}
GROUP_OF = {"audio": "audio", "video": "video", "transcripts": "documents", "protocols": "documents", "chat": "documents", "boards": "documents", "chat_files": "chat_files",
            "journal": "journal", "backups": "backups", "other": "other"}
ACTIVE = ("scanning", "copying", "verifying", "switching", "cleaning", "waiting")          # задание занимает очередь
BUSY = (*ACTIVE, "ready", "attention")                                                    # нельзя начать второе на те же хранилища
RETRIES = 3                  # попыток на файл до состояния failed
WAIT_S = 30                  # пауза между проверками недоступного хранилища
RESERVE = 256 << 20          # запас свободного места на цели
SPEED_SAMPLE = 8 << 20       # объём пробной записи для оценки длительности
CLEAN_GRACE_S = 300          # очистка не раньше: идущие воспроизведения читают источник
MAX_EVENTS = 300
DELTA_PASSES = 4


# корзины и снимки Samba, QNAP, Synology, Windows, NetApp
SERVICE_DIRS = {".deleted", ".recycle", "@recycle", "#recycle", "$recycle.bin", "system volume information", ".snapshot", ".snapshots", "@eadir", ".trash", ".trashes", "lost+found", ".streams"}
SERVICE_FILES = {"thumbs.db", ".ds_store", "desktop.ini"}


class MigrationError(Exception):
    """Понятный отказ администратору."""


class Unavailable(Exception):
    """Хранилище не отвечает: дело не в файле, продолжать сейчас бессмысленно."""


class Stopped(StreamStopped):
    """Администратор отменил задание, файл не докопирован."""


class Conflict(Exception):
    """На цели уже лежит другой файл с тем же именем."""


def category_of(path: str) -> str:
    top, _, rest = path.partition("/")
    if top == CHAT:
        return "chat_files" if rest.startswith("files/") else "chat"
    return {AUDIO: "audio", VIDEO: "video", TRANSCRIPTS: "transcripts", PROTOCOLS: "protocols", BOARDS: "boards", LOGS: "journal", BACKUPS: "backups"}.get(top, "other")


def scanned(path: str) -> bool:
    """Служебные файлы не переносятся: метка тома у каждого хранилища своя, временные — остатки прерванных записей, а корзины и снимки принадлежат серверу хранения, не приложению."""
    parts = path.split("/")
    name = parts[-1]
    if SERVICE_DIRS & {p.lower() for p in parts[:-1]} or name.lower() in SERVICE_FILES:
        return False
    return name != MARKER and not name.endswith((".tmp", ".partial")) and not name.startswith(".peregovorka-write-test")


def sha256_of(storage, rel: str, size: int, stop: Event | None = None) -> str:
    h = hashlib.sha256()
    if size > 0:
        for chunk in storage.read_range(rel, 0, size - 1):
            if stop is not None and stop.is_set():
                raise Stopped
            h.update(chunk)
    return h.hexdigest()


def copy_file(src, dst, rel: str, mtime: float, *, overwrite: bool, stop: Event) -> tuple[str, int]:
    """Поток src → dst через временное имя на dst, сверка копии с источником. Ответ: (sha256, размер). Выполняется в потоке."""
    size, _ = src.stat_of(rel)
    if dst.exists(rel):
        same_size = dst.stat_of(rel)[0] == size
        if same_size:
            digest = sha256_of(src, rel, size, stop)
            if sha256_of(dst, rel, size, stop) == digest:
                return digest, size                       # повторный запуск: копия уже на месте
        if not overwrite:
            raise Conflict("На целевом хранилище уже есть другой файл с этим именем. Проверьте его и повторите с заменой.")
    h = hashlib.sha256()

    def chunks():
        got = 0
        if size > 0:
            for chunk in src.read_range(rel, 0, size - 1):
                if stop.is_set():
                    raise Stopped
                h.update(chunk)
                got += len(chunk)
                yield chunk
        if got != size:
            raise StorageError("файл изменился во время копирования")

    dst.write_stream(rel, chunks(), mtime)
    digest = h.hexdigest()
    if sha256_of(dst, rel, size, stop) != digest:
        dst.delete(rel)
        raise StorageError("контрольная сумма копии не совпала с источником: копия удалена")
    return digest, size


def speed_sample(dst) -> float | None:
    """Скорость записи на цель: байт в секунду. Для оценки длительности. Выполняется в потоке."""
    rel = f".peregovorka-write-test-{uuid.uuid4().hex[:8]}"
    t0 = time.monotonic()
    try:
        dst.write_stream(rel, iter([b"\0" * CHUNK] * (SPEED_SAMPLE // CHUNK)))
        return SPEED_SAMPLE / max(time.monotonic() - t0, 0.001)
    except StorageError:
        return None
    finally:
        try:
            dst.delete(rel)
        except StorageError:
            pass


class StorageMigrations:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession], files: FileStore, svc, protocols=None, journal=None,
                 audit: Callable[..., Awaitable[None]] | None = None, idle_s: float = 3.0):
        self._sm, self._files, self._svc, self._ps, self.journal, self._audit, self._idle = session_maker, files, svc, protocols, journal, audit, idle_s
        self._wake = asyncio.Event()
        self.verify_backup: Callable[[str], Awaitable[str | None]] | None = None      # проверка копии настроек обычным путём: наличие файла ещё не значит, что из него можно восстановиться
        self.clean_grace_s = CLEAN_GRACE_S
        self.wait_s = WAIT_S

    # ------------------------------------------------------------------------------------------ действия администратора
    async def create(self, db: AsyncSession, source_id: str, target_id: str, groups: list[str] | None, *, actor: str) -> StorageMigration:
        """Создаёт задание и запускает оценку. Копирование начнётся только после start()."""
        if str(source_id) == str(target_id):
            raise MigrationError("Исходное и целевое хранилище совпадают.")
        try:
            source, target = await self._files.row(db, source_id), await self._files.row(db, target_id)
        except SettingsError as exc:
            raise MigrationError(str(exc)) from None
        chosen = list(GROUPS) if not groups else [g for g in GROUPS if g in groups]
        if not chosen or (groups and set(groups) - set(GROUPS)):
            raise MigrationError("Выберите, какие данные переносить.")
        busy = (await db.execute(select(StorageMigration.id).where(StorageMigration.state.in_(BUSY)).limit(1))).first()
        if busy:
            raise MigrationError("Уже есть незавершённый перенос: дождитесь его окончания или отмените.")
        if (await db.execute(select(StorageTransfer.id).where(StorageTransfer.state.in_(("queued", "running"))).limit(1))).first():
            raise MigrationError("Идёт перенос записей с локального диска: дождитесь его окончания.")
        job = StorageMigration(source_id=source.id, target_id=target.id, source_name=source.name, target_name=target.name, mode="selected" if groups else "full",
                               groups=chosen, state="scanning", events=[], created_by=actor[:200])
        db.add(job)
        await db.flush()
        self._note(job, f"Перенос создан: {source.name} → {target.name}")
        if self._audit is not None:
            await self._audit(db, actor=actor, action="storage.migration.create", target_id=str(job.id), details={"source": source.name, "target": target.name, "groups": chosen})
        await db.commit()
        self._wake.set()
        return job

    async def start(self, db: AsyncSession, job_id: uuid.UUID, actor: str) -> StorageMigration:
        job = await self._job(db, job_id)
        if job.state != "ready":
            raise MigrationError("Начать можно только после завершения оценки.")
        free = (job.estimate or {}).get("target_free")
        if free is not None and free < job.bytes_total + RESERVE:
            raise MigrationError("На целевом хранилище не хватает места для этих данных.")
        job.state, job.copy_started_at = "copying", utcnow()
        self._note(job, f"Копирование начато ({actor})")
        await db.commit()
        self._wake.set()
        return job

    async def cancel(self, db: AsyncSession, job_id: uuid.UUID, actor: str) -> StorageMigration:
        job = await self._job(db, job_id)
        if job.state not in ("scanning", "ready", "copying", "attention", "verifying", "waiting"):
            raise MigrationError("На этом этапе отменить перенос нельзя.")
        if job.state in ("ready", "attention") or (job.state == "waiting" and job.resume_state != "cleaning"):
            job.state, job.finished_at = "cancelled", utcnow()
        job.cancel_requested = True
        self._note(job, f"Отмена ({actor}). Исходные данные не изменялись.")
        await db.commit()
        self._wake.set()
        return job

    async def retry(self, db: AsyncSession, job_id: uuid.UUID, *, overwrite: bool, actor: str) -> StorageMigration:
        """Повторить файлы с ошибками. overwrite — заменить на цели файлы, отличающиеся от источника."""
        job = await self._job(db, job_id)
        if job.state != "attention":
            raise MigrationError("Повторять нечего: задание не остановлено из-за ошибок.")
        await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.migration_id == job.id, StorageMigrationItem.state == "failed")
                         .values(state="pending", attempts=0, error=None, overwrite=overwrite))
        job.state, job.failed, job.error = "copying", 0, None
        self._note(job, f"Повтор файлов с ошибками ({actor})" + (", с заменой" if overwrite else ""))
        await db.commit()
        self._wake.set()
        return job

    async def clean(self, db: AsyncSession, job_id: uuid.UUID, confirm: str, actor: str) -> StorageMigration:
        """Очистка источника: только после переключения и только по явному подтверждению именем хранилища."""
        job = await self._job(db, job_id)
        if job.state != "switched":
            raise MigrationError("Очистка доступна только после переноса и переключения.")
        if confirm.strip() != job.source_name:
            raise MigrationError("Для подтверждения введите название исходного хранилища.")
        if (utcnow() - job.switched_at).total_seconds() < self.clean_grace_s:
            raise MigrationError("Подождите несколько минут после переключения: уже идущие воспроизведения ещё читают файлы с исходного хранилища.")
        job.state = "cleaning"
        self._note(job, f"Очистка исходного хранилища подтверждена ({actor})")
        if self._audit is not None:
            await self._audit(db, actor=actor, action="storage.migration.clean", target_id=str(job.id), details={"source": job.source_name})
        await db.commit()
        self._wake.set()
        return job

    async def _job(self, db: AsyncSession, job_id: uuid.UUID) -> StorageMigration:
        job = await db.get(StorageMigration, job_id)
        if job is None:
            raise MigrationError("Задание не найдено.")
        return job

    # ------------------------------------------------------------------------------------------ для интерфейса
    @staticmethod
    def view(job: StorageMigration) -> dict:
        elapsed = (utcnow() - job.copy_started_at).total_seconds() if job.copy_started_at and job.state in ("copying", "verifying", "waiting") else None
        return {"id": str(job.id), "state": job.state, "source": job.source_name, "target": job.target_name, "source_id": str(job.source_id), "target_id": str(job.target_id),
                "mode": job.mode, "groups": job.groups, "total": job.total, "done": job.done, "failed": job.failed, "bytes_total": job.bytes_total, "bytes_done": job.bytes_done,
                "percent": round(job.bytes_done * 100 / job.bytes_total, 1) if job.bytes_total else (100.0 if job.done >= job.total and job.total else 0.0),
                "speed_bps": int(job.bytes_done / elapsed) if elapsed and elapsed > 1 else None, "estimate": job.estimate, "report": job.report, "error": job.error,
                "switched_at": job.switched_at.isoformat() if job.switched_at else None, "created_at": job.created_at.isoformat(), "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "created_by": job.created_by, "resume_state": job.resume_state}

    async def problems(self, db: AsyncSession, job_id: uuid.UUID, limit: int = 100) -> list[dict]:
        rows = (await db.execute(select(StorageMigrationItem).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.state.in_(("failed", "kept", "gone")))
                                 .order_by(StorageMigrationItem.id).limit(limit))).scalars().all()
        return [{"path": i.path, "state": i.state, "error": i.error} for i in rows]

    def _note(self, job: StorageMigration, text: str, level: str = "info") -> None:
        job.events = [*(job.events or [])[-(MAX_EVENTS - 1):], {"t": utcnow().isoformat(), "level": level, "text": text[:400]}]
        log.info("Перенос данных: %s", text[:200], extra={"migration": str(job.id)})
        if self.journal is not None:
            self.journal.emit("storage", "storage_migration", level="warn" if level == "warn" else "error" if level == "error" else "info", message=text[:300], data={"migration": str(job.id)})

    # ------------------------------------------------------------------------------------------ фоновый цикл
    async def run(self) -> None:
        """Ведущий экземпляр. Задание одно: этапы выполняются по очереди, между ними состояние сохранено в базе."""
        while True:
            try:
                worked = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Сбой задания переноса")
                worked = False
            if not worked:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), self._idle)
                except asyncio.TimeoutError:
                    pass

    async def step(self) -> bool:
        """Один шаг активного задания. True — есть ещё работа."""
        async with self._sm() as db:
            job = (await db.execute(select(StorageMigration).where(StorageMigration.state.in_(ACTIVE)).order_by(StorageMigration.created_at).limit(1))).scalars().first()
            if job is None:
                return False
            job_id, state = job.id, job.state
        handler = {"scanning": self._scan, "copying": self._copy, "verifying": self._verify, "switching": self._switch, "cleaning": self._clean, "waiting": self._wait}[state]
        try:
            await handler(job_id)
        except Unavailable as exc:
            await self._set(job_id, state="waiting", resume_state=state, error=str(exc)[:490], note=f"Хранилище недоступно, задание продолжится само: {exc}", level="warn")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка этапа переноса", extra={"migration": str(job_id), "stage": state})
            await self._set(job_id, state="failed", error=f"{type(exc).__name__}: {exc}"[:490], finished=True, note=f"Перенос остановлен: {exc}", level="error")
        return True

    async def _set(self, job_id: uuid.UUID, *, note: str | None = None, level: str = "info", finished: bool = False, **fields) -> None:
        async with self._sm() as db:
            job = await db.get(StorageMigration, job_id)
            for k, v in fields.items():
                setattr(job, k, v)
            if finished:
                job.finished_at = utcnow()
            if note:
                self._note(job, note, level)
            await db.commit()

    async def _backends(self, job_id: uuid.UUID) -> tuple[StorageMigration, object, object]:
        async with self._sm() as db:
            job = await self._job(db, job_id)
            try:
                src = await asyncio.to_thread(self._files.build, await self._files.row(db, job.source_id))
                dst = await asyncio.to_thread(self._files.build, await self._files.row(db, job.target_id))
            except (SettingsError, StorageError) as exc:
                raise Unavailable(str(exc)) from None
        return job, PrefixedStorage(src, ""), PrefixedStorage(dst, "")

    async def _probe(self, src, dst) -> None:
        for name, st in (("исходное", src), ("целевое", dst)):
            try:
                await asyncio.to_thread(st.probe)
            except StorageError as exc:
                raise Unavailable(f"{name} хранилище: {exc}") from None

    # ---------------------------------------------------------------------------- оценка
    async def _walk(self, src, groups: list[str]):
        """Файлы источника порциями: обход сетевого ресурса с миллионом файлов не держит цикл событий и не копит всё в памяти."""
        it = iter(src.walk(""))
        while True:
            batch = await asyncio.to_thread(lambda: list(itertools.islice(it, 500)))
            if not batch:
                return
            yield [(p, s, m) for p, s, m in batch if scanned(p) and GROUP_OF[category_of(p)] in groups]

    async def _scan(self, job_id: uuid.UUID) -> None:
        job, src, dst = await self._backends(job_id)
        await self._probe(src, dst)
        groups = list(job.groups)
        by: dict[str, dict] = {}
        total, size = 0, 0
        async with self._sm() as db:                  # оценка могла прерваться перезапуском: начинаем заново, файлы ещё не копировались
            await db.execute(delete(StorageMigrationItem).where(StorageMigrationItem.migration_id == job_id))
            await db.commit()
        try:
            async for batch in self._walk(src, groups):
                async with self._sm() as db:
                    await db.execute(update(StorageMigration).where(StorageMigration.id == job_id).values(total=total + len(batch)))
                    db.add_all(StorageMigrationItem(migration_id=job_id, path=p, category=category_of(p), size=s, mtime=m) for p, s, m in batch)
                    await db.commit()
                    if (await db.get(StorageMigration, job_id)).cancel_requested:
                        return await self._cancelled(job_id)
                for p, s, _ in batch:
                    c = by.setdefault(category_of(p), {"files": 0, "bytes": 0})
                    c["files"] += 1
                    c["bytes"] += s
                    total += 1
                    size += s
        except StorageError as exc:
            raise Unavailable(str(exc)) from None
        try:
            _all, free = await asyncio.to_thread(dst.volume)
        except StorageError:
            free = None
        speed = await asyncio.to_thread(speed_sample, dst)
        est = {"categories": [{"key": k, "title": CATEGORIES[k], **v} for k, v in sorted(by.items(), key=lambda kv: -kv[1]["bytes"])], "target_free": free, "speed_bps": int(speed) if speed else None,
               "seconds": int(size / speed) + total // 20 if speed else None, "not_in_profiles": "Сегменты идущего эфира и записи, ещё не выгруженные с диска сервера, в хранилищах не лежат и здесь не переносятся. Корзины и снимки сервера хранения не переносятся."}
        await self._set(job_id, state="ready", total=total, bytes_total=size, estimate=est, note=f"Оценка готова: {total} файлов, {size} байт")

    async def _cancelled(self, job_id: uuid.UUID) -> None:
        await self._set(job_id, state="cancelled", finished=True, note="Перенос отменён. Файлы на исходном хранилище не тронуты; уже скопированные остались на целевом и будут учтены при повторном запуске.")

    # ---------------------------------------------------------------------------- копирование
    async def _stop_watcher(self, job_id: uuid.UUID, stop: Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(1.0)
            async with self._sm() as db:
                if (await db.execute(select(StorageMigration.cancel_requested).where(StorageMigration.id == job_id))).scalar_one_or_none():
                    stop.set()

    async def _copy(self, job_id: uuid.UUID) -> None:
        job, src, dst = await self._backends(job_id)
        await self._probe(src, dst)
        stop = Event()
        watcher = asyncio.create_task(self._stop_watcher(job_id, stop))
        try:
            while True:
                async with self._sm() as db:
                    items = (await db.execute(select(StorageMigrationItem).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.state == "pending")
                                              .order_by(StorageMigrationItem.id).limit(100))).scalars().all()
                    await db.commit()
                if not items:
                    break
                for it in items:
                    if stop.is_set():
                        return await self._cancelled(job_id)
                    await self._copy_item(job_id, it, src, dst, stop)
        except Stopped:
            return await self._cancelled(job_id)
        finally:
            stop.set()
            watcher.cancel()
        async with self._sm() as db:
            failed = (await db.execute(select(func.count()).select_from(StorageMigrationItem).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.state == "failed"))).scalar_one()
        if failed:
            await self._set(job_id, state="attention", failed=failed, note=f"Копирование закончилось, файлов с ошибками: {failed}. Источник цел: повторите или отмените.", level="warn")
        else:
            await self._set(job_id, state="verifying", note="Копирование закончено, идёт сверка")

    async def _copy_item(self, job_id: uuid.UUID, it: StorageMigrationItem, src, dst, stop: Event) -> None:
        state, sha, error, size = "done", None, None, it.size
        try:
            sha, size = await asyncio.to_thread(copy_file, src, dst, it.path, it.mtime, overwrite=it.overwrite, stop=stop)
        except StorageNotFound:
            state, error = "gone", "Файл исчез с исходного хранилища (удалён по сроку хранения или вручную)"
        except Conflict as exc:
            state, error = "failed", str(exc)
        except (StorageError, OSError) as exc:
            await self._probe(src, dst)                      # недоступное хранилище: файл не виноват, попытка не считается
            try:
                _t, free = await asyncio.to_thread(dst.volume)
            except StorageError:
                free = None
            if free is not None and free < it.size + (64 << 20):
                raise Unavailable("на целевом хранилище закончилось место") from None
            attempts = it.attempts + 1
            state, error = ("failed", str(exc)[:480]) if attempts >= RETRIES else ("pending", str(exc)[:480])
            async with self._sm() as db:
                await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.id == it.id).values(state=state, attempts=attempts, error=error))
                await db.commit()
            return
        async with self._sm() as db:
            await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.id == it.id).values(state=state, sha256=sha, size=size, error=error, overwrite=False))
            await db.execute(update(StorageMigration).where(StorageMigration.id == job_id).values(done=StorageMigration.done + 1, bytes_done=StorageMigration.bytes_done + (size if state == "done" else it.size)))
            await db.commit()

    # ---------------------------------------------------------------------------- сверка
    async def _delta(self, job_id: uuid.UUID, src, groups: list[str]) -> int:
        """Сравнить источник со списком: новые файлы добавить, изменившиеся скопировать заново, исчезнувшие отметить. Ответ — сколько файлов нужно докопировать."""
        seen: set[str] = set()
        queued = 0
        try:
            async for batch in self._walk(src, groups):
                async with self._sm() as db:
                    known = {i.path: i for i in (await db.execute(select(StorageMigrationItem).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.path.in_([p for p, _, _ in batch])))).scalars()}
                    for p, s, m in batch:
                        seen.add(p)
                        it = known.get(p)
                        if it is None:
                            db.add(StorageMigrationItem(migration_id=job_id, path=p, category=category_of(p), size=s, mtime=m))
                            await db.execute(update(StorageMigration).where(StorageMigration.id == job_id).values(total=StorageMigration.total + 1, bytes_total=StorageMigration.bytes_total + s))
                            queued += 1
                        elif it.state in ("done", "gone") and (it.size != s or abs(it.mtime - m) > 2):
                            await db.execute(update(StorageMigration).where(StorageMigration.id == job_id).values(bytes_total=StorageMigration.bytes_total + s - it.size, done=StorageMigration.done - 1, bytes_done=StorageMigration.bytes_done - it.size))
                            it.state, it.size, it.mtime, it.overwrite, it.error = "pending", s, m, True, None
                            queued += 1
                    await db.commit()
        except StorageError as exc:
            raise Unavailable(str(exc)) from None
        async with self._sm() as db:
            rows = (await db.execute(select(StorageMigrationItem.id, StorageMigrationItem.path).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.state == "done"))).all()
            vanished = [i for i, p in rows if p not in seen]
            for chunk in (vanished[n:n + 500] for n in range(0, len(vanished), 500)):
                await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.id.in_(chunk)).values(state="gone", error="Файл удалён с исходного хранилища после копирования"))
            await db.commit()
        return queued

    async def _verify(self, job_id: uuid.UUID) -> None:
        job, src, dst = await self._backends(job_id)
        await self._probe(src, dst)
        if job.cancel_requested:
            return await self._cancelled(job_id)
        passes = int((job.estimate or {}).get("passes", 0))
        queued = await self._delta(job_id, src, list(job.groups))
        if queued and passes < DELTA_PASSES:
            await self._set(job_id, state="copying", estimate={**(job.estimate or {}), "passes": passes + 1}, note=f"Пока шло копирование, изменилось файлов: {queued}. Докопирую.")
            return
        # целевое хранилище: каждый скопированный файл на месте и нужного размера
        have: dict[str, int] = {}
        try:
            it = iter(dst.walk(""))
            while True:
                batch = await asyncio.to_thread(lambda: list(itertools.islice(it, 2000)))
                if not batch:
                    break
                have.update((p, s) for p, s, _ in batch)
        except StorageError as exc:
            raise Unavailable(str(exc)) from None
        bad: list[str] = []
        count, size = 0, 0
        async with self._sm() as db:
            res = await db.stream(select(StorageMigrationItem.path, StorageMigrationItem.size).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.state == "done"))
            async for p, s in res:
                count += 1
                size += s
                if have.get(p) != s:
                    bad.append(p)
            problems = await self._db_check(db, job, have)
        if bad:
            await self._set(job_id, state="attention", failed=len(bad), error=f"На целевом хранилище не сходится файлов: {len(bad)}. Пример: {bad[0]}"[:490], note=f"Сверка не пройдена: {len(bad)} файлов", level="error")
            async with self._sm() as db:
                for chunk in (bad[n:n + 500] for n in range(0, len(bad), 500)):
                    await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.path.in_(chunk)).values(state="failed", error="На целевом хранилище файла нет или размер другой", overwrite=True))
                await db.commit()
            return
        await self._set(job_id, state="switching", estimate={**(job.estimate or {}), "verified": {"files": count, "bytes": size}, "db_missing": problems[:200], "db_missing_total": len(problems)},
                        note=f"Сверка пройдена: {count} файлов, {size} байт" + (f". Записей базы без файла ни на одном хранилище: {len(problems)}" if problems else ""), level="warn" if problems else "info")

    async def _db_check(self, db: AsyncSession, job: StorageMigration, have: dict[str, int]) -> list[str]:
        """Соответствие записям базы: файл, на который ссылается запись и который должен лежать в этих хранилищах, есть на цели. Отсутствующий и в источнике — отчёт, а не препятствие:
        его нет и сейчас, перенос ничего не ухудшает."""
        missing: list[str] = []
        groups = set(job.groups)
        for prefix, kind in ((AUDIO, "audio"), (VIDEO, "video")):
            if kind not in groups:
                continue
            owned = [Recording.export_profile_id == job.source_id]
            if await self._files.current_profile(db, kind) == job.source_id:
                owned.append(Recording.export_profile_id.is_(None))             # записи до 0.70: профиль не запомнен, файл лежит на выбранном для них хранилище
            q = select(Recording.path).where(Recording.export_status == "exported", Recording.file_state == "ok", or_(*owned), self._area(kind))
            for p in (await db.execute(q)).scalars():
                if f"{prefix}/{p}" not in have:
                    missing.append(f"{prefix}/{p}")
        if "chat_files" in groups:
            for model in (ChatAttachment, ChatFile):
                for key in (await db.execute(select(model.storage_key).where(model.profile_id == job.source_id, model.file_state == "ok"))).scalars():
                    if f"{CHAT}/files/{key}" not in have:
                        missing.append(f"{CHAT}/files/{key}")
        return missing

    # ---------------------------------------------------------------------------- переключение
    GROUP_SETTINGS = {"audio": "audio_storage", "video": "video", "documents": "storage", "chat_files": "chat_files", "journal": "journal", "backups": "auto_backup"}

    async def _switch(self, job_id: uuid.UUID) -> None:
        job, src, dst = await self._backends(job_id)
        await self._probe(src, dst)
        source, target = str(job.source_id), str(job.target_id)
        before: dict[str, dict] = {}
        loc_src, loc_dst = await asyncio.to_thread(src.location), await asyncio.to_thread(dst.location)
        async with self._sm() as db:
            try:
                for g in job.groups:
                    setting = self.GROUP_SETTINGS.get(g)
                    if setting is None:
                        continue
                    cfg = await self._svc.get(db, setting)
                    if setting == "video" and getattr(cfg, "storage", "") == "audio":
                        a = await self._svc.get(db, "audio_storage")
                        if a.profile_id == source and "audio" not in job.groups:      # видео уходит отдельно от аудио: для него своё хранилище
                            before[setting] = {"storage": "audio", "profile_id": cfg.profile_id}
                            await self._svc.update(db, setting, {"storage": "profile", "profile_id": target}, actor="перенос данных")
                        continue
                    if getattr(cfg, "profile_id", "") == source:
                        before[setting] = {"profile_id": source}
                        await self._svc.update(db, setting, {"profile_id": target}, actor="перенос данных")
                await db.execute(update(Recording).where(Recording.export_profile_id == job.source_id, self._rec_filter(job)).values(export_profile_id=job.target_id))
                if "chat_files" in job.groups:
                    for model in (ChatAttachment, ChatFile):
                        await db.execute(update(model).where(model.profile_id == job.source_id).values(profile_id=job.target_id))
                await self._relocate(db, job, loc_src, loc_dst)
                job = await self._job(db, job_id)
                job.switched, job.switched_at = {"settings": before, "from": loc_src, "to": loc_dst}, utcnow()
                self._note(job, "Функции переведены на новое хранилище, проверяю работу")
                await db.commit()
            except Exception:
                await db.rollback()
                await self._switch_back(job_id, before, loc_src, loc_dst)
                raise
        problem = await self._acceptance(job_id)
        if problem:
            await self._switch_back(job_id, before, loc_src, loc_dst)
            await self._set(job_id, state="attention", error=problem[:490], note=f"Проверка на новом хранилище не прошла, система возвращена на исходное: {problem}", level="error")
            return
        await self._set(job_id, state="switched", error=None, note="Система работает на новом хранилище. Источник сохранён: очистите его, когда убедитесь, что всё в порядке.")
        if self.journal is not None:
            self.journal.emit("storage", "storage_migration_switched", message=f"Данные перенесены: {job.source_name} → {job.target_name}", data={"migration": str(job_id)})

    @staticmethod
    def _area(kind: str):
        """Записи области: у выгрузок первых версий область не записана, они все в Audio/."""
        return or_(Recording.export_area == "audio", Recording.export_area.is_(None)) if kind == "audio" else Recording.export_area == kind

    @classmethod
    def _rec_filter(cls, job: StorageMigration):
        return or_(*(cls._area(k) for k in ("audio", "video") if k in job.groups), Recording.id.is_(None))

    async def _relocate(self, db: AsyncSession, job: StorageMigration, old: str, new: str) -> None:
        """Адреса для показа: в записях и документах хранится полный путь, а не только относительный."""
        await db.execute(update(Recording).where(Recording.export_location.startswith(old), self._rec_filter(job)).values(export_location=func.replace(Recording.export_location, old, new)))
        if "documents" in job.groups:
            rows = (await db.execute(select(Protocol).where(Protocol.meta.is_not(None)))).scalars().all()
            for p in rows:
                loc = (p.meta or {}).get("location")
                if isinstance(loc, str) and loc.startswith(old):
                    p.meta = {**p.meta, "location": new + loc[len(old):]}

    async def _switch_back(self, job_id: uuid.UUID, before: dict[str, dict], loc_src: str, loc_dst: str) -> None:
        async with self._sm() as db:
            job = await self._job(db, job_id)
            for setting, values in before.items():
                await self._svc.update(db, setting, values, actor="перенос данных: возврат")
            await db.execute(update(Recording).where(Recording.export_profile_id == job.target_id, self._rec_filter(job)).values(export_profile_id=job.source_id))
            if "chat_files" in job.groups:
                for model in (ChatAttachment, ChatFile):
                    await db.execute(update(model).where(model.profile_id == job.target_id).values(profile_id=job.source_id))
            await self._relocate(db, job, loc_dst, loc_src)
            job.switched, job.switched_at = None, None
            await db.commit()

    async def _acceptance(self, job_id: uuid.UUID) -> str | None:
        """После переключения: система читает файлы и пишет новые уже на новом месте. Проверяются настоящие пути приложения, а не отдельный обход."""
        async with self._sm() as db:
            job = await self._job(db, job_id)
            files, groups = self._files, set(job.groups)
            try:
                for g, setting, folder in (("audio", "audio_storage", AUDIO), ("documents", "storage", PROTOCOLS), ("journal", "journal", LOGS)):
                    if g not in groups or getattr(await self._svc.get(db, setting), "profile_id", "") != str(job.target_id):
                        continue
                    st = await files.backend(db, setting, folder)
                    if st is not None:                       # функция выключена: писать ей нечего
                        await self._write_probe(st, folder, job)
                if "video" in groups and await files.current_profile(db, "video") == job.target_id:
                    await self._write_probe(await files.video_backend(db), VIDEO, job)
                if "chat_files" in groups and getattr(await self._svc.get(db, "chat_files"), "profile_id", "") == str(job.target_id):
                    st, _pid = await files.chat_backend(db)
                    await self._write_probe(st, f"{CHAT}/files", job)
                if self._ps is not None:
                    sample = (await db.execute(select(Recording).where(Recording.export_status == "exported", Recording.file_state == "ok", Recording.export_profile_id == job.target_id).limit(1))).scalars().first()
                    if sample is not None:
                        size, reader = await self._ps.media_source(db, sample)
                        await asyncio.to_thread(lambda: next(iter(reader(0, min(size, 1024) - 1)), None))
            except (StorageError, SettingsError, OSError) as exc:
                return str(exc)
            if "backups" in groups and self.verify_backup is not None:
                problem = await self.verify_backup(str(job.target_id))
                if problem:
                    return f"резервная копия на новом хранилище не прошла проверку: {problem}"
        return None

    async def _write_probe(self, st, folder: str, job: StorageMigration) -> None:
        """Файл, записанный через обычный путь функции, должен оказаться на целевом хранилище."""
        if st is None:
            raise StorageError(f"функция {folder} не видит хранилище после переключения")
        name = f".peregovorka-write-test-{uuid.uuid4().hex[:8]}"
        await asyncio.to_thread(st.write_bytes, name, b"ok")
        try:
            async with self._sm() as db:
                row = await self._files.row(db, job.target_id)
                base = await asyncio.to_thread(self._files.build, row)
            if not await asyncio.to_thread(PrefixedStorage(base, folder).exists, name):
                raise StorageError(f"новый файл функции {folder} попал не на целевое хранилище")
        finally:
            await asyncio.to_thread(st.delete, name)

    # ---------------------------------------------------------------------------- очистка
    async def _clean(self, job_id: uuid.UUID) -> None:
        job, src, dst = await self._backends(job_id)
        await self._probe(src, dst)
        await self._final_sync(job_id, src, dst, list(job.groups))
        freed, removed = 0, 0
        kept: dict[str, int] = {}
        stop = Event()
        while True:
            async with self._sm() as db:
                items = (await db.execute(select(StorageMigrationItem).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.state == "done").order_by(StorageMigrationItem.id).limit(200))).scalars().all()
                await db.commit()
            if not items:
                break
            for it in items:
                state, error = await asyncio.to_thread(self._clean_one, src, dst, it, stop)
                if state == "cleaned":
                    freed += it.size
                    removed += 1
                else:
                    kept[error] = kept.get(error, 0) + 1
                async with self._sm() as db:
                    await db.execute(update(StorageMigrationItem).where(StorageMigrationItem.id == it.id).values(state=state, error=None if state == "cleaned" else error))
                    await db.commit()
        for top in (AUDIO, VIDEO, TRANSCRIPTS, PROTOCOLS, CHAT, BOARDS, LOGS, BACKUPS):
            await asyncio.to_thread(self._prune, src, top)
        left: dict[str, int] = {}
        async with self._sm() as db:
            for state, n in (await db.execute(select(StorageMigrationItem.state, func.count()).where(StorageMigrationItem.migration_id == job_id).group_by(StorageMigrationItem.state))).all():
                left[state] = n
        report = {"moved_files": left.get("cleaned", 0) + left.get("kept", 0), "moved_bytes": (await self._sum(job_id)), "freed_bytes": freed, "removed_files": removed,
                  "kept": [{"reason": k, "files": v} for k, v in kept.items()], "gone_files": left.get("gone", 0)}
        await self._set(job_id, state="cleaned", report=report, finished=True, note=f"Очистка закончена: удалено файлов {removed}, освобождено {freed} байт. Оставлено: {sum(kept.values())}.")

    async def _sum(self, job_id: uuid.UUID) -> int:
        async with self._sm() as db:
            return int((await db.execute(select(func.coalesce(func.sum(StorageMigrationItem.size), 0)).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.state.in_(("cleaned", "kept"))))).scalar_one())

    @staticmethod
    def _prune(src, top: str) -> None:
        try:
            src.prune_dirs(top)
        except StorageError:
            pass

    @staticmethod
    def _clean_one(src, dst, it: StorageMigrationItem, stop: Event) -> tuple[str, str]:
        """Удалить файл источника, только если он тот же, что скопирован, и копия на месте. Выполняется в потоке."""
        try:
            size, mtime = src.stat_of(it.path)
        except StorageNotFound:
            return "cleaned", ""
        except StorageError as exc:
            return "kept", f"Исходное хранилище не ответило: {exc}"[:200]
        if size != it.size or abs(mtime - it.mtime) > 2:
            return "kept", "Файл изменился после копирования"
        try:
            if dst.stat_of(it.path)[0] != size or (it.sha256 and sha256_of(dst, it.path, size, stop) != it.sha256):
                return "kept", "Копия на целевом хранилище не совпадает"
            src.delete(it.path)
        except StorageNotFound:
            return "kept", "Копии нет на целевом хранилище"
        except StorageError as exc:
            return "kept", f"Не удалось удалить файл: {exc}"[:200]
        return "cleaned", ""

    async def _final_sync(self, job_id: uuid.UUID, src, dst, groups: list[str]) -> None:
        """Файлы, успевшие попасть в источник до переключения, не должны исчезнуть вместе с ним."""
        await self._delta(job_id, src, groups)
        stop = Event()
        async with self._sm() as db:
            items = (await db.execute(select(StorageMigrationItem).where(StorageMigrationItem.migration_id == job_id, StorageMigrationItem.state == "pending"))).scalars().all()
            await db.commit()
        for it in items:
            await self._copy_item(job_id, it, src, dst, stop)

    # ---------------------------------------------------------------------------- ожидание
    async def _wait(self, job_id: uuid.UUID) -> None:
        job, src, dst = await self._backends(job_id)
        if job.cancel_requested:
            return await self._cancelled(job_id)
        await asyncio.sleep(self.wait_s)
        await self._probe(src, dst)
        await self._set(job_id, state=job.resume_state or "copying", resume_state=None, error=None, note="Хранилище снова доступно, продолжаю")
