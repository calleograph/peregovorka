"""Показатели «Хранилище записей» для администратора: что и где лежит, сколько места занято и свободно.

Считается ФОНОМ (раз в `INTERVAL_S` и по кнопке «Обновить»), страница читает готовый снимок из Redis и не ждёт ни базу, ни SMB. Количество и размеры берутся из базы
(строки `recordings`, `meeting_chat_attachments`), без обхода каталогов; по файловой системе выполняется один `stat` на каждую выгруженную запись — чтобы отличить
«только во внешнем хранилище» от «и на диске тоже», и один запрос о свободном месте на том. Если внешнее хранилище недоступно, показываются прошлые данные с пометкой
«устарело» и временем последнего удачного замера.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone

from sqlalchemy import func, select

from ..models import ChatAttachment, Recording, StorageSyncRun
from .storage import StorageError

log = logging.getLogger("app.storage_stats")

KEY = "storage:stats"
LOCK = "storage:stats:lock"
INTERVAL_S = 600
VOLUME_TIMEOUT_S = 20
SCAN_LIMIT = 300_000        # предел числа файлов при фактическом подсчёте занятого места (дальше — «неполно»)
SCAN_BUDGET_S = 60
KINDS = ("mix_video", "mix_audio", "participant")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StorageStats:
    def __init__(self, session_maker, protocols, redis, interval_s: float = INTERVAL_S):
        self._sm, self._ps, self._r, self._interval = session_maker, protocols, redis, interval_s
        self._task: asyncio.Task | None = None

    # --------------------------------------------------------------------------------- чтение (мгновенно)
    async def snapshot(self) -> dict:
        raw = await self._r.get(KEY)
        if not raw:
            return {"measured_at": None, "volumes": [], "refreshing": bool(await self._r.get(LOCK)), "note": "Первый замер ещё не выполнен — нажмите «Обновить»."}
        data = json.loads(raw)
        data["refreshing"] = bool(await self._r.get(LOCK))
        return data

    def refresh_in_background(self) -> bool:
        """Запустить замер, если он ещё не идёт. Возвращает True, если запущен."""
        if self._task is not None and not self._task.done():
            return False
        self._task = asyncio.create_task(self.refresh(), name="storage-stats-refresh")
        return True

    async def run(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Не удалось измерить хранилище записей")
            await asyncio.sleep(self._interval)

    # --------------------------------------------------------------------------------- замер
    async def refresh(self) -> dict:
        if not await self._r.set(LOCK, "1", nx=True, ex=180):
            return await self.snapshot()
        try:
            prev_raw = await self._r.get(KEY)
            prev = json.loads(prev_raw) if prev_raw else {}
            data = await self._measure({v["id"]: v for v in prev.get("volumes", [])})
            await self._r.set(KEY, json.dumps(data, ensure_ascii=False))
            return data
        finally:
            await self._r.delete(LOCK)

    async def _measure(self, prev: dict[str, dict]) -> dict:
        async with self._sm() as db:
            rows = (await db.execute(select(Recording.kind, Recording.export_status, Recording.path, Recording.size_bytes).where(Recording.status == "ready"))).all()
            att = (await db.execute(select(func.count(), func.coalesce(func.sum(ChatAttachment.size), 0)).where(ChatAttachment.message_id.is_not(None)))).one()
            cfg = await self._ps._svc.get(db, "audio_storage")
            enabled = bool(getattr(cfg, "enabled", False))
            title, kind, address = await self._describe(db, cfg) if enabled else ("", "", "")
            storage = await self._ps._audio_storage(db) if enabled else None
            last = (await db.execute(select(StorageSyncRun).order_by(StorageSyncRun.started_at.desc()).limit(1))).scalar_one_or_none()
        root = self._ps._s.recordings_path
        local, external = await asyncio.to_thread(self._tally, rows, root)
        legacy_ext = "" if getattr(cfg, "profile_id", "") else "audio"
        ext_scan = os.path.join(address, "Audio" if getattr(cfg, "profile_id", "") else legacy_ext) if enabled and kind == "local" and address else None
        local_scan, ext_scan_res = await asyncio.gather(asyncio.to_thread(scan_dir, root), asyncio.to_thread(scan_dir, ext_scan) if ext_scan else _none())
        out = {"measured_at": _now(), "volumes": [], "sync": ({"at": last.finished_at.isoformat() if last and last.finished_at else None, "status": last.status, "orphans": last.orphans, "missing": last.missing} if last else None)}
        out["volumes"].append(await self._volume("local", "Локальный диск сервера", "local", root, local, None,
                                                 lambda: shutil.disk_usage(root if os.path.isdir(root) else os.path.dirname(root)), prev.get("local"), scan=local_scan))
        if enabled:
            out["volumes"].append(await self._volume("external", title or "Внешнее хранилище записей", kind, address, external, None,
                                                     (lambda: storage.volume()) if storage is not None else None, prev.get("external"), unavailable=storage is None, scan=ext_scan_res))
        else:
            out["volumes"].append({"id": "external", "title": "Внешнее хранилище записей", "kind": "", "address": "", "state": "not_configured", "total": None, "free": None,
                                   "used_percent": None, "files": _empty(), "measured_at": _now(), "last_ok_at": None, "stale": False})
        out["other"] = {"title": "Вложения чата", "count": int(att[0]), "bytes": int(att[1]),
                        "note": "Протоколы, резюме и стенограммы хранятся в базе данных и в этот расчёт не входят."}
        return out

    async def _describe(self, db, cfg) -> tuple[str, str, str]:
        pid = getattr(cfg, "profile_id", "")
        if pid:
            try:
                row = await self._ps.files.row(db, pid)
                return row.name, row.kind, self._ps.files.address(row.kind, row.config or {})
            except Exception:  # noqa: BLE001
                return "Внешнее хранилище записей", "", ""
        mode = getattr(cfg, "mode", "local")
        if mode == "smb":
            return "Внешнее хранилище записей (SMB)", "smb", self._ps.files.address("smb", {"smb_server": getattr(cfg, "smb_server", ""), "smb_share": getattr(cfg, "smb_share", ""), "smb_base_path": getattr(cfg, "smb_base_path", "")})
        return "Внешнее хранилище записей (папка)", "local", str(getattr(cfg, "local_path", ""))

    @staticmethod
    def _tally(rows, root: str) -> tuple[dict, dict]:
        """Разложить записи по месту: локальный диск (не выгруженные и выгруженные с локальной копией) и внешнее хранилище (выгруженные)."""
        local, external = _empty(), _empty()
        for kind, status, path, size in rows:
            k = kind if kind in KINDS else "participant"
            size = int(size or 0)
            if status == "exported":
                external[k]["count"] += 1
                external[k]["bytes"] += size
                try:
                    if not os.path.isfile(os.path.join(root, path)):
                        continue
                except OSError:
                    continue
            local[k]["count"] += 1
            local[k]["bytes"] += size
        return local, external

    async def _volume(self, vid, title, kind, address, files, other, probe, prev, unavailable: bool = False, scan: dict | None = None) -> dict:
        db_bytes = sum(x["bytes"] for x in files.values())
        v = {"id": vid, "title": title, "kind": kind, "address": address if vid != "local" else "", "state": "ok", "total": None, "free": None, "used_percent": None,
             "files": files, "measured_at": _now(), "last_ok_at": None, "stale": False, "db_bytes": db_bytes, "disk_bytes": None, "disk_files": None, "scan_truncated": False, "mismatch": False}
        if scan is not None:
            v.update(disk_bytes=scan["bytes"], disk_files=scan["files"], scan_truncated=scan["truncated"])
            # «в базе» и «на диске» расходятся, если на диске лежат файлы, о которых база не знает (осиротевшие, недокопированные), или файлы пропали
            v["mismatch"] = not scan["truncated"] and abs(scan["bytes"] - db_bytes) > max(1 << 20, db_bytes // 100)
        if other is not None:
            v["other"] = other
        err = "хранилище недоступно или настроено некорректно" if unavailable else None
        if probe is not None and not unavailable:
            try:
                t0 = time.monotonic()
                usage = await asyncio.wait_for(asyncio.to_thread(probe), VOLUME_TIMEOUT_S)
                total, free = (usage.total, usage.free) if hasattr(usage, "total") else usage
                v.update(total=int(total), free=int(free), used_percent=round((total - free) * 100 / total, 1) if total else None, last_ok_at=v["measured_at"], probe_ms=round((time.monotonic() - t0) * 1000))
            except asyncio.TimeoutError:
                err = f"нет ответа за {VOLUME_TIMEOUT_S} с"
            except (StorageError, OSError, NotImplementedError) as exc:
                err = str(exc)[:200]
        if err:
            v["state"], v["error"] = "unavailable", err
            if prev:                                                       # прошлые данные о месте — с пометкой «устарело»
                v.update(total=prev.get("total"), free=prev.get("free"), used_percent=prev.get("used_percent"), last_ok_at=prev.get("last_ok_at"), stale=True)
        return v


async def _none():
    return None


def scan_dir(path: str | None) -> dict | None:
    """Фактически занятое место каталога записей: сумма размеров файлов. Выполняется только в фоне; предел по числу файлов и времени."""
    if not path or not os.path.isdir(path):
        return None
    total = files = 0
    stack, t0, truncated = [path], time.monotonic(), False
    while stack and not truncated:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
                    if files >= SCAN_LIMIT or time.monotonic() - t0 > SCAN_BUDGET_S:
                        truncated = True
                        break
        except OSError:
            continue
    return {"bytes": total, "files": files, "truncated": truncated}


def _empty() -> dict:
    return {k: {"count": 0, "bytes": 0} for k in KINDS}
