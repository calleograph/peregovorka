"""Хранилище протоколов: локальный каталог или сетевой ресурс SMB.

Для SMB используется pure-python клиент (smbprotocol): внутри контейнера ничего не монтируется
(CIFS требовал бы привилегий). Сервисная учётка с правом записи на ресурс задаётся в админке;
пароль хранится зашифрованным. Структура:

    <корень>/<комната>/<ГГГГ-ММ-ДД, день недели>/<ЧЧ-ММ начало>/protocol.txt
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from .settings import StorageSettings

log = logging.getLogger("app.storage")

WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class StorageError(Exception):
    pass


def safe_component(name: str, fallback: str = "room") -> str:
    """Имя каталога/файла без запрещённых символов и без «..». Пользовательский ввод в путь не попадает «как есть»."""
    cleaned = _BAD.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:80].strip(" .")
    return cleaned or fallback


def meeting_relpath(room_name: str, start_local: datetime) -> str:
    return "/".join([
        safe_component(room_name),
        f"{start_local:%Y-%m-%d}, {WEEKDAYS_RU[start_local.weekday()]}",
        f"{start_local:%H-%M}",
    ])


class StorageBackend(Protocol):
    def exists(self, rel: str) -> bool: ...
    def write_bytes(self, rel: str, data: bytes) -> str: ...
    def test(self) -> str: ...


def _check_rel(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise StorageError("Недопустимый путь")
    return p


class LocalStorage:
    def __init__(self, root: str):
        self._root = Path(root)

    def _full(self, rel: str) -> Path:
        full = (self._root / _check_rel(rel)).resolve()
        if self._root.resolve() not in full.parents:
            raise StorageError("Путь выходит за пределы хранилища")
        return full

    def exists(self, rel: str) -> bool:
        return self._full(rel).exists()

    def write_bytes(self, rel: str, data: bytes) -> str:
        full = self._full(rel)
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            tmp = full.with_name(full.name + f".{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, full)
        except OSError as exc:
            raise StorageError(f"Не удалось записать в {self._root}: {exc.strerror or exc}") from None
        return str(full)

    def test(self) -> str:
        probe = f".voicemeet-write-test-{uuid.uuid4().hex[:8]}"
        self.write_bytes(probe, b"ok")
        self._full(probe).unlink(missing_ok=True)
        return f"Запись в {self._root} возможна"


class SmbStorage:
    """Запись на SMB-ресурс от имени сервисной учётки (NTLM/Kerberos — как согласует сервер)."""

    def __init__(self, server: str, share: str, base_path: str, username: str, password: str, domain: str = ""):
        self._server, self._share = server, share
        self._base = [p for p in re.split(r"[\\/]", base_path) if p]
        self._user = f"{domain}\\{username}" if domain and username else username
        self._password = password

    def _unc(self, rel: str | None = None) -> str:
        parts = list(self._base) + (list(_check_rel(rel).parts) if rel else [])
        return "\\\\" + self._server + "\\" + self._share + ("\\" + "\\".join(parts) if parts else "")

    def _session(self):
        import smbclient  # noqa: PLC0415

        try:
            smbclient.register_session(self._server, username=self._user or None, password=self._password or None,
                                       connection_timeout=10, encrypt=False)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"SMB: не удалось подключиться/авторизоваться ({type(exc).__name__})") from None
        return smbclient

    def exists(self, rel: str) -> bool:
        smb = self._session()
        try:
            return smb.path.exists(self._unc(rel))
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"SMB: {type(exc).__name__}") from None

    def write_bytes(self, rel: str, data: bytes) -> str:
        smb = self._session()
        path = self._unc(rel)
        parent = path.rsplit("\\", 1)[0]
        try:
            smb.makedirs(parent, exist_ok=True)
            with smb.open_file(path, mode="wb") as fh:
                fh.write(data)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"SMB: не удалось записать файл ({type(exc).__name__})") from None
        return path

    def test(self) -> str:
        probe = f".voicemeet-write-test-{uuid.uuid4().hex[:8]}"
        self.write_bytes(probe, b"ok")
        try:
            self._session().remove(self._unc(probe))
        except Exception:  # noqa: BLE001
            pass
        return f"Запись на {self._unc()} возможна"


def build_storage(cfg: StorageSettings, allowed_root: str | None = None) -> StorageBackend | None:
    """allowed_root — локальный каталог должен лежать внутри него (смонтированные тома контейнера, DATA_DIR)."""
    if not cfg.enabled:
        return None
    if cfg.mode == "local":
        if allowed_root is not None:
            root, want = Path(allowed_root).resolve(), Path(cfg.local_path).resolve()
            if want != root and root not in want.parents:
                raise StorageError(f"Каталог должен находиться внутри {allowed_root} (смонтированный том)")
        return LocalStorage(cfg.local_path)
    return SmbStorage(cfg.smb_server, cfg.smb_share, cfg.smb_base_path, cfg.smb_username, cfg.smb_password, cfg.smb_domain)


async def unique_meeting_dir(storage: StorageBackend, base_rel: str) -> str:
    """Если в ту же минуту уже была встреча в этой комнате — добавляем « (2)», « (3)»…"""
    for n in range(1, 50):
        rel = base_rel if n == 1 else f"{base_rel} ({n})"
        if not await asyncio.to_thread(storage.exists, rel + "/protocol.txt"):
            return rel
    return f"{base_rel} ({uuid.uuid4().hex[:6]})"
