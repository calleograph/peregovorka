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
from typing import Iterator, Protocol

from .settings import _StorageTarget as StorageSettings

log = logging.getLogger("app.storage")

CHUNK = 1 << 20          # размер куска при потоковом чтении/записи файлов
WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class StorageError(Exception):
    pass


class StorageNotFound(StorageError):
    """Файла нет в хранилище (а само хранилище отвечает) — в отличие от «хранилище недоступно»."""


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
    def read_bytes(self, rel: str) -> bytes: ...
    def delete(self, rel: str) -> None: ...
    def list_dir(self, rel: str) -> list[str]: ...
    def delete_dir(self, rel: str) -> None: ...
    def test(self) -> str: ...
    def probe(self) -> None: ...   # лёгкая проверка доступности хранилища (без записи); StorageError — недоступно
    # Потоковые операции для больших файлов (записи встреч): файл целиком в память не читается и не собирается
    def size_of(self, rel: str) -> int: ...
    def read_range(self, rel: str, start: int, end: int) -> Iterator[bytes]: ...      # байты start…end включительно, кусками
    def copy_in(self, rel: str, src_path: str) -> str: ...                              # локальный файл → хранилище (через временное имя, затем переименование)
    def copy_out(self, rel: str, dst_path: str) -> None: ...                            # хранилище → локальный файл
    def volume(self) -> tuple[int, int]: ...                                            # (всего, свободно) байт тома хранилища


