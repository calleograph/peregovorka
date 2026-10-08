"""Журнал событий (диагностика): запись, внешнее дублирование, очистка по сроку.

Запись неблокирующая: `emit()` кладёт событие в ограниченную очередь, фоновая задача пакетами пишет в таблицу `event_log` и/или
во внешнее хранилище (каталог/SMB, файлы NDJSON по дням). Сбой журнала НИКОГДА не влияет на работу системы: ошибки только
логируются, при переполнении очереди самые старые события отбрасываются (счётчик `dropped`).

В журнал не попадают секреты (`scrub`) и содержимое разговоров; только метаданные: кто, где, когда, что произошло и почему.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..logging_setup import request_id_var, scrub
from ..models import EventLog, utcnow
from .settings import JournalSettings, SettingsError, SettingsService
from .filestore import LOGS, FileStore
from .storage import StorageError, build_storage

log = logging.getLogger("app.journal")

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
CATEGORIES = ("auth", "room", "client", "device", "network", "admin", "llm", "storage", "asr", "system")
EXT_DIR = "journal"
_MAX_MSG, _MAX_DATA = 600, 4000


def parse_client(ua: str | None) -> str | None:
    """«Chrome 130 · Windows 10/11» из User-Agent: достаточно, чтобы отличить браузер и ОС при разборе проблем."""
    if not ua:
        return None
    u = ua[:400]
    br = None
    for name, pat in (("Edge", r"Edg(?:e|A|iOS)?/(\d+)"), ("Opera", r"OPR/(\d+)"), ("Firefox", r"Firefox/(\d+)"),
                      ("Chrome", r"(?:Chrome|CriOS)/(\d+)"), ("Safari", r"Version/(\d+)[\d.]* .*Safari/")):
        m = re.search(pat, u)
        if m:
            br = f"{name} {m.group(1)}"
            break
    os_ = None
    for name, pat in (("Windows 10/11", r"Windows NT 10"), ("Windows 8", r"Windows NT 6\.[23]"), ("Windows 7", r"Windows NT 6\.1"),
                      ("Android", r"Android (\d+)"), ("iOS", r"(?:iPhone|iPad).*OS (\d+)"), ("macOS", r"Mac OS X"), ("Linux", r"Linux")):
        m = re.search(pat, u)
        if m:
            os_ = name + (f" {m.group(1)}" if name in ("Android", "iOS") else "")
            break
    out = " · ".join(x for x in (br, os_) if x)
    return out or u[:80]


class Journal:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession], svc: SettingsService, data_dir: str, *, max_queue: int = 20000):
        self._sm = session_maker
        self._svc = svc
        self._data_dir = data_dir
        self._files = FileStore(svc, data_dir)
        self._q: deque[dict] = deque(maxlen=max_queue)
        self._ext: list[dict] = []
        self._cfg: JournalSettings | None = None
        self._cfg_at = 0.0
        self._ext_flushed_at = time.monotonic()
        self._wake = asyncio.Event()
        self._flush_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.dropped = 0
        self.written = 0
        self.last_external: dict = {"ok": None, "at": None, "error": None, "files": 0}

    # ------------------------------------------------------------------------ приём
    def emit(self, category: str, event: str, *, level: str = "info", user: str | None = None, room: str | None = None,
             meeting_id: str | None = None, ip: str | None = None, client: str | None = None, message: str | None = None,
             data: dict | None = None) -> None:
        """Неблокирующая запись события. Никогда не выбрасывает исключений."""
        try:
            rid = request_id_var.get()
            raw = json.dumps(scrub(data), ensure_ascii=False, default=str) if data else ""
            payload = scrub(data) if data and len(raw) <= _MAX_DATA else ({"truncated": raw[:_MAX_DATA]} if data else None)
            if len(self._q) == self._q.maxlen:
                self.dropped += 1
            self._q.append({
                "at": utcnow(), "level": level if level in LEVELS else "info", "category": category[:24], "event": event[:64],
                "user_name": (user or None) and user[:300], "room": (room or None) and room[:200], "meeting_id": (meeting_id or None) and str(meeting_id)[:40],
                "ip": (ip or None) and ip[:64], "client": (client or None) and client[:160],
                "message": (message or None) and str(scrub(message))[:_MAX_MSG], "data": payload, "request_id": None if rid == "-" else rid})
            self._wake.set()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось поставить событие в очередь журнала")

    # ----------------------------------------------------------------------- настройки
    async def config(self, force: bool = False) -> JournalSettings:
        if force or self._cfg is None or time.monotonic() - self._cfg_at > 20:
            try:
                async with self._sm() as db:
                    self._cfg = await self._svc.get(db, "journal")  # type: ignore[assignment]
            except Exception:  # noqa: BLE001
                log.exception("Не удалось прочитать настройки журнала")
                self._cfg = self._cfg or JournalSettings()
            self._cfg_at = time.monotonic()
        return self._cfg  # type: ignore[return-value]

    # ------------------------------------------------------------------------- запись
    async def flush(self) -> int:
        """Записать накопленное (БД — сразу, внешнее хранилище — по расписанию). Вызовы выполняются по одному: ожидающий видит результат предыдущего."""
        async with self._flush_lock:
            return await self._flush_locked()

    async def _flush_locked(self) -> int:
        cfg = await self.config()
        batch: list[dict] = []
        while self._q and len(batch) < 2000:
            batch.append(self._q.popleft())
        wanted = [e for e in batch if LEVELS[e["level"]] >= LEVELS[cfg.min_level]]
        if wanted and cfg.keep_local:
            try:
                async with self._sm() as db:
                    db.add_all(EventLog(**e) for e in wanted)
                    await db.commit()
            except Exception:  # noqa: BLE001
                log.exception("Не удалось записать события журнала в базу")
        if wanted and cfg.enabled:
            self._ext.extend(wanted)
        self.written += len(wanted)
        if self._ext and (time.monotonic() - self._ext_flushed_at >= cfg.external_flush_seconds):
            await self.flush_external()
        return len(wanted)

    async def _storage(self, cfg):
        """Хранилище журнала: профиль (подпапка Logs/) или прежняя раскладка."""
        if getattr(cfg, "profile_id", ""):
            async with self._sm() as db:
                return await self._files.backend(db, "journal", LOGS)
        return build_storage(cfg, self._data_dir)  # type: ignore[arg-type]

    async def flush_external(self) -> None:
        """Пакетом выгружает накопленные события во внешнее хранилище файлом journal/ГГГГ-ММ-ДД/ЧЧММСС-xxxx.ndjson."""
        cfg = await self.config()
        self._ext_flushed_at = time.monotonic()
        if not cfg.enabled:
            self._ext.clear()
            return
        if not self._ext:
            return
        items, self._ext = self._ext, []
        try:
            storage = await self._storage(cfg)
            if storage is None:
                return
            now = datetime.now(timezone.utc)
            body = "\n".join(json.dumps({**e, "at": e["at"].isoformat()}, ensure_ascii=False, default=str) for e in items) + "\n"
            rel = f"{EXT_DIR}/{now:%Y-%m-%d}/{now:%H%M%S}-{uuid.uuid4().hex[:4]}.ndjson"
            await asyncio.to_thread(storage.write_bytes, rel, body.encode("utf-8"))
            self.last_external = {"ok": True, "at": now.isoformat(), "error": None, "files": self.last_external.get("files", 0) + 1}
        except (StorageError, OSError) as exc:
            self._ext = (items + self._ext)[-5000:]  # не теряем, попробуем в следующий раз (с ограничением памяти)
            self.last_external = {**self.last_external, "ok": False, "at": datetime.now(timezone.utc).isoformat(), "error": str(exc)[:300]}
            log.error("Выгрузка журнала во внешнее хранилище не удалась", extra={"error": str(exc)})

    async def _run(self) -> None:
        while True:
            try:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                await asyncio.sleep(0.3)  # копим мелкие события в один пакет
                while self._q:
                    await self.flush()
                cfg = await self.config()
                if self._ext and time.monotonic() - self._ext_flushed_at >= cfg.external_flush_seconds:
                    await self.flush_external()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Ошибка фоновой записи журнала")
                await asyncio.sleep(2)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="journal-writer")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        try:
            while self._q:
                await self.flush()
            await self.flush_external()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось дописать журнал при остановке")

    # ----------------------------------------------------------------------- очистка
    async def purge_expired(self) -> dict[str, int]:
        """Удаляет события старше срока хранения — из базы и из внешнего хранилища (каталоги дней)."""
        cfg = await self.config(force=True)
        cutoff = utcnow() - timedelta(days=cfg.retention_days)
        stats = {"db": 0, "external_days": 0}
        async with self._sm() as db:
            res = await db.execute(delete(EventLog).where(EventLog.at < cutoff))
            stats["db"] = res.rowcount or 0
            await db.commit()
        if cfg.enabled:
            try:
                storage = await self._storage(cfg)
                if storage is not None:
                    day_cut = cutoff.strftime("%Y-%m-%d")
                    for name in await asyncio.to_thread(storage.list_dir, EXT_DIR):
                        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and name < day_cut:
                            await asyncio.to_thread(storage.delete_dir, f"{EXT_DIR}/{name}")
                            stats["external_days"] += 1
            except (StorageError, SettingsError, OSError) as exc:
                log.error("Очистка журнала во внешнем хранилище не удалась", extra={"error": str(exc)})
        if any(stats.values()):
            log.info("Очистка журнала по сроку", extra={**stats, "retention_days": cfg.retention_days})
        return stats


async def run_journal_retention(journal: Journal, interval: float = 3600.0) -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await journal.purge_expired()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очистки журнала")
        await asyncio.sleep(interval)
