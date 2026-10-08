"""Сверка метаданных базы с реальным содержимым хранилищ (Local/SMB).

Принцип: база не считает файл доступным только потому, что он когда-то был создан. Сверка фоновая и пакетная — НЕ `exists()` на каждый объект и не при
открытии страниц: объекты берутся пачками по встречам, а наличие файлов проверяется ОДНИМ листингом каталога на группу (на SMB это один запрос вместо сотен).
Точечная проверка конкретного файла происходит только при обращении к нему (скачивание), см. `mark_missing`.

Что сверяется: записи аудио (локальная копия и выгруженный файл), выгруженные стенограммы/протоколы/резюме (их текст остаётся в базе), чат и схема доски
(копии рядом со стенограммой), вложения чата. Файл исчез → состояние `missing`, ссылка на скачивание перестаёт отдаваться; вернулся → снова `ok`.

Чего НЕ делает: не импортирует неизвестные файлы (SMB — не источник истины); они только считаются «неизвестными» и показываются администратору.
Безопасность: при недоступном хранилище (нет связи/прав) объекты НЕ объявляются удалёнными — они учитываются как «не удалось проверить». Если почти всё
пропало разом (≥ порога, по умолчанию 95 %) — это похоже на сбой монтирования, а не на ручное удаление: изменения не применяются без явного подтверждения.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import ChatAttachment, Meeting, MeetingChatMessage, MeetingWhiteboard, Protocol, Recording, StorageSyncRun, utcnow
from .filestore import AUDIO, BOARDS, CHAT, PROTOCOLS, TRANSCRIPTS, FileStore
from .storage import StorageBackend, StorageError, StorageNotFound

log = logging.getLogger("app.reconcile")

_SERVICE_NAME = re.compile(r"^\.peregovorka-write-test-|\.tmp$|^\.")
MAX_SAMPLE = 100
ORPHAN_SCAN_LIMIT = 100_000


def mark_missing_sync(obj) -> None:
    obj.file_state = "missing"
    obj.file_checked_at = utcnow()


async def mark_missing(db: AsyncSession, obj, journal=None, what: str = "файл") -> None:
    """Точечно: при обращении к файлу выяснилось, что его нет — состояние обновляется сразу (без ожидания плановой сверки)."""
    if getattr(obj, "file_state", "ok") != "missing":
        mark_missing_sync(obj)
        await db.commit()
        if journal is not None:
            journal.emit("storage", "file_missing", level="warn", message=f"{what}: файл не найден в хранилище (удалён вне приложения?)", data={"id": str(obj.id)})


@dataclass
class Obj:
    model: str                 # recording | protocol | attachment | protocol_extra
    row: object
    backend_key: str           # ключ хранилища: "audio" | "docs:<папка>" | "chat:<profile>" | "local"
    dir: str
    name: str
    # для recording с локальной копией
    local_ok: bool | None = None
    label: str = ""


@dataclass
class Report:
    checked: int = 0
    missing: int = 0
    restored: int = 0
    orphans: int = 0
    unavailable: int = 0
    missing_sample: list[dict] = field(default_factory=list)
    orphan_sample: list[dict] = field(default_factory=list)
    unavailable_backends: dict[str, str] = field(default_factory=dict)
    suspicious: dict[str, str] = field(default_factory=dict)
    per_kind: dict[str, dict[str, int]] = field(default_factory=dict)
    orphan_scan_skipped: bool = False

    def kind(self, name: str) -> dict[str, int]:
        return self.per_kind.setdefault(name, {"checked": 0, "missing": 0, "restored": 0, "unavailable": 0})


def split_key(key: str) -> tuple[str, str]:
    p = PurePosixPath(key)
    return (str(p.parent) if str(p.parent) != "." else ""), p.name


class Reconciler:
    def __init__(self, sm: async_sessionmaker[AsyncSession], files: FileStore, recordings_path: str, journal=None, audit=None):
        self._sm = sm
        self._files = files
        self._rec_path = recordings_path
        self.journal = journal
        self._audit = audit       # async (db, *, actor, action, target_id, details) -> None

    # ------------------------------------------------------------------------------- хранилища
    async def _backend(self, db: AsyncSession, key: str, cache: dict[str, StorageBackend | Exception | None]) -> StorageBackend | None:
        """Хранилище по ключу; None — выгрузка выключена (проверять нечего); Exception в кэше — недоступно."""
        if key in cache:
            v = cache[key]
            if isinstance(v, Exception):
                raise v
            return v
        try:
            if key == "audio":
                b = await self._files.backend(db, "audio_storage", AUDIO, legacy_prefix="audio")
            elif key.startswith("docs:"):
                b = await self._files.backend(db, "storage", key[5:])
            elif key.startswith("chat:"):
                pid = key[5:]
                b = await self._files.chat_backend_for(db, uuid.UUID(pid) if pid else None)
            else:
                b = None
            if b is not None:
                await asyncio.to_thread(b.probe)
            cache[key] = b
            return b
        except StorageError as exc:
            cache[key] = exc
            raise

    # --------------------------------------------------------------------------------- сбор
    async def _collect(self, db: AsyncSession, meeting_ids: list[uuid.UUID]) -> list[Obj]:
        out: list[Obj] = []
        recs = (await db.execute(select(Recording).where(Recording.meeting_id.in_(meeting_ids)))).scalars().all()
        for r in recs:
            d, n = split_key(r.path)
            local = os.path.isfile(os.path.join(self._rec_path, r.path))
            out.append(Obj("recording", r, "audio" if r.export_status == "exported" else "local", d, n, local_ok=local, label=r.path.rsplit("/", 1)[-1]))
        prots = (await db.execute(select(Protocol).where(Protocol.meeting_id.in_(meeting_ids), Protocol.status == "ready"))).scalars().all()
        for p in prots:
            meta = p.meta or {}
            d = meta.get("dir")
            if not d:
                continue
            if p.kind == "transcript":
                out.append(Obj("protocol", p, f"docs:{TRANSCRIPTS}", d, "protocol.txt", label="стенограмма"))
            elif p.kind in ("protocol", "summary") and meta.get("location"):
                out.append(Obj("protocol", p, f"docs:{PROTOCOLS}", d, "official-protocol.md" if p.kind == "protocol" else "summary.md",
                               label="протокол" if p.kind == "protocol" else "резюме"))
        for p in prots:
            if p.kind == "transcript" and (p.meta or {}).get("dir"):
                d = p.meta["dir"]
                if (await db.execute(select(MeetingChatMessage.id).where(MeetingChatMessage.meeting_id == p.meeting_id).limit(1))).first():
                    out.append(Obj("protocol_extra", p, f"docs:{CHAT}", d, "chat.txt", label="переписка"))
                wb = await db.get(MeetingWhiteboard, p.meeting_id)
                if wb is not None and wb.shapes > 0:
                    out.append(Obj("protocol_extra", p, f"docs:{BOARDS}", d, "whiteboard.drawio", label="схема доски"))
        atts = (await db.execute(select(ChatAttachment).where(ChatAttachment.meeting_id.in_(meeting_ids), ChatAttachment.message_id.is_not(None)))).scalars().all()
        for a in atts:
            d, n = split_key(a.storage_key)
            out.append(Obj("attachment", a, f"chat:{a.profile_id or ''}", d, n, label=a.name))
        return out

    # ------------------------------------------------------------------------------- проверка
    async def run(self, trigger: str, actor: str | None = None, *, force: bool = False, batch_meetings: int = 200, guard_percent: int = 95,
                  run_id: uuid.UUID | None = None) -> uuid.UUID:
        async with self._sm() as db:
            run = StorageSyncRun(id=run_id or uuid.uuid4(), trigger=trigger, actor=actor, status="running")
            if run_id is not None and (existing := await db.get(StorageSyncRun, run_id)) is not None:
                run = existing
            else:
                db.add(run)
            await db.commit()
            rid = run.id
        rep = Report()
        status = "ok"
        try:
            await self._run(rep, force=force, batch=batch_meetings, guard=guard_percent)
            if rep.suspicious:
                status = "partial"
            elif rep.unavailable_backends and rep.checked == 0 and rep.unavailable:
                status = "unavailable"
            elif rep.unavailable_backends:
                status = "partial"
        except Exception:  # noqa: BLE001
            log.exception("Сверка хранилища прервана")
            status = "failed"
        async with self._sm() as db:
            run = await db.get(StorageSyncRun, rid)
            run.status, run.finished_at = status, utcnow()
            run.checked, run.missing, run.restored, run.orphans, run.unavailable = rep.checked, rep.missing, rep.restored, rep.orphans, rep.unavailable
            run.details = {"per_kind": rep.per_kind, "missing": rep.missing_sample, "orphans": rep.orphan_sample, "unavailable_backends": rep.unavailable_backends,
                           "suspicious": rep.suspicious, "orphan_scan_skipped": rep.orphan_scan_skipped, "force": force}
            if self._audit is not None:
                await self._audit(db, actor=actor or "система (по расписанию)", action="storage.sync", target_id=str(rid),
                                  details={"trigger": trigger, "status": status, "checked": rep.checked, "missing": rep.missing, "restored": rep.restored,
                                           "orphans": rep.orphans, "unavailable": rep.unavailable, "suspicious": rep.suspicious,
                                           "missing_sample": rep.missing_sample[:20]})
            await db.commit()
        if self.journal is not None:
            self.journal.emit("storage", "sync_done", level="warn" if status in ("partial", "unavailable", "failed") or rep.missing else "info", user=actor,
                              message=f"Сверка хранилища: проверено {rep.checked}, отсутствует {rep.missing}, восстановлено {rep.restored}, неизвестных {rep.orphans}, не проверено {rep.unavailable}",
                              data={"status": status, "run": str(rid)})
        return rid

    async def _run(self, rep: Report, *, force: bool, batch: int, guard: int) -> None:
        cache: dict[str, StorageBackend | Exception | None] = {}
        dir_cache: dict[tuple[str, str], list[str] | None] = {}
        orphan_scan = True
        async with self._sm() as db:
            total = sum([(await db.execute(select(func.count()).select_from(m))).scalar_one() for m in (Recording, ChatAttachment, Protocol)])
        if total > ORPHAN_SCAN_LIMIT:
            orphan_scan, rep.orphan_scan_skipped = False, True
        stats: dict[str, dict[str, int]] = {}      # по хранилищу: checked/missing — для защиты от «пропало всё»
        buffers: dict[str, list[tuple[Obj, bool]]] = {}
        halted: set[str] = set()
        after: uuid.UUID | None = None
        while True:
            async with self._sm() as db:
                stmt = select(Meeting.id).order_by(Meeting.id).limit(batch)
                if after is not None:
                    stmt = stmt.where(Meeting.id > after)
                ids = list((await db.execute(stmt)).scalars().all())
                if not ids:
                    break
                after = ids[-1]
                objs = await self._collect(db, ids)
                expected: dict[tuple[str, str], set[str]] = {}
                for o in objs:
                    expected.setdefault((o.backend_key, o.dir), set()).add(o.name)
                decisions: list[tuple[Obj, bool | None]] = []
                for o in objs:
                    kind = {"recording": "записи", "protocol": "протоколы и стенограммы", "protocol_extra": "переписка и доски", "attachment": "вложения чата"}[o.model]
                    k = rep.kind(kind)
                    if o.backend_key in halted:
                        continue
                    present = await self._present(db, o, cache, dir_cache, rep, expected if orphan_scan else None)
                    if present == "skip":                     # выгрузка в это хранилище сейчас выключена — проверять нечего
                        continue
                    if present is None:                       # хранилище недоступно: не знаем
                        rep.unavailable += 1
                        k["unavailable"] += 1
                        continue
                    rep.checked += 1
                    k["checked"] += 1
                    decisions.append((o, present))
                for o, present in decisions:
                    bk = o.backend_key
                    st = stats.setdefault(bk, {"checked": 0, "missing": 0})
                    st["checked"] += 1
                    st["missing"] += 0 if present else 1
                    buffers.setdefault(bk, []).append((o, bool(present)))
                    if len(buffers[bk]) >= 100:
                        await self._flush(db, bk, buffers.pop(bk), stats[bk], rep, force, guard, halted)
                await db.commit()
            if len(dir_cache) > 400:
                dir_cache.clear()
        async with self._sm() as db:
            for bk, buf in list(buffers.items()):
                await self._flush(db, bk, buf, stats.get(bk, {"checked": 0, "missing": 0}), rep, force, guard, halted)
            await db.commit()

    async def _present(self, db: AsyncSession, o: Obj, cache, dir_cache, rep: Report, expected):
        """True — файл есть, False — нет (хранилище отвечает), None — проверить не удалось, "skip" — выгрузка выключена."""
        if o.model == "recording":
            if o.backend_key != "audio":                       # единственная копия — на локальном диске приложения
                return bool(o.local_ok)
            if o.local_ok:
                return True                                    # локальная копия есть — запись доступна (даже если выгрузка исчезла)
        try:
            backend = await self._backend(db, o.backend_key, cache)
        except StorageError as exc:
            rep.unavailable_backends.setdefault(o.backend_key, str(exc)[:200])
            return None
        if backend is None:
            return "skip"
        ck = (o.backend_key, o.dir)
        if ck not in dir_cache:
            try:
                dir_cache[ck] = await asyncio.to_thread(backend.list_dir, o.dir) if o.dir else []
            except StorageError as exc:
                dir_cache[ck] = None
                rep.unavailable_backends.setdefault(o.backend_key, str(exc)[:200])
            if dir_cache[ck] is not None and expected is not None:
                known = expected.get(ck, set())
                for name in dir_cache[ck] or []:
                    if name not in known and not _SERVICE_NAME.search(name):
                        rep.orphans += 1
                        if len(rep.orphan_sample) < MAX_SAMPLE:
                            rep.orphan_sample.append({"storage": o.backend_key, "path": f"{o.dir}/{name}" if o.dir else name})
        names = dir_cache[ck]
        if names is None:
            return None
        return o.name in names

    async def _flush(self, db: AsyncSession, bk: str, buf: list[tuple[Obj, bool]], st: dict[str, int], rep: Report, force: bool, guard: int, halted: set[str]) -> None:
        miss = sum(1 for _, p in buf if not p)
        if not force and len(buf) >= 10 and miss * 100 >= guard * len(buf) and bk != "local":
            # почти всё «пропало» сразу — вероятно, не смонтирован том или подменена шара: ничего не меняем, пока администратор не подтвердит
            halted.add(bk)
            rep.suspicious[bk] = f"в выборке из {len(buf)} объектов отсутствует {miss}: похоже на сбой хранилища, а не на ручное удаление"
            rep.unavailable += len(buf)
            rep.checked -= len(buf)
            for o, _p in buf:
                kind = {"recording": "записи", "protocol": "протоколы и стенограммы", "protocol_extra": "переписка и доски", "attachment": "вложения чата"}[o.model]
                k = rep.kind(kind)
                k["checked"] -= 1
                k["unavailable"] += 1
            return
        for o, present in buf:
            kind = {"recording": "записи", "protocol": "протоколы и стенограммы", "protocol_extra": "переписка и доски", "attachment": "вложения чата"}[o.model]
            k = rep.kind(kind)
            row = await db.get(type(o.row), o.row.id)    # строка могла быть прочитана в другой (уже закрытой) сессии
            if row is None:
                continue
            if o.model == "protocol_extra":
                meta = dict(row.meta or {})
                files = dict(meta.get("export_files") or {})
                new = "ok" if present else "missing"
                if files.get(o.name) != new:
                    files[o.name] = new
                    meta["export_files"] = files
                    row.meta = meta
                    if not present:
                        rep.missing += 1
                        k["missing"] += 1
                        if len(rep.missing_sample) < MAX_SAMPLE:
                            rep.missing_sample.append({"kind": o.label, "name": o.name, "dir": o.dir})
                continue
            was = getattr(row, "file_state", "ok")
            if present:
                if was == "missing":
                    rep.restored += 1
                    k["restored"] += 1
                row.file_state, row.file_checked_at = "ok", utcnow()
            else:
                if was != "missing":
                    rep.missing += 1
                    k["missing"] += 1
                    if len(rep.missing_sample) < MAX_SAMPLE:
                        rep.missing_sample.append({"kind": o.label if o.model != "attachment" else "вложение чата", "name": o.label or o.name, "dir": o.dir,
                                                   "id": str(row.id)})
                mark_missing_sync(row)

