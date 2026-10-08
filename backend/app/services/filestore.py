"""Единый слой файлового хранения.

*Профиль хранилища* (`storage_profiles`) создаёт администратор один раз: «Файловый сервер №1 → SMB → \\\\server\\share → учётная запись».
Функции (записи аудио, протоколы и материалы встречи, вложения чата, журнал) только выбирают профиль (`profile_id` в своей группе настроек) —
адрес и пароль второй раз не вводятся. Внутри профиля подпапки создаются сами:

    Audio/        записи аудио
    Transcripts/  стенограммы
    Protocols/    протокол и резюме (LLM)
    Chat/         переписка встречи и вложения чата
    Boards/       схемы доски (.drawio)
    Logs/         журнал

Все потребители работают через интерфейс `StorageBackend` (services/storage.py: локальный диск и SMB); ни чат, ни записи, ни протоколы не
реализуют SMB сами. Старые настройки (адрес прямо в группе, без профиля) продолжают работать как раньше — плоская раскладка без подпапок.
Если хранилище недоступно, `StorageError` поднимается вызывающему: файл не пропадает молча (вызывающий пишет ошибку в журнал).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import StorageProfile, utcnow
from .settings import SettingsError, SettingsService, _StorageTarget, _first_error
from .storage import LocalStorage, SmbStorage, StorageBackend, StorageError, build_storage

log = logging.getLogger("app.filestore")

AUDIO, TRANSCRIPTS, PROTOCOLS, CHAT, BOARDS, LOGS = "Audio", "Transcripts", "Protocols", "Chat", "Boards", "Logs"
FOLDERS = (AUDIO, TRANSCRIPTS, PROTOCOLS, CHAT, BOARDS, LOGS)
# какая группа настроек выбирает профиль
FUNCTION_GROUPS = {"audio_storage": "Записи аудио", "storage": "Протоколы и материалы встречи", "chat_files": "Вложения чата", "journal": "Журнал"}
_CONFIG_KEYS = ("local_path", "smb_server", "smb_share", "smb_base_path", "smb_username", "smb_domain")


class PrefixedStorage:
    """Подпапка внутри хранилища: все пути потребителя считаются относительно неё."""

    def __init__(self, base: StorageBackend, prefix: str):
        self._base, self._prefix = base, prefix.strip("/")

    def _p(self, rel: str) -> str:
        return f"{self._prefix}/{rel}" if self._prefix else rel

    def exists(self, rel: str) -> bool:
        return self._base.exists(self._p(rel))

    def write_bytes(self, rel: str, data: bytes) -> str:
        return self._base.write_bytes(self._p(rel), data)

    def read_bytes(self, rel: str) -> bytes:
        return self._base.read_bytes(self._p(rel))

    def delete(self, rel: str) -> None:
        self._base.delete(self._p(rel))

    def list_dir(self, rel: str) -> list[str]:
        return self._base.list_dir(self._p(rel)) if (rel or self._prefix) else []

    def delete_dir(self, rel: str) -> None:
        self._base.delete_dir(self._p(rel))

    def test(self) -> str:
        return self._base.test()

    def probe(self) -> None:
        self._base.probe()


class FileStore:
    def __init__(self, svc: SettingsService, data_dir: str):
        self._svc = svc
        self._data_dir = data_dir

    # ------------------------------------------------------------------ секрет и сборка
    def _aad(self, pid: uuid.UUID | str) -> bytes:
        return f"storage_profile.{pid}".encode()

    def _encrypt(self, pid: uuid.UUID, secret: str) -> str:
        if not secret:
            return ""
        if self._svc.box is None:
            raise SettingsError("APP_MASTER_KEY не задан — пароль сохранить нельзя")
        return self._svc.box.encrypt(secret, aad=self._aad(pid))

    def _decrypt(self, row: StorageProfile) -> str:
        if not row.secret_enc or self._svc.box is None:
            return ""
        try:
            return self._svc.box.decrypt(row.secret_enc, aad=self._aad(row.id))
        except Exception:  # noqa: BLE001 — повреждённый секрет не должен ронять остальные функции
            return ""

    def _target(self, kind: str, config: dict, secret: str) -> _StorageTarget:
        if kind not in ("local", "smb"):
            raise SettingsError("Тип хранилища: local или smb")
        data = {k: str(v) for k, v in (config or {}).items() if k in _CONFIG_KEYS and v is not None}
        try:
            cfg = _StorageTarget(enabled=True, mode=kind, smb_password=secret, **data)  # type: ignore[arg-type]
        except ValueError as exc:
            raise SettingsError(_first_error(exc)) from None
        if kind == "smb" and not (cfg.smb_server and cfg.smb_share):
            raise SettingsError("Для SMB укажите сервер и общий ресурс")
        return cfg

    def build(self, row: StorageProfile) -> StorageBackend:
        cfg = self._target(row.kind, row.config or {}, self._decrypt(row))
        backend = build_storage(cfg, self._data_dir)
        assert backend is not None
        return backend

    def _public(self, row: StorageProfile, usage: list[str]) -> dict:
        cfg = {k: v for k, v in (row.config or {}).items() if k in _CONFIG_KEYS}
        return {"id": str(row.id), "name": row.name, "kind": row.kind, "config": cfg, "secret_set": bool(row.secret_enc), "used_by": usage,
                "address": self.address(row.kind, cfg)}

    @staticmethod
    def address(kind: str, cfg: dict) -> str:
        if kind == "local":
            return str(cfg.get("local_path", ""))
        base = "\\".join(p for p in str(cfg.get("smb_base_path", "")).replace("/", "\\").split("\\") if p)
        return "\\\\" + str(cfg.get("smb_server", "")) + "\\" + str(cfg.get("smb_share", "")) + ("\\" + base if base else "")

    # ------------------------------------------------------------------------ чтение
    async def _usage(self, db: AsyncSession) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for group, title in FUNCTION_GROUPS.items():
            try:
                pid = getattr(await self._svc.get(db, group), "profile_id", "")
            except SettingsError:
                continue
            if pid:
                out.setdefault(pid, []).append(title)
        return out

    async def list(self, db: AsyncSession) -> list[dict]:
        usage = await self._usage(db)
        rows = (await db.execute(select(StorageProfile).order_by(StorageProfile.name))).scalars().all()
        return [self._public(r, usage.get(str(r.id), [])) for r in rows]

    async def row(self, db: AsyncSession, profile_id: str) -> StorageProfile:
        try:
            row = await db.get(StorageProfile, uuid.UUID(str(profile_id)))
        except ValueError:
            row = None
        if row is None:
            raise SettingsError("Хранилище не найдено")
        return row

    # ------------------------------------------------------------------------- запись
    async def create(self, db: AsyncSession, name: str, kind: str, config: dict, secret: str) -> dict:
        name = (name or "").strip()
        if not name or len(name) > 120:
            raise SettingsError("Название хранилища: 1–120 символов")
        if (await db.execute(select(StorageProfile).where(StorageProfile.name == name))).scalars().first():
            raise SettingsError("Хранилище с таким названием уже есть")
        self._check(kind, config, secret)
        pid = uuid.uuid4()
        row = StorageProfile(id=pid, name=name, kind=kind, config={k: v for k, v in config.items() if k in _CONFIG_KEYS},
                             secret_enc=self._encrypt(pid, secret))
        db.add(row)
        await db.commit()
        return self._public(row, [])

    def _check(self, kind: str, config: dict, secret: str) -> None:
        cfg = self._target(kind, config, secret)
        build_storage(cfg, self._data_dir)  # проверяет, что локальный каталог внутри смонтированного тома

    async def update(self, db: AsyncSession, profile_id: str, patch: dict) -> dict:
        row = await self.row(db, profile_id)
        name = patch.get("name")
        if name is not None:
            name = name.strip()
            if not name or len(name) > 120:
                raise SettingsError("Название хранилища: 1–120 символов")
            dup = (await db.execute(select(StorageProfile).where(StorageProfile.name == name, StorageProfile.id != row.id))).scalars().first()
            if dup:
                raise SettingsError("Хранилище с таким названием уже есть")
        cfg = dict(row.config or {})
        for k, v in (patch.get("config") or {}).items():
            if k in _CONFIG_KEYS:
                cfg[k] = v
        secret = self._decrypt(row)
        if patch.get("secret") is not None:    # None = не менять, "" = очистить
            secret = str(patch["secret"])
        self._check(row.kind, cfg, secret)
        if name is not None:
            row.name = name
        row.config = cfg
        if patch.get("secret") is not None:
            row.secret_enc = self._encrypt(row.id, secret)
        row.updated_at = utcnow()
        await db.commit()
        return self._public(row, (await self._usage(db)).get(str(row.id), []))

    async def delete(self, db: AsyncSession, profile_id: str) -> None:
        row = await self.row(db, profile_id)
        used = (await self._usage(db)).get(str(row.id), [])
        if used:
            raise SettingsError("Хранилище используется: " + ", ".join(used) + ". Сначала выберите для этих функций другое.")
        from ..models import ChatAttachment  # noqa: PLC0415

        if (await db.execute(select(ChatAttachment.id).where(ChatAttachment.profile_id == row.id).limit(1))).first():
            raise SettingsError("В этом хранилище лежат вложения чата существующих встреч — удалить его нельзя, пока они не удалены.")
        await db.delete(row)
        await db.commit()

    async def validate_choice(self, db: AsyncSession, patch: dict) -> None:
        """Выбранный функцией профиль должен существовать."""
        pid = patch.get("profile_id")
        if pid:
            await self.row(db, str(pid))

    # ---------------------------------------------------------------------- использование
    async def backend(self, db: AsyncSession, group: str, folder: str, *, legacy_prefix: str = "") -> StorageBackend | None:
        """Хранилище функции или None, если выгрузка выключена. С профилем — подпапка `folder`; без профиля (старые настройки) — как раньше.
        Недоступность/ошибка настройки — `StorageError`."""
        cfg = await self._svc.get(db, group)
        if not getattr(cfg, "enabled", False):
            return None
        pid = getattr(cfg, "profile_id", "")
        if pid:
            try:
                row = await self.row(db, pid)
                base = await asyncio.to_thread(self.build, row)
            except SettingsError as exc:
                raise StorageError(f"Хранилище недоступно: {exc}") from None
            return PrefixedStorage(base, folder)
        legacy = build_storage(cfg, self._data_dir)  # type: ignore[arg-type]
        if legacy is None:
            return None
        return PrefixedStorage(legacy, legacy_prefix) if legacy_prefix else legacy

    async def chat_backend(self, db: AsyncSession) -> tuple[StorageBackend, uuid.UUID | None]:
        """Где лежат вложения чата: выбранный профиль (подпапка Chat/files) или локальный диск приложения. Возвращает и id профиля."""
        cfg = await self._svc.get(db, "chat_files")
        pid = getattr(cfg, "profile_id", "")
        if pid:
            try:
                row = await self.row(db, pid)
                base = await asyncio.to_thread(self.build, row)
            except SettingsError as exc:
                raise StorageError(f"Хранилище вложений недоступно: {exc}") from None
            return PrefixedStorage(base, f"{CHAT}/files"), row.id
        return LocalStorage(str(Path(self._data_dir) / "chat-files")), None

    async def chat_backend_for(self, db: AsyncSession, profile_id: uuid.UUID | None) -> StorageBackend:
        """Хранилище, в котором лежит уже загруженное вложение (профиль мог смениться после загрузки)."""
        if profile_id is None:
            return LocalStorage(str(Path(self._data_dir) / "chat-files"))
        try:
            base = await asyncio.to_thread(self.build, await self.row(db, str(profile_id)))
        except SettingsError as exc:
            raise StorageError(f"Хранилище вложений недоступно: {exc}") from None
        return PrefixedStorage(base, f"{CHAT}/files")

    # ------------------------------------------------------------------------- проверка
    async def test_profile(self, db: AsyncSession, profile_id: str) -> str:
        """Пробная запись и создание подпапок (Audio/, Protocols/ …) — так права и пути проверяются сразу, а не при первой выгрузке."""
        row = await self.row(db, profile_id)
        backend = await asyncio.to_thread(self.build, row)

        def run() -> str:
            for folder in FOLDERS:
                probe = f"{folder}/.peregovorka-write-test-{uuid.uuid4().hex[:8]}"
                backend.write_bytes(probe, b"ok")
                backend.delete(probe)
            return f"Запись возможна, подпапки созданы: {', '.join(FOLDERS)}"

        return await asyncio.to_thread(run)
