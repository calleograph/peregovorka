"""Автоматическая копия настроек на сетевое хранилище (SMB или папку).

Архив собирается тем же кодом, что и ручной экспорт, и шифруется (AES-256-GCM, ключ из пароля через scrypt) ДО отправки в хранилище: на общем ресурсе
лежит только шифртекст. Пароль шифрования (`archive_key`) создаётся сервером, показывается администратору один раз и хранится в настройках только в
зашифрованном виде (ключом APP_MASTER_KEY); в саму копию он не попадает и рядом с ней не лежит.

Запись атомарна: сначала временный файл `.partial`, чтение обратно и сверка SHA-256, затем переименование в итоговое имя и файл контрольной суммы.
После успешной записи лишние старые копии удаляются (хранится `keep` последних). Каждое задание — запись в `config_backup_runs` и в журнал аудита.
Копия записей и документов — отдельный режим, здесь не реализован: копируются только настройки.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..models import ConfigBackupRun, utcnow
from .audit import audit_now
from .config_backup import container, exporter, importer
from .filestore import PrefixedStorage
from .storage import StorageError

log = logging.getLogger("app.auto_backup")

FOLDER = "ConfigBackups"
NAME_RE = re.compile(r"^peregovorka-config-\d{8}-\d{6}\.pgcfg$")
LOCK_KEY = "autobackup:lock"
LOCK_TTL_S = 1800
RETRY_AFTER = timedelta(hours=1)


class BackupError(Exception):
    pass


def new_key() -> str:
    return container.generate_password()


def file_name(now: datetime) -> str:
    return f"peregovorka-config-{now.strftime('%Y%m%d-%H%M%S')}.pgcfg"


def rotation_victims(names: list[str], keep: int) -> list[str]:
    """Имена копий, которые нужно удалить: всё, кроме `keep` самых новых (имя содержит время, поэтому сортировка по имени = по времени)."""
    arch = sorted((n for n in names if NAME_RE.match(n)), reverse=True)
    return arch[keep:]


def is_due(now_local: datetime, hhmm: str, every_days: int, last_auto: ConfigBackupRun | None, tz: ZoneInfo) -> bool:
    """Пора ли запускать плановую копию: наступило время, и с прошлой попытки прошло `every_days` суток; неудачная попытка повторяется раз в час."""
    h, m = (int(x) for x in hhmm.split(":"))
    if now_local < now_local.replace(hour=h, minute=m, second=0, microsecond=0):
        return False
    if last_auto is None:
        return True
    last = last_auto.started_at.astimezone(tz)
    failed = last_auto.status == "failed" or (last_auto.status == "running" and now_local - last >= 2 * RETRY_AFTER)     # «зависшее» задание считается неудачным
    if failed:
        return now_local - last >= RETRY_AFTER
    return last_auto.status != "running" and (now_local.date() - last.date()).days >= every_days


class AutoBackup:
    def __init__(self, app):
        self._app = app

    # --------------------------------------------------------------------------------------- хранилище и ключ
    async def _target(self, db, cfg) -> PrefixedStorage:
        if not cfg.profile_id:
            raise BackupError("Не выбрано хранилище для копий.")
        fs = self._app.state.files
        try:
            row = await fs.row(db, cfg.profile_id)
            base = await asyncio.to_thread(fs.build, row)
        except Exception as exc:  # noqa: BLE001
            raise BackupError(f"Хранилище недоступно: {exc}") from None
        return PrefixedStorage(base, FOLDER)

    @staticmethod
    def _key(cfg) -> str:
        if not cfg.archive_key:
            raise BackupError("Не создан ключ шифрования копий. Создайте его в настройках автокопирования.")
        return cfg.archive_key

    async def _tz(self, db) -> ZoneInfo:
        try:
            return ZoneInfo((await self._app.state.settings_svc.get(db, "general")).timezone)     # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    # --------------------------------------------------------------------------------------- задания
    async def _begin(self, kind: str, trigger: str, actor: str | None) -> ConfigBackupRun:
        async with self._app.state.session_maker() as db:
            run = ConfigBackupRun(kind=kind, trigger=trigger, actor=actor, status="running")
            db.add(run)
            await db.commit()
            await db.refresh(run)
            return run

    async def _end(self, run: ConfigBackupRun, status: str, message: str, **fields) -> None:
        async with self._app.state.session_maker() as db:
            row = await db.get(ConfigBackupRun, run.id)
            if row is None:
                return
            row.status, row.message, row.finished_at = status, message[:500], utcnow()
            for k, v in fields.items():
                setattr(row, k, v)
            await db.commit()
            for k in ("status", "message", "finished_at", *fields):
                setattr(run, k, getattr(row, k))

    async def _audit(self, action: str, run: ConfigBackupRun, actor_id, ok: bool, **details) -> None:
        await audit_now(self._app, actor_user_id=actor_id, actor_name=run.actor or "планировщик", action=action, target_type="config_backup", target_id=run.file_name or str(run.id),
                        result="success" if ok else "failure", actor_type="user" if actor_id else "system",
                        details={"trigger": run.trigger, "run": str(run.id), "size_bytes": run.size_bytes, "sha256": run.sha256, **details})

    async def create(self, trigger: str, actor: str | None = None, actor_id=None) -> ConfigBackupRun:
        """Создать копию сейчас. Результат (в том числе ошибка) записывается в историю и аудит; исключения наружу не выходят."""
        redis = self._app.state.redis
        if not await redis.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL_S):
            raise BackupError("Копирование уже выполняется.")
        run = await self._begin("backup", trigger, actor)
        try:
            s = self._app.state
            async with s.session_maker() as db:
                cfg = await s.settings_svc.get(db, "auto_backup")
                key = self._key(cfg)
                backend = await self._target(db, cfg)
                payload = await exporter.build_payload(db, s.settings_svc, app_version=s.settings.app_version, public_url=s.settings.app_public_url,
                                                       branding=s.branding, env_settings=s.settings)
            blob = await asyncio.to_thread(container.seal, payload, key, app_version=s.settings.app_version)
            del payload
            name = file_name(datetime.now(timezone.utc))
            sha = hashlib.sha256(blob).hexdigest()
            victims = await asyncio.to_thread(self._store, backend, name, blob, sha, cfg.keep)
            await self._end(run, "ok", "Копия создана" + (f"; удалено старых: {victims}" if victims else ""), file_name=name, size_bytes=len(blob), sha256=sha)
            await self._audit("backup.create", run, actor_id, True, rotated=victims)
        except Exception as exc:  # noqa: BLE001 — любой сбой фиксируется в истории; сообщение без секретов
            msg = str(exc) if isinstance(exc, (BackupError, StorageError, exporter.ExportError)) else f"Непредвиденная ошибка ({type(exc).__name__})"
            if not isinstance(exc, (BackupError, StorageError, exporter.ExportError)):
                log.exception("Автокопия настроек не выполнена")
            await self._end(run, "failed", msg)
            await self._audit("backup.create", run, actor_id, False, error=msg[:200])
        finally:
            await redis.delete(LOCK_KEY)
        return run

    @staticmethod
    def _store(backend: PrefixedStorage, name: str, blob: bytes, sha: str, keep: int) -> int:
        """Атомарная запись: .partial → проверка чтением → переименование → контрольная сумма → ротация. Возвращает число удалённых старых копий."""
        for leftover in (n for n in backend.list_dir("") if n.endswith(".partial")):          # остатки прерванных записей
            backend.delete(leftover)
        tmp = f"{name}.partial"
        backend.write_bytes(tmp, blob)
        if hashlib.sha256(backend.read_bytes(tmp)).hexdigest() != sha:
            backend.delete(tmp)
            raise BackupError("Контрольная сумма записанного файла не совпала: копия не сохранена.")
        backend.rename(tmp, name)
        backend.write_bytes(f"{name}.sha256", f"{sha}  {name}\n".encode())
        victims = rotation_victims(backend.list_dir(""), keep)
        for v in victims:
            backend.delete(v)
            backend.delete(f"{v}.sha256")
        return len(victims)

    async def verify(self, name: str | None, actor: str | None, actor_id=None) -> ConfigBackupRun:
        """Проверка копии: файл на месте, сумма совпадает с записанной, архив расшифровывается ключом и проходит проверку структуры. Ничего не применяется."""
        run = await self._begin("verify", "manual" if actor else "auto", actor)
        try:
            s = self._app.state
            async with s.session_maker() as db:
                cfg = await s.settings_svc.get(db, "auto_backup")
                key = self._key(cfg)
                backend = await self._target(db, cfg)
                if not name:
                    last = (await db.execute(select(ConfigBackupRun).where(ConfigBackupRun.kind == "backup", ConfigBackupRun.status == "ok")
                                             .order_by(ConfigBackupRun.started_at.desc()).limit(1))).scalar_one_or_none()
                    name = last.file_name if last else None
            if not name or not NAME_RE.match(name):
                raise BackupError("Нет копии для проверки.")
            blob = await asyncio.to_thread(backend.read_bytes, name)
            sha = hashlib.sha256(blob).hexdigest()
            side = (await asyncio.to_thread(backend.read_bytes, f"{name}.sha256")).decode("utf-8", "replace").split()[:1]
            if not side or side[0] != sha:
                raise BackupError("Контрольная сумма файла не совпадает с записанной: копия повреждена.")
            _, payload = await asyncio.to_thread(container.open_, blob, key)
            importer.validate_payload(payload)
            await self._end(run, "ok", "Копия цела: сумма совпадает, архив расшифровывается и проходит проверку структуры", file_name=name, size_bytes=len(blob), sha256=sha)
            await self._audit("backup.verify", run, actor_id, True)
        except Exception as exc:  # noqa: BLE001
            known = (BackupError, StorageError, container.ContainerError, importer.ImportError_)
            msg = getattr(exc, "message", None) or str(exc) if isinstance(exc, known) else f"Непредвиденная ошибка ({type(exc).__name__})"
            if not isinstance(exc, known):
                log.exception("Проверка копии не выполнена")
            await self._end(run, "failed", msg, file_name=name if name and NAME_RE.match(name) else None)
            await self._audit("backup.verify", run, actor_id, False, error=str(msg)[:200])
        return run

    async def fetch(self, name: str, actor: str, actor_id, ip: str | None) -> bytes:
        """Скачать копию из хранилища (администратор); событие записывается в аудит с IP."""
        if not NAME_RE.match(name):
            raise BackupError("Недопустимое имя файла.")
        s = self._app.state
        async with s.session_maker() as db:
            backend = await self._target(db, await s.settings_svc.get(db, "auto_backup"))
        try:
            blob = await asyncio.to_thread(backend.read_bytes, name)
        except StorageError as exc:
            await audit_now(self._app, actor_user_id=actor_id, actor_name=actor, action="backup.download", target_type="config_backup", target_id=name, ip=ip,
                            result="failure", details={"error": str(exc)[:200]})
            raise BackupError(str(exc)) from None
        await audit_now(self._app, actor_user_id=actor_id, actor_name=actor, action="backup.download", target_type="config_backup", target_id=name, ip=ip,
                        details={"size_bytes": len(blob)})
        return blob

    async def files(self) -> list[dict]:
        s = self._app.state
        async with s.session_maker() as db:
            backend = await self._target(db, await s.settings_svc.get(db, "auto_backup"))

        def run() -> list[dict]:
            out = []
            for n in sorted((n for n in backend.list_dir("") if NAME_RE.match(n)), reverse=True):
                try:
                    out.append({"name": n, "size_bytes": backend.size_of(n)})
                except StorageError:
                    out.append({"name": n, "size_bytes": None})
            return out
        return await asyncio.to_thread(run)

    # --------------------------------------------------------------------------------------- состояние
    async def status(self, db) -> dict:
        cfg = await self._app.state.settings_svc.get(db, "auto_backup")
        runs = (await db.execute(select(ConfigBackupRun).order_by(ConfigBackupRun.started_at.desc()).limit(30))).scalars().all()
        last_ok = next((r for r in runs if r.kind == "backup" and r.status == "ok"), None)
        if last_ok is None:
            last_ok = (await db.execute(select(ConfigBackupRun).where(ConfigBackupRun.kind == "backup", ConfigBackupRun.status == "ok")
                                        .order_by(ConfigBackupRun.started_at.desc()).limit(1))).scalar_one_or_none()
        stale = bool(cfg.enabled) and (last_ok is None or utcnow() - last_ok.finished_at.astimezone(timezone.utc) > timedelta(hours=cfg.stale_hours))
        pub = cfg.model_dump()
        pub["archive_key_set"] = bool(pub.pop("archive_key", ""))
        return {"settings": pub, "stale": stale, "last_ok": run_out(last_ok) if last_ok else None, "runs": [run_out(r) for r in runs]}

    async def tick(self) -> None:
        s = self._app.state
        async with s.session_maker() as db:
            cfg = await s.settings_svc.get(db, "auto_backup")
            if not cfg.enabled or not cfg.profile_id:
                return
            tz = await self._tz(db)
            last = (await db.execute(select(ConfigBackupRun).where(ConfigBackupRun.kind == "backup", ConfigBackupRun.trigger == "auto")
                                     .order_by(ConfigBackupRun.started_at.desc()).limit(1))).scalar_one_or_none()
        if is_due(datetime.now(tz), cfg.time, cfg.every_days, last, tz):
            try:
                await self.create("auto")
            except BackupError:
                pass                                                    # уже идёт другое копирование


def run_out(r: ConfigBackupRun) -> dict:
    return {"id": str(r.id), "kind": r.kind, "trigger": r.trigger, "actor": r.actor, "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at,
            "file_name": r.file_name, "size_bytes": r.size_bytes, "sha256": r.sha256, "message": r.message}


async def run_auto_backup(svc: AutoBackup, interval: float = 60.0) -> None:
    while True:
        try:
            await svc.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка шага автокопирования настроек")
        await asyncio.sleep(interval)