def _check_rel(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise StorageError("Недопустимый путь")
    # На Linux обратная косая — обычный символ имени, а на SMB-сервере (Windows) — разделитель: компонент «..\..\x» вышел бы из каталога хранилища.
    # Двоеточие — альтернативные потоки NTFS. Все наши компоненты проходят safe_component (он эти символы заменяет), так что нормальные пути не затрагиваются.
    if any(("\\" in part or ":" in part or "\x00" in part) for part in p.parts):
        raise StorageError("Недопустимый путь")
    return p


MARKER = ".peregovorka-volume"


class LocalStorage:
    """Локальный каталог. Если задана метка тома (`marker`), запись и проверка доступности разрешены, только когда в каталоге лежит файл-метка с тем же значением:
    так отключившаяся сетевая папка (пустая точка монтирования на системном диске) не принимается за хранилище и не заполняется файлами."""

    def __init__(self, root: str, marker: str | None = None):
        self._root = Path(root)
        self._marker = marker

    def _require_marker(self) -> None:
        if not self._marker:
            return
        try:
            ok = (self._root / MARKER).read_text(encoding="utf-8").strip() == self._marker
        except OSError:
            ok = False
        if not ok:
            raise StorageError(f"Том хранилища {self._root} не подключён: в каталоге нет метки тома (сетевая папка не смонтирована?). "
                               "Запись остановлена, чтобы не заполнять локальный диск; после подключения тома нажмите «Проверить» у хранилища.")

    def mark_volume(self) -> str:
        """Поставить метку тома (при первой проверке хранилища) и вернуть её значение; уже стоящая метка не меняется."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            p = self._root / MARKER
            if p.is_file() and p.read_text(encoding="utf-8").strip():
                return p.read_text(encoding="utf-8").strip()
            value = uuid.uuid4().hex
            p.write_text(value, encoding="utf-8")
            return value
        except OSError as exc:
            raise StorageError(f"Не удалось поставить метку тома в {self._root}: {exc.strerror or exc}") from None

    def _full(self, rel: str) -> Path:
        full = (self._root / _check_rel(rel)).resolve()
        if self._root.resolve() not in full.parents:
            raise StorageError("Путь выходит за пределы хранилища")
        return full

    def exists(self, rel: str) -> bool:
        return self._full(rel).exists()

    def write_bytes(self, rel: str, data: bytes) -> str:
        full = self._full(rel)
        self._require_marker()
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            tmp = full.with_name(full.name + f".{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, full)
        except OSError as exc:
            raise StorageError(f"Не удалось записать в {self._root}: {exc.strerror or exc}") from None
        return str(full)

    def read_bytes(self, rel: str) -> bytes:
        try:
            return self._full(rel).read_bytes()
        except FileNotFoundError:
            raise StorageNotFound("Файл не найден в хранилище") from None
        except OSError as exc:
            raise StorageError(f"Не удалось прочитать файл: {exc.strerror or exc}") from None

    def probe(self) -> None:
        if not self._root.is_dir():
            raise StorageError(f"Каталог хранилища {self._root} не найден (том не подключён?)")
        self._require_marker()

    def size_of(self, rel: str) -> int:
        try:
            return self._full(rel).stat().st_size
        except FileNotFoundError:
            raise StorageNotFound("Файл не найден в хранилище") from None
        except OSError as exc:
            raise StorageError(f"Не удалось прочитать файл: {exc.strerror or exc}") from None

    def read_range(self, rel: str, start: int, end: int) -> Iterator[bytes]:
        full = self._full(rel)
        try:
            with open(full, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(CHUNK, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                    yield chunk
        except FileNotFoundError:
            raise StorageNotFound("Файл не найден в хранилище") from None
        except OSError as exc:
            raise StorageError(f"Не удалось прочитать файл: {exc.strerror or exc}") from None

    def copy_in(self, rel: str, src_path: str) -> str:
        import shutil  # noqa: PLC0415

        full = self._full(rel)
        self._require_marker()
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            tmp = full.with_name(full.name + f".{uuid.uuid4().hex[:8]}.tmp")
            shutil.copyfile(src_path, tmp)
            os.replace(tmp, full)
        except OSError as exc:
            raise StorageError(f"Не удалось записать в {self._root}: {exc.strerror or exc}") from None
        return str(full)

    def copy_out(self, rel: str, dst_path: str) -> None:
        import shutil  # noqa: PLC0415

        try:
            shutil.copyfile(self._full(rel), dst_path)
        except FileNotFoundError:
            raise StorageNotFound("Файл не найден в хранилище") from None
        except OSError as exc:
            raise StorageError(f"Не удалось прочитать файл: {exc.strerror or exc}") from None

    def volume(self) -> tuple[int, int]:
        import shutil  # noqa: PLC0415

        try:
            u = shutil.disk_usage(self._root)
        except OSError as exc:
            raise StorageError(f"Не удалось узнать свободное место: {exc.strerror or exc}") from None
        return u.total, u.free

    def delete(self, rel: str) -> None:
        try:
            self._full(rel).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"Не удалось удалить файл: {exc.strerror or exc}") from None

    def list_dir(self, rel: str) -> list[str]:
        full = self._full(rel)
        try:
            return sorted(p.name for p in full.iterdir()) if full.is_dir() else []
        except OSError as exc:
            raise StorageError(f"Не удалось прочитать каталог: {exc.strerror or exc}") from None

    def delete_dir(self, rel: str) -> None:
        import shutil  # noqa: PLC0415

        try:
            shutil.rmtree(self._full(rel), ignore_errors=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StorageError(f"Не удалось удалить каталог: {exc.strerror or exc}") from None

    def test(self) -> str:
        probe = f".peregovorka-write-test-{uuid.uuid4().hex[:8]}"
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

    def read_bytes(self, rel: str) -> bytes:
        smb = self._session()
        try:
            with smb.open_file(self._unc(rel), mode="rb") as fh:
                return fh.read()
        except Exception as exc:  # noqa: BLE001
            text = f"{type(exc).__name__} {exc}".lower()
            if isinstance(exc, FileNotFoundError) or getattr(exc, "errno", None) == 2 or "object_name_not_found" in text or "object_path_not_found" in text or "no such file" in text:
                raise StorageNotFound("Файл не найден в хранилище") from None
            raise StorageError(f"SMB: не удалось прочитать файл ({type(exc).__name__})") from None

    def size_of(self, rel: str) -> int:
        smb = self._session()
        try:
            return int(smb.stat(self._unc(rel)).st_size)
        except Exception as exc:  # noqa: BLE001
            raise self._read_error(exc) from None

    @staticmethod
    def _read_error(exc: Exception) -> StorageError:
        text = f"{type(exc).__name__} {exc}".lower()
        if isinstance(exc, FileNotFoundError) or getattr(exc, "errno", None) == 2 or "object_name_not_found" in text or "object_path_not_found" in text or "no such file" in text:
            return StorageNotFound("Файл не найден в хранилище")
        return StorageError(f"SMB: не удалось прочитать файл ({type(exc).__name__})")

    def read_range(self, rel: str, start: int, end: int) -> Iterator[bytes]:
        smb = self._session()
        try:
            with smb.open_file(self._unc(rel), mode="rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(CHUNK, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            raise self._read_error(exc) from None

    def copy_in(self, rel: str, src_path: str) -> str:
        smb = self._session()
        path = self._unc(rel)
        tmp = path + f".{uuid.uuid4().hex[:8]}.tmp"
        try:
            smb.makedirs(path.rsplit("\\", 1)[0], exist_ok=True)
            with open(src_path, "rb") as src, smb.open_file(tmp, mode="wb") as dst:
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
            if smb.path.exists(path):
                smb.remove(path)
            smb.rename(tmp, path)
        except Exception as exc:  # noqa: BLE001
            try:
                if smb.path.exists(tmp):
                    smb.remove(tmp)
            except Exception:  # noqa: BLE001
                pass
            raise StorageError(f"SMB: не удалось записать файл ({type(exc).__name__})") from None
        return path

    def copy_out(self, rel: str, dst_path: str) -> None:
        smb = self._session()
        try:
            with smb.open_file(self._unc(rel), mode="rb") as src, open(dst_path, "wb") as dst:
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
        except Exception as exc:  # noqa: BLE001
            raise self._read_error(exc) from None

    def volume(self) -> tuple[int, int]:
        smb = self._session()
        try:
            v = smb.stat_volume("\\\\" + self._server + "\\" + self._share)
            return int(v.total_size), int(v.actual_available_size)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"SMB: не удалось узнать свободное место ({type(exc).__name__})") from None

    def probe(self) -> None:
        smb = self._session()
        try:
            if not smb.path.isdir("\\\\" + self._server + "\\" + self._share):
                raise StorageError(f"SMB: ресурс {self._server}\\{self._share} не найден")
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"SMB: ресурс недоступен ({type(exc).__name__})") from None

    def delete(self, rel: str) -> None:
        smb = self._session()
        try:
            if smb.path.exists(self._unc(rel)):
                smb.remove(self._unc(rel))
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"SMB: не удалось удалить файл ({type(exc).__name__})") from None

    def list_dir(self, rel: str) -> list[str]:
        smb = self._session()
        try:
            path = self._unc(rel)
            return sorted(smb.listdir(path)) if smb.path.isdir(path) else []
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"SMB: не удалось прочитать каталог ({type(exc).__name__})") from None

    def delete_dir(self, rel: str) -> None:
        smb = self._session()

        def rm(path: str) -> None:
            for name in smb.listdir(path):
                child = path + "\\" + name
                rm(child) if smb.path.isdir(child) else smb.remove(child)
            smb.rmdir(path)

        try:
            path = self._unc(rel)
            if smb.path.isdir(path):
                rm(path)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"SMB: не удалось удалить каталог ({type(exc).__name__})") from None

    def test(self) -> str:
        probe = f".peregovorka-write-test-{uuid.uuid4().hex[:8]}"
        self.write_bytes(probe, b"ok")
        try:
            self._session().remove(self._unc(probe))
        except Exception:  # noqa: BLE001
            pass
        return f"Запись на {self._unc()} возможна"


def build_storage(cfg: StorageSettings, allowed_root: str | None = None, marker: str | None = None) -> StorageBackend | None:
    """allowed_root — локальный каталог должен лежать внутри него (смонтированные тома контейнера, DATA_DIR)."""
    if not cfg.enabled:
        return None
    if cfg.mode == "local":
        if allowed_root is not None:
            root, want = Path(allowed_root).resolve(), Path(cfg.local_path).resolve()
            if want != root and root not in want.parents:
                raise StorageError(f"Каталог должен находиться внутри {allowed_root} (смонтированный том)")
        return LocalStorage(cfg.local_path, marker)
    return SmbStorage(cfg.smb_server, cfg.smb_share, cfg.smb_base_path, cfg.smb_username, cfg.smb_password, cfg.smb_domain)


async def unique_meeting_dir(storage: StorageBackend, base_rel: str) -> str:
    """Если в ту же минуту уже была встреча в этой комнате — добавляем « (2)», « (3)»…"""
    for n in range(1, 50):
        rel = base_rel if n == 1 else f"{base_rel} ({n})"
        if not await asyncio.to_thread(storage.exists, rel + "/protocol.txt"):
            return rel
    return f"{base_rel} ({uuid.uuid4().hex[:6]})"
