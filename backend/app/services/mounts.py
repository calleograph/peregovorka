"""Определение фактического монтирования каталога (Linux, /proc/self/mountinfo).

Нужно, чтобы отключившаяся сетевая папка (на месте точки монтирования остаётся пустой каталог на системном диске) не принималась за хранилище: при первой проверке хранилища
запоминается, КАКАЯ файловая система смонтирована по этому пути (точка монтирования, тип, источник), а перед записью проверяется, что это всё ещё она. Если каталог лежит на корневой
файловой системе («/») — это системный диск. На системах без /proc (разработка на Windows) проверка недоступна: describe возвращает None, и остаётся только файл-метка.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass

# типы файловых систем, которые точно являются сетевыми/удалёнными
NETWORK_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "9p", "ceph", "glusterfs", "fuse.sshfs", "fuse.rclone", "davfs", "fuse.davfs2", "afs", "lustre"}
MOUNTINFO = "/proc/self/mountinfo"


@dataclass(frozen=True)
class Mount:
    mountpoint: str
    fstype: str
    source: str


def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_mountinfo(text: str) -> list[Mount]:
    out: list[Mount] = []
    for line in text.splitlines():
        parts = line.split()
        if "-" not in parts:
            continue
        i = parts.index("-")
        if i < 5 or len(parts) < i + 3:
            continue
        out.append(Mount(_unescape(parts[4]), parts[i + 1], _unescape(parts[i + 2])))
    return out


def read_mountinfo() -> str | None:
    try:
        with open(MOUNTINFO, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def describe(path: str, text: str | None = None) -> dict | None:
    """Файловая система, на которой лежит путь: {mountpoint, fstype, source, on_root}. None — определить нельзя (нет /proc)."""
    text = read_mountinfo() if text is None else text
    if not text:
        return None
    mounts = parse_mountinfo(text)
    if not mounts:
        return None
    real = os.path.realpath(path)
    best: Mount | None = None
    for m in mounts:
        mp = m.mountpoint.rstrip("/") or "/"
        if real == mp or real.startswith(mp.rstrip("/") + "/") or mp == "/":
            if best is None or len(m.mountpoint) >= len(best.mountpoint):
                best = m                                             # при нескольких монтированиях в одну точку побеждает последнее (верхнее)
    if best is None:
        return None
    return {"mountpoint": best.mountpoint, "fstype": best.fstype, "source": best.source, "on_root": best.mountpoint == "/"}


def same_mount(expected: dict, current: dict | None) -> bool:
    """Тот же ли том смонтирован: совпадают точка монтирования, тип и источник, и это не корневая файловая система."""
    if current is None:
        return True                                                  # определить нельзя (нет /proc) — полагаемся на метку
    want = expected.get("source")
    return (not current["on_root"] and current["mountpoint"] == expected.get("mountpoint") and current["fstype"] == expected.get("fstype")
            and (not want or current.get("source") == want))


def alive(path: str, timeout: float = 5.0) -> bool:
    """Отвечает ли файловая система. Обращение к недоступному NFS может зависнуть навсегда и заберёт поток приложения, поэтому проверяет отдельный процесс: не ответил вовремя — том недоступен,
    а зависший процесс остаётся ядру (ожидать его нельзя)."""
    try:
        p = subprocess.Popen(["stat", "-f", "-c", "%T", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return True                                                  # нет утилиты stat (разработка на Windows): остаётся проверка метки
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rc = p.poll()
        if rc is not None:
            return rc == 0
        time.sleep(0.02)
    p.kill()
    return False
