#!/usr/bin/env python3
"""Операции от root над путями, которые может менять приложение (uid 10001). Только стандартная библиотека, запуск: python3 -I scripts/safefs.py.

Проверка «это каталог, а не ссылка» и следующая за ней команда в оболочке разделены: взломанный backend успевает подменить путь между ними. Здесь путь открывается
по одному звену с O_NOFOLLOW, а действие выполняется над уже открытым объектом через /proc/self/fd: подмена после открытия ни на что не влияет.

    safefs.py fix БАЗА ОТНОСИТЕЛЬНЫЙ_ПУТЬ UID GID РЕЖИМ|- dir|file     владелец и режим объекта внутри БАЗА (сама БАЗА доверенная, её пишет только root)
    safefs.py bind ПУТЬ ТОЧКА                                          mount --bind каталога, если ни одно звено пути не ссылка

Коды завершения: 0 — готово, 3 — отказ (ссылка, не тот тип объекта), 1 — ошибка системы.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys

REFUSED = 3


class Refused(Exception):
    pass


def open_nofollow(base: str, rel: str) -> int:
    """Дескриптор O_PATH объекта base/rel. Любое звено внутри rel — ссылка или «..» — отказ."""
    parts = [p for p in rel.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise Refused(f"недопустимое звено в пути: {rel}")
    fd = os.open(base, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for i, part in enumerate(parts):
            nxt = os.open(part, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nxt
            mode = os.fstat(fd).st_mode
            if stat.S_ISLNK(mode):
                raise Refused(f"символьная ссылка: {'/'.join(parts[:i + 1])}")
            if i < len(parts) - 1 and not stat.S_ISDIR(mode):
                raise Refused(f"не каталог: {'/'.join(parts[:i + 1])}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def fix(base: str, rel: str, uid: int, gid: int, mode: str, kind: str) -> None:
    fd = open_nofollow(base, rel)
    try:
        st = os.fstat(fd)
        if kind == "dir" and not stat.S_ISDIR(st.st_mode):
            raise Refused(f"не каталог: {rel}")
        if kind == "file":
            if not stat.S_ISREG(st.st_mode):
                raise Refused(f"не обычный файл: {rel}")
            if st.st_nlink != 1:                    # жёсткая ссылка на чужой файл: сменить владельца значило бы отдать его приложению
                raise Refused(f"у файла несколько имён: {rel}")
        target = f"/proc/self/fd/{fd}"              # по этому пути ядро обращается к открытому объекту, а не к текущему содержимому каталога
        if (st.st_uid, st.st_gid) != (uid, gid):
            os.chown(target, uid, gid)
        if mode != "-" and stat.S_IMODE(st.st_mode) != int(mode, 8):
            os.chmod(target, int(mode, 8))
    finally:
        os.close(fd)


def bind(path: str, mountpoint: str) -> int:
    if not path.startswith("/"):
        raise Refused("нужен абсолютный путь")
    fd = open_nofollow("/", path)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise Refused(f"не каталог: {path}")
        # --no-canonicalize: иначе mount(8) сам разрешит /proc/self/fd/N в обычный путь, и окно подмены вернётся
        return subprocess.run(["mount", "--no-canonicalize", "--bind", f"/proc/self/fd/{fd}", mountpoint], pass_fds=[fd]).returncode
    finally:
        os.close(fd)


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 8 and argv[1] == "fix":
            fix(argv[2], argv[3], int(argv[4]), int(argv[5]), argv[6], argv[7])
            return 0
        if len(argv) == 4 and argv[1] == "bind":
            return bind(argv[2], argv[3])
    except Refused as exc:
        print(f"safefs: отказ: {exc}", file=sys.stderr)
        return REFUSED
    except OSError as exc:
        print(f"safefs: {exc}", file=sys.stderr)
        return REFUSED if exc.errno in (20, 40) else 1      # ENOTDIR, ELOOP: промежуточное звено оказалось ссылкой
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
