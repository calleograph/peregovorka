"""Поиск и восстановление материалов встреч после переноса хранилища (Администрирование → Хранилища → «Восстановление материалов»).

Задача: файлы записей, стенограмм, протоколов, доски лежат на диске, а в базе нет встреч и связей — приложение их не показывает. Инструмент находит такие каталоги и,
по решению администратора, возвращает материалы в приложение. Основной надёжный способ переноса сервера остаётся прежним: база и хранилище вместе (docs/STORAGE.md);
это дополнительная возможность «подобрать» файлы, а не гарантия восстановления всего по одним файлам.

Правила безопасности:
* Источник только читается: исходные файлы не меняются, не переименовываются и не удаляются (даже при ошибках).
* Читаются только настроенные в приложении хранилища (профиль → Audio/Transcripts/Protocols/Chat/Boards) через общий слой хранения: выход за каталог по `..` и символьным ссылкам невозможен.
* **Достоверно** восстанавливается только встреча с паспортом `manifest.json`, подпись которого верна ключом этой установки (`manifest.verify` = `valid`): комната, участники и права
  берутся из паспорта, но только для существующих учётных записей и только по идентификаторам (GUID каталога или внутренний id), не по ФИО. Недостающая комната создаётся **выключенной**.
* Всё остальное (нет паспорта, подпись чужая, не сходится, или паспорт без подписи) — «предположение»: материалы кладутся в изолированную комнату «Восстановленные материалы»
  (выключена, доступ только администратору, участников и прав нет), под новым идентификатором встречи (идентификатор из неподтверждённого файла не доверяется).
* Контрольные суммы паспорта проверяются при восстановлении по каждому файлу; расходящийся файл пропускается и попадает в отчёт.
* Повторный запуск безопасен: восстановленное (встреча, запись, документ) не дублируется. Прерванную операцию можно запустить заново.
* Работа в фоне: HTTP-запрос только запускает задание. Нагрузка на диск ограничена паузами; перед каждым каталогом проверяется отмена.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import posixpath
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Meeting, MeetingGrant, MeetingParticipant, MeetingWhiteboard, Protocol, Recording, RestoreJob, Room, RoomAcl, RoomModerator, User, utcnow
from . import manifest as mf
from .storage import StorageBackend, StorageError, StorageNotFound

log = logging.getLogger("app.restore")
LOCK = "lock:restore"
CANCEL = "restore:cancel:"
MAX_DEPTH = 7
MAX_DIRS = 30000
MAX_ITEMS = 3000
MAX_TEXT = 4 * 1024 * 1024               # текстовый материал крупнее не читается в базу
STALE_AFTER = timedelta(minutes=10)
ISOLATED_SLUG = "restored-materials"
ISOLATED_NAME = "Восстановленные материалы (проверьте права)"
AREAS = (("Audio", "audio_storage", "audio"), ("Transcripts", "storage", ""), ("Protocols", "storage", ""), ("Chat", "storage", ""), ("Boards", "storage", ""))
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DATE_DIR = re.compile(r"^(\d{4})-(\d{2})-(\d{2}), ")
_TIME_DIR = re.compile(r"^(\d{2})-(\d{2})(?: \(.+\))?$")


class RestoreBusy(Exception):
    pass


def item_id(directory: str) -> str:
    return hashlib.sha1(directory.encode("utf-8")).hexdigest()[:12]


def guess_start(directory: str, tz) -> datetime | None:
    """Время начала по пути `Комната/ГГГГ-ММ-ДД, день/ЧЧ-ММ`: это предположение (пояс сервера из настроек, имя комнаты в пути очищено)."""
    parts = directory.split("/")
    if len(parts) < 3:
        return None
    d, t = _DATE_DIR.match(parts[-2]), _TIME_DIR.match(parts[-1])
    if not d or not t:
        return None
    try:
        return datetime(int(d.group(1)), int(d.group(2)), int(d.group(3)), int(t.group(1)), int(t.group(2)), tzinfo=tz).astimezone(timezone.utc)
    except ValueError:
        return None


class RestoreService:
    def __init__(self, sm: async_sessionmaker[AsyncSession], files, settings_svc, redis, recordings_path: str, master_key: str, journal=None, audit=None):
        self._sm, self._files, self._svc, self._redis, self._rec_path, self._key = sm, files, settings_svc, redis, recordings_path, master_key
        self.journal, self._audit = journal, audit
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------------------------------ управление заданиями
    async def start(self, kind: str, *, user_id, user_name: str, params: dict, parent_id: uuid.UUID | None = None) -> RestoreJob:
        """Создаёт задание и запускает его в фоне. Одновременно работает одно задание на весь кластер."""
        if not await self._redis.set(LOCK, "1", nx=True, ex=2 * 3600):
            async with self._sm() as db:
                cur = (await db.execute(select(RestoreJob).where(RestoreJob.status == "running").order_by(RestoreJob.created_at.desc()).limit(1))).scalar_one_or_none()
                if cur is None or cur.heartbeat_at is None or utcnow() - cur.heartbeat_at > STALE_AFTER:
                    await self._redis.delete(LOCK)                       # прежнее задание оборвалось (перезапуск сервера): блокировка устарела
                    await self._redis.set(LOCK, "1", nx=True, ex=2 * 3600)
                else:
                    raise RestoreBusy("Уже выполняется другое сканирование или восстановление. Дождитесь окончания или отмените его.")
        async with self._sm() as db:
            for stale in (await db.execute(select(RestoreJob).where(RestoreJob.status == "running"))).scalars():
                stale.status, stale.error, stale.finished_at = "failed", "Прервано перезапуском сервера: запустите заново (уже сделанное не повторится).", utcnow()
            job = RestoreJob(kind=kind, parent_id=parent_id, status="running", params=params, progress={}, started_by_user_id=user_id, started_by_name=user_name[:200], heartbeat_at=utcnow())
            db.add(job)
            await db.commit()
            await db.refresh(job)
        run = self._scan if kind == "scan" else self._restore
        task = asyncio.create_task(self._guard(job.id, run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def cancel(self, job_id: uuid.UUID) -> None:
        await self._redis.set(CANCEL + str(job_id), "1", ex=3600)

    async def _cancelled(self, job_id: uuid.UUID) -> bool:
        return bool(await self._redis.get(CANCEL + str(job_id)))

    async def _guard(self, job_id: uuid.UUID, run) -> None:
        try:
            await run(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — любое падение фиксируется в задании, а не теряется в фоне
            log.exception("Задание восстановления завершилось ошибкой", extra={"job": str(job_id)})
            await self._finish(job_id, "failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            await self._redis.delete(LOCK)

    async def _finish(self, job_id: uuid.UUID, status: str, *, result: dict | None = None, error: str | None = None) -> None:
        async with self._sm() as db:
            job = await db.get(RestoreJob, job_id)
            if job is not None:
                job.status, job.finished_at, job.error = status, utcnow(), error
                if result is not None:
                    job.result = result
                await db.commit()

    async def _beat(self, job_id: uuid.UUID, **progress) -> None:
        async with self._sm() as db:
            job = await db.get(RestoreJob, job_id)
            if job is not None:
                job.heartbeat_at, job.progress = utcnow(), {**(job.progress or {}), **progress}
                await db.commit()
        await self._redis.expire(LOCK, 2 * 3600)

    # ------------------------------------------------------------------------------------------ сканирование
    async def _areas(self, db: AsyncSession, subpath: str) -> list[tuple[str, StorageBackend]]:
        out: list[tuple[str, StorageBackend]] = []
        for folder, group, legacy in AREAS:
            try:
                st = await self._files.backend(db, group, folder, legacy_prefix=legacy)
            except StorageError:
                st = None
            if st is not None:
                out.append((folder, st))
        return out

    def _walk(self, storage: StorageBackend, start: str, budget: dict, on_dir) -> list[tuple[str, list[str]]]:
        """Каталоги с материалами (блокирующе, в потоке): (путь, имена). Поиск в ширину с ограничением глубины и числа каталогов; перед каждым каталогом — проверка отмены."""
        import time  # noqa: PLC0415

        found: list[tuple[str, list[str]]] = []
        queue: list[tuple[str, int]] = [(start, 0)]
        while queue:
            cur, depth = queue.pop(0)
            if budget["dirs"] >= MAX_DIRS or on_dir(cur):
                budget["truncated"] = budget["dirs"] >= MAX_DIRS
                break
            budget["dirs"] += 1
            try:
                names = storage.list_dir(cur)
            except StorageError:
                continue
            here = [n for n in names if mf.classify(n) or n == mf.FILENAME]
            if here and cur:                                            # файлы прямо в корне хранилища — не каталог встречи
                found.append((cur, here))
            if depth < MAX_DEPTH:
                for n in names:
                    if mf.classify(n) or n == mf.FILENAME or n.endswith((".tmp", ".partial")) or n.startswith("."):
                        continue
                    queue.append((f"{cur}/{n}" if cur else n, depth + 1))
            time.sleep(0.003)                                           # не нагружать диск и сеть подряд идущими обходами
        return found

    async def _scan(self, job_id: uuid.UUID) -> None:
        async with self._sm() as db:
            job = await db.get(RestoreJob, job_id)
            params = dict(job.params or {})
            tz = await self._tz(db)
            areas = await self._areas(db, "")
        sub = str(params.get("subpath") or "").strip().strip("/")
        deep = bool(params.get("deep"))
        loop = asyncio.get_running_loop()
        budget = {"dirs": 0, "truncated": False}
        cancelled = {"v": False}
        counter = {"n": 0}

        def on_dir(_cur: str) -> bool:
            counter["n"] += 1
            if counter["n"] % 25 == 0:                                   # отмена и сердцебиение не чаще, чем раз в 25 каталогов
                fut = asyncio.run_coroutine_threadsafe(self._cancelled(job_id), loop)
                try:
                    cancelled["v"] = bool(fut.result(timeout=10))
                    asyncio.run_coroutine_threadsafe(self._beat(job_id, dirs=budget["dirs"], area=_cur.split("/")[0] if _cur else ""), loop)
                except Exception:  # noqa: BLE001
                    pass
            return cancelled["v"]

        by_dir: dict[str, dict[str, Any]] = {}
        for folder, storage in areas:
            found = await asyncio.to_thread(self._walk, storage, sub, budget, on_dir)
            for d, names in found:
                it = by_dir.setdefault(d, {"dir": d, "areas": {}, "manifest": None, "manifest_area": None})
                it["areas"][folder] = {"names": names}
                if mf.FILENAME in names and it["manifest"] is None:
                    try:
                        doc = json.loads(storage.read_bytes(f"{d}/{mf.FILENAME}").decode("utf-8"))
                        it["manifest"], it["manifest_area"] = doc, folder
                    except (StorageError, ValueError, UnicodeDecodeError):
                        it["manifest"], it["manifest_area"] = {"__broken": True}, folder
            if cancelled["v"]:
                break
        if cancelled["v"]:
            await self._finish(job_id, "cancelled", result={"items": [], "counts": {}, "note": "Сканирование отменено."})
            return
        items: list[dict] = []
        async with self._sm() as db:
            for d, it in sorted(by_dir.items()):
                if len(items) >= MAX_ITEMS:
                    break
                items.append(await self._describe(db, it, dict(areas), tz, deep))
        counts = {"found": len(items), "verified": sum(1 for i in items if i["trust"] == "valid"), "supposed": sum(1 for i in items if i["trust"] != "valid"),
                  "new": sum(1 for i in items if i["status"] == "new"), "partial": sum(1 for i in items if i["status"] == "partial"),
                  "present": sum(1 for i in items if i["status"] == "present"), "problem": sum(1 for i in items if i["status"] == "problem"), "dirs": budget["dirs"]}
        await self._finish(job_id, "done", result={"items": items, "counts": counts, "truncated": budget["truncated"] or len(by_dir) > MAX_ITEMS})

    async def _tz(self, db: AsyncSession):
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        try:
            g = await self._svc.get(db, "general")
            return ZoneInfo(getattr(g, "timezone", "") or "UTC")
        except Exception:  # noqa: BLE001
            return timezone.utc

    async def _describe(self, db: AsyncSession, it: dict, areas: dict[str, StorageBackend], tz, deep: bool) -> dict:
        d = it["dir"]
        doc = it["manifest"]
        trust, why = ("none", "Паспорта (manifest.json) нет: данные определены только по именам файлов и каталогов.")
        if doc is not None:
            trust, why = mf.verify(doc, self._key) if not doc.get("__broken") else ("invalid", "Файл manifest.json не читается или повреждён.")
        files: list[dict] = []
        problems: list[str] = []
        listed = {f: a["names"] for f, a in it["areas"].items()}
        want: dict[tuple[str, str], dict] = {}
        for folder, names in listed.items():
            for n in names:
                if mf.classify(n):
                    want[(folder, n)] = {"area": folder, "name": n, "kind": mf.classify(n)[0], "state": "found"}
        # файлы паспорта: проверка наличия, размера и (по желанию) суммы
        mfiles: dict[tuple[str, str], dict] = {}
        if trust in ("valid", "foreign", "unsigned") and isinstance(doc, dict) and not doc.get("__broken"):
            for f in doc.get("files") or []:
                if isinstance(f, dict) and isinstance(f.get("name"), str):
                    mfiles[(str(doc.get("folder") or it["manifest_area"]), f["name"])] = f
        for key, row in list(want.items()):
            mfile = mfiles.get(key)
            storage = areas.get(key[0])
            if mfile is not None and storage is not None:
                try:
                    size = await asyncio.to_thread(storage.size_of, f"{d}/{key[1]}")
                    if size != mfile.get("size"):
                        row["state"] = "mismatch"
                        problems.append(f"{key[1]}: размер не совпадает с паспортом")
                    elif deep:
                        _, digest = await asyncio.to_thread(mf.hash_file, storage, f"{d}/{key[1]}", throttle=0.002)
                        row["state"] = "ok" if digest == mfile.get("sha256") else "mismatch"
                        if row["state"] == "mismatch":
                            problems.append(f"{key[1]}: контрольная сумма не совпадает")
                    else:
                        row["state"] = "ok_size"
                except StorageError:
                    row["state"] = "unreadable"
            row["size"] = mfile.get("size") if mfile else None
            files.append(row)
        for key, mfile in mfiles.items():
            if key not in want:
                files.append({"area": key[0], "name": key[1], "kind": mfile.get("kind"), "state": "missing", "size": mfile.get("size")})
                problems.append(f"{key[1]}: в паспорте есть, а в каталоге нет")
        meeting_id = None
        room = None
        started = None
        if isinstance(doc, dict) and trust == "valid":                   # название комнаты, время и идентификатор берутся только из подтверждённого паспорта; из чужого — лишь путь
            m = doc.get("meeting") or {}
            r = doc.get("room") or {}
            started = m.get("started_at")
            try:
                meeting_id = str(uuid.UUID(str(m.get("id"))))
            except (ValueError, TypeError):
                meeting_id = None
            room = {"name": str(r.get("name") or "")[:200], "id": str(r.get("id") or "")[:40]}
        else:
            g = guess_start(d, tz)
            started = g.isoformat().replace("+00:00", "Z") if g else None
            room = {"name": d.split("/")[0] if "/" in d else "", "id": ""}
        # сопоставление с базой (только чтение)
        status, note = "new", ""
        exists_meeting = await db.get(Meeting, uuid.UUID(meeting_id)) if (trust == "valid" and meeting_id) else None
        linked = await self._linked(db, d, exists_meeting.id if exists_meeting else None)
        usable = [f for f in files if f["kind"] in LINKABLE and f["state"] in ("ok", "ok_size", "found")]
        todo = [f for f in usable if not linked.get((f["area"], f["name"]))]
        if exists_meeting is not None:
            status = "present" if not todo else "partial"
            note = "Встреча уже есть в базе: будут добавлены только недостающие материалы." if todo else "Всё уже на месте."
        else:
            if trust != "valid" and await self._match_loose(db, d, started) is not None:
                note = "Похожая встреча (та же комната и время начала) уже есть в базе; найденные файлы будут помещены в изолированную область для проверки."
            if usable and not todo:
                status, note = "present", "Все файлы уже привязаны к встречам в базе."
            elif not usable and files:
                status = "problem"
        people = {"found": 0, "missing": 0}
        if trust == "valid" and isinstance(doc, dict):
            for p in doc.get("participants") or []:
                u = await self._find_user(db, p)
                people["found" if u else "missing"] += 1
        return {"id": item_id(d), "dir": d, "mode": "manifest" if doc is not None else "files", "trust": trust, "trust_note": why, "status": status, "note": note,
                "meeting_id": meeting_id, "room": room, "started_at": started, "files": files, "problems": problems, "people": people,
                "how": ("Достоверно: подпись паспорта верна ключом этой установки — комната, участники и права восстановятся по идентификаторам." if trust == "valid"
                        else "Предположительно: права и участники не определены — материалы попадут в изолированную область «Восстановленные материалы», доступную только администратору.")}

    async def _linked(self, db: AsyncSession, directory: str, meeting_id) -> dict[tuple[str, str], bool]:
        """Какие файлы каталога уже привязаны в базе к какой-либо встрече (по относительному пути/каталогу): их не восстанавливаем второй раз."""
        out: dict[tuple[str, str], bool] = {}
        recs = (await db.execute(select(Recording.path).where(Recording.path.like(directory + "/%")))).scalars().all()
        for p in recs:
            out[("Audio", posixpath.basename(p))] = True
        for kind, name, area in (("transcript", "protocol.txt", "Transcripts"), ("protocol", "official-protocol.md", "Protocols"), ("summary", "summary.md", "Protocols")):
            rows = (await db.execute(select(Protocol.meta).where(Protocol.kind == kind, Protocol.status == "ready"))).scalars().all()
            if any((m or {}).get("dir") == directory for m in rows):
                out[(area, name)] = True
        if meeting_id is not None and (await db.get(MeetingWhiteboard, meeting_id)) is not None:
            out[("Boards", "whiteboard.drawio")] = True
        return out

    async def _match_loose(self, db: AsyncSession, directory: str, started: str | None) -> Meeting | None:
        if not started:
            return None
        try:
            t = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            return None
        rows = (await db.execute(select(Meeting).where(Meeting.started_at >= t - timedelta(minutes=1), Meeting.started_at <= t + timedelta(minutes=1)))).scalars().all()
        from .storage import safe_component  # noqa: PLC0415

        return next((m for m in rows if safe_component(m.room.name) == directory.split("/")[0]), None)

    async def _find_user(self, db: AsyncSession, p: dict) -> User | None:
        """Сотрудник по идентификатору из ПРОВЕРЕННОГО паспорта: GUID каталога (стабилен при переносе) либо внутренний id. По имени никогда."""
        guid = str(p.get("ad_guid") or "").lower()
        if guid and _GUID.match(guid):
            u = (await db.execute(select(User).where(User.ad_guid == guid))).scalars().first()
            if u is not None:
                return u
        try:
            return await db.get(User, uuid.UUID(str(p.get("user_id"))))
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------------------------------ восстановление
    async def _restore(self, job_id: uuid.UUID) -> None:
        async with self._sm() as db:
            job = await db.get(RestoreJob, job_id)
            params = dict(job.params or {})
            scan = await db.get(RestoreJob, job.parent_id) if job.parent_id else None
            areas = dict(await self._areas(db, ""))
            user_name = job.started_by_name
        if scan is None or scan.status != "done" or not scan.result:
            await self._finish(job_id, "failed", error="Результат сканирования не найден: выполните сканирование заново.")
            return
        wanted = set(params.get("items") or [])
        items = [i for i in scan.result["items"] if i["id"] in wanted]
        report: list[dict] = []
        for n, it in enumerate(items):
            if await self._cancelled(job_id):
                report.append({"id": it["id"], "dir": it["dir"], "outcome": "skipped", "lines": ["Не обработано: восстановление отменено."]})
                continue
            await self._beat(job_id, done=n, total=len(items), current=it["dir"])
            try:
                async with self._sm() as db:
                    row = await self._restore_one(db, it, areas, job_id, user_name)
                report.append(row)
            except Exception as exc:  # noqa: BLE001 — одна встреча не должна останавливать остальные
                log.exception("Встреча не восстановлена", extra={"dir": it["dir"]})
                report.append({"id": it["id"], "dir": it["dir"], "outcome": "failed", "lines": [f"Ошибка: {type(exc).__name__}: {str(exc)[:200]}"]})
            await asyncio.sleep(0.01)
        counts = {"restored": sum(1 for r in report if r["outcome"] == "restored"), "isolated": sum(1 for r in report if r["outcome"] == "isolated"),
                  "skipped": sum(1 for r in report if r["outcome"] == "skipped"), "failed": sum(1 for r in report if r["outcome"] == "failed")}
        await self._finish(job_id, "cancelled" if await self._cancelled(job_id) else "done", result={"report": report, "counts": counts})
        if self._audit is not None:
            await self._audit(actor=user_name or "администратор", action="restore.finish", target_id=str(job_id), details=counts)

    async def _isolated_room(self, db: AsyncSession) -> Room:
        room = (await db.execute(select(Room).where(Room.slug == ISOLATED_SLUG))).scalars().first()
        if room is None:
            room = Room(slug=ISOLATED_SLUG, name=ISOLATED_NAME, description="Материалы, права на которые не удалось подтвердить. Видны только администратору; перенесите встречу в нужную комнату вручную.",
                        lifetime="permanent", lifecycle="active", is_enabled=False, history_access="admin")
            db.add(room)
            await db.flush()
        return room

    async def _restore_one(self, db: AsyncSession, it: dict, areas: dict[str, StorageBackend], job_id: uuid.UUID, user_name: str) -> dict:
        d = it["dir"]
        lines: list[str] = []
        trusted = it["trust"] == "valid"
        doc = None
        sums: dict[str, str] = {}
        if trusted:
            for folder in sorted({f["area"] for f in it["files"]}):                 # у каждой папки свой паспорт со своими файлами
                st = areas.get(folder)
                if st is None:
                    continue
                try:
                    cand = json.loads(st.read_bytes(f"{d}/{mf.FILENAME}").decode("utf-8"))
                except (StorageError, ValueError, UnicodeDecodeError):
                    continue
                if mf.verify(cand, self._key)[0] != "valid":
                    continue
                if doc is None:
                    doc = cand
                elif (cand.get("meeting") or {}).get("id") != (doc.get("meeting") or {}).get("id"):
                    continue                                                       # паспорт другой встречи в том же каталоге не смешиваем
                for f in cand.get("files") or []:
                    if isinstance(f, dict) and isinstance(f.get("name"), str) and isinstance(f.get("sha256"), str):
                        sums[f"{folder}/{f['name']}"] = f["sha256"]
        trusted = doc is not None            # паспорт перечитан и проверен ещё раз прямо перед применением (с момента сканирования его могли заменить)
        meeting: Meeting | None = None
        if trusted:
            meeting, room, how = await self._materialise_trusted(db, doc, lines)
        else:
            if it["trust"] == "valid":
                lines.append("Паспорт изменился после сканирования или не проходит проверку подписи — восстановление как предположительное.")
            room = await self._isolated_room(db)
            started = it.get("started_at")
            try:
                t0 = datetime.fromisoformat(str(started).replace("Z", "+00:00")) if started else None
            except ValueError:
                t0 = None
            mid = uuid.uuid4()
            meeting = Meeting(id=mid, room_id=room.id, livekit_room=f"restored-{mid.hex}", started_at=t0 or utcnow(), ended_at=(t0 or utcnow()) + timedelta(minutes=1),
                              end_reason="restored_unverified", transcription_enabled=False, record_audio=False)
            existing = await self._existing_isolated(db, room, d)
            if existing is not None:
                meeting = existing
                lines.append("Эта папка уже была восстановлена в изолированную область: добавлены только недостающие материалы.")
            else:
                db.add(meeting)
                await db.flush()
                lines.append("Права и участники не определены: встреча помещена в изолированную область «Восстановленные материалы» (только для администратора).")
            how = "isolated"
        added = await self._attach_files(db, meeting, it, areas, d, trusted, job_id, lines, sums)
        await db.commit()
        if self._audit is not None:
            await self._audit(actor=user_name or "администратор", action="restore.item", target_id=str(meeting.id), details={"dir": d, "mode": how, "files": added})
        return {"id": it["id"], "dir": d, "outcome": "restored" if trusted else "isolated", "meeting_id": str(meeting.id), "lines": lines, "files": added}

    async def _existing_isolated(self, db: AsyncSession, room: Room, directory: str) -> Meeting | None:
        rows = (await db.execute(select(Protocol).where(Protocol.title == f"Восстановлено: {directory}"))).scalars().all()
        for p in rows:
            m = await db.get(Meeting, p.meeting_id)
            if m is not None and m.room_id == room.id:
                return m
        recs = (await db.execute(select(Recording).where(Recording.path.like(directory + "/%"), Recording.room_id == room.id))).scalars().first()
        return await db.get(Meeting, recs.meeting_id) if recs is not None else None

    async def _materialise_trusted(self, db: AsyncSession, doc: dict, lines: list[str]) -> tuple[Meeting, Room, str]:
        m, r, acc = doc.get("meeting") or {}, doc.get("room") or {}, doc.get("access") or {}
        mid = uuid.UUID(str(m["id"]))
        meeting = await db.get(Meeting, mid)
        room: Room | None = None
        if meeting is not None:
            room = await db.get(Room, meeting.room_id)
            lines.append("Встреча уже есть в базе: добавлены недостающие материалы, права не менялись.")
            return meeting, room, "existing"
        try:
            room = await db.get(Room, uuid.UUID(str(r.get("id"))))
        except (ValueError, TypeError):
            room = None
        if room is None:
            slug = str(r.get("slug") or "")[:60] or f"restored-{mid.hex[:8]}"
            if (await db.execute(select(Room.id).where(Room.slug == slug))).first() is not None:
                slug = f"{slug[:48]}-restored-{mid.hex[:6]}"
            room = Room(id=uuid.UUID(str(r["id"])) if _is_uuid(r.get("id")) else uuid.uuid4(), slug=slug, name=(str(r.get("name") or "Восстановленная комната"))[:200],
                        lifetime="permanent", lifecycle="active", is_enabled=False, history_access=str(r.get("history_access") or "admin") if str(r.get("history_access")) in ("admin", "participants") else "admin")
            db.add(room)
            await db.flush()
            for a in acc.get("acl") or []:
                if _acl_ok(a):
                    db.add(RoomAcl(room_id=room.id, subject_type=a["type"], subject_ref=a["ref"], display_name=a["ref"][:200]))
            for a in acc.get("moderators") or []:
                if _acl_ok(a):
                    db.add(RoomModerator(room_id=room.id, subject_type=a["type"], subject_ref=a["ref"], display_name=a["ref"][:200]))
            lines.append(f"Комната «{room.name}» не найдена — создана выключенной, права комнаты взяты из подписанного паспорта. Включите её после проверки.")
        else:
            lines.append(f"Комната «{room.name}» найдена по идентификатору; её права не менялись.")
        started = _dt(m.get("started_at")) or utcnow()
        ended = _dt(m.get("ended_at")) or started + timedelta(minutes=1)
        meeting = Meeting(id=mid, room_id=room.id, livekit_room=f"restored-{mid.hex}", started_at=started, ended_at=ended, end_reason="restored",
                          transcription_enabled=bool(m.get("transcription", True)), record_audio=bool(m.get("record_audio", False)))
        db.add(meeting)
        await db.flush()
        miss = 0
        seen: set = set()
        for p in doc.get("participants") or []:
            u = await self._find_user(db, p)
            if u is None:
                miss += 1
                continue
            if u.id in seen:
                continue
            seen.add(u.id)
            db.add(MeetingParticipant(meeting_id=meeting.id, user_id=u.id, joined_at=_dt(p.get("joined_at")) or started, left_at=_dt(p.get("left_at")) or ended))
        for g in acc.get("grants") or []:
            u = await self._find_user(db, g)
            if u is not None and u.id not in seen:
                db.add(MeetingGrant(meeting_id=meeting.id, user_id=u.id, granted_by="восстановление по паспорту"))
            elif u is None:
                miss += 1
        lines.append(f"Участники и разрешения: восстановлено {len(seen)}" + (f", не найдено учётных записей: {miss} — доступ им не выдан." if miss else "."))
        return meeting, room, "trusted"

    async def _attach_files(self, db: AsyncSession, meeting: Meeting, it: dict, areas: dict[str, StorageBackend], d: str, trusted: bool, job_id: uuid.UUID, lines: list[str], sums: dict[str, str]) -> list[str]:
        added: list[str] = []
        have_paths = set((await db.execute(select(Recording.path).where(Recording.meeting_id == meeting.id))).scalars().all())
        protos = {(p.kind, (p.meta or {}).get("dir")) for p in (await db.execute(select(Protocol).where(Protocol.meeting_id == meeting.id))).scalars()}
        first_title = not protos
        for f in it["files"]:
            kind, area, name = f["kind"], f["area"], f["name"]
            if kind not in LINKABLE:
                continue
            storage = areas.get(area)
            rel = f"{d}/{name}"
            if storage is None:
                lines.append(f"{name}: хранилище «{area}» не настроено — пропущен.")
                continue
            try:
                size, digest = await asyncio.to_thread(mf.hash_file, storage, rel, throttle=0.002)
            except StorageNotFound:
                lines.append(f"{name}: файл не найден — пропущен.")
                continue
            except StorageError as exc:
                lines.append(f"{name}: не читается ({str(exc)[:80]}) — пропущен.")
                continue
            want = sums.get(f"{area}/{name}") if trusted else None
            if want and want != digest:
                lines.append(f"{name}: контрольная сумма не совпадает с паспортом — файл повреждён или изменён, пропущен.")
                continue
            if kind in ("audio_participant", "audio_mix"):
                if rel in have_paths:
                    continue
                db.add(Recording(meeting_id=meeting.id, room_id=meeting.room_id, participant_identity="restored" if kind == "audio_participant" else "meeting", path=rel, size_bytes=size,
                                 kind="participant" if kind == "audio_participant" else "mix_audio", mime="audio/wav" if name.lower().endswith(".wav") else "audio/mp4", status="ready",
                                 export_status="exported", export_location=f"{area}/{rel}", file_state="ok", sha256=digest, created_at=utcnow()))
                added.append(name)
            elif kind in ("transcript", "protocol", "summary"):
                if (kind, d) in protos:
                    continue
                if size > MAX_TEXT:
                    lines.append(f"{name}: слишком большой текст ({size} байт) — пропущен.")
                    continue
                text = (await asyncio.to_thread(storage.read_bytes, rel)).decode("utf-8", "replace")
                db.add(Protocol(meeting_id=meeting.id, kind=kind, status="ready", content=text, created_by="восстановление", title=(f"Восстановлено: {d}" if first_title else None),
                                meta={"dir": d, "location": f"{area}/{rel}", "restored": str(job_id), "sha256": digest, "trusted": trusted}))
                first_title = False
                protos.add((kind, d))
                added.append(name)
            elif kind == "board":
                if await db.get(MeetingWhiteboard, meeting.id) is not None:
                    continue
                xml = (await asyncio.to_thread(storage.read_bytes, rel)).decode("utf-8", "replace")
                if size <= MAX_TEXT and xml.lstrip().startswith("<"):
                    db.add(MeetingWhiteboard(meeting_id=meeting.id, xml=xml, seq=0, shapes=max(1, xml.count("<mxCell") - 2), updated_by="восстановление"))
                    added.append(name)
        if any(f["kind"] == "chat" for f in it["files"]):
            lines.append("Переписка встречи (chat.txt) в чат не восстанавливается: текст остаётся файлом в хранилище.")
        if any(f["kind"] == "transcript" for f in it["files"]):
            lines.append("Стенограмма восстановлена как документ: по репликам с привязкой к записи она не воссоздаётся.")
        lines.append(f"Материалы: добавлено {len(added)}" + (f" ({', '.join(added[:6])}{'…' if len(added) > 6 else ''})." if added else "."))
        return added


LINKABLE = {"audio_participant", "audio_mix", "transcript", "protocol", "summary", "board"}


def _is_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except (ValueError, TypeError):
        return False


def _dt(v: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _acl_ok(a: Any) -> bool:
    """Запись прав из паспорта: тип и ссылка проверяются по форме (группа — DN, сотрудник — GUID); произвольные значения не принимаются."""
    if not isinstance(a, dict) or a.get("type") not in ("group", "user"):
        return False
    ref = str(a.get("ref") or "")
    return bool(ref) and len(ref) <= 500 and (_GUID.match(ref) is not None if a["type"] == "user" else "=" in ref)
