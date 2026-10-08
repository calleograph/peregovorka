"""Безопасное описание исключения для журнала и диагностики: класс ошибки и, для ошибок файловой системы, ошибка ОС и путь.

Сообщения произвольных исключений (SQL с параметрами, ответы сторонних библиотек) в описание НЕ попадают — в них могут быть секреты.
Пример: `PermissionError: [Errno 13] Permission denied: '/data/ca/bundle.pem.tmp'`.
"""
from __future__ import annotations


def describe_error(exc: BaseException) -> str:
    name = type(exc).__name__
    if isinstance(exc, OSError):
        parts = [name]
        if exc.errno is not None:
            parts.append(f"[Errno {exc.errno}]")
        if exc.strerror:
            parts.append(str(exc.strerror))
        text = " ".join(parts)
        if exc.filename:
            text += f": {exc.filename!s}"
        return text[:300]
    return name
