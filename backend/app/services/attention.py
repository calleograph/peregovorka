"""Центр внимания администратора: единый список состояний, которые требуют действия, для страницы «Обзор».

Каждый модуль регистрирует источник — `@source("имя")` над функцией `(app, db) -> list[dict]`, — и его состояния попадают в общий список без правок
«Обзора». Пункт (`item(...)`) отвечает на вопросы администратора: что произошло, что затронуто, как это влияет на работу, что сделать, — и ведёт прямо
к нужной настройке (`tab` — раздел администрирования, `anchor` — поле или блок в нём). Где исправление безопасно и уже есть в системе, пункт несёт
его описание (`fix`): интерфейс показывает, что будет сделано, и просит подтверждения.

Источник опрашивается с ограничением по времени; сбой или превышение — тоже пункт («проверка не выполнена»), а не пустой список: молчание не должно
выглядеть как «всё исправно». Результат кэшируется на короткое время, «Проверить сейчас» и возврат после исправления опрашивают заново.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

LEVELS = ("critical", "warning", "info")             # критическая проблема · предупреждение · к сведению
CACHE_S = 30
SOURCE_TIMEOUT_S = 8.0

Source = Callable[[object, object], Awaitable[list[dict]]]
_SOURCES: list[tuple[str, Source]] = []
_cache: dict = {"at": 0.0, "data": None}


def source(name: str) -> Callable[[Source], Source]:
    """Регистрация источника состояний модуля (имя — для журнала и пункта «проверка не выполнена»)."""
    def deco(fn: Source) -> Source:
        _SOURCES.append((name, fn))
        return fn
    return deco


def item(id: str, level: str, component: str, title: str, impact: str, action: str, tab: str, *, anchor: str | None = None,  # noqa: A002
         fix: dict | None = None, doc: str | None = None) -> dict:
    assert level in LEVELS, level
    return {"id": id, "level": level, "component": component, "title": title, "impact": impact, "action": action, "tab": tab, "anchor": anchor,
            "fix": fix, "doc": doc}


async def collect(app, session_maker, *, refresh: bool = False) -> dict:
    """Все состояния, требующие внимания, по убыванию важности. Каждый источник — в своей сессии БД и со своим пределом времени."""
    now = time.monotonic()
    if not refresh and _cache["data"] is not None and now - _cache["at"] < CACHE_S:
        return _cache["data"]
    from . import attention_sources  # noqa: F401,PLC0415 — регистрация источников основных модулей

    async def one(name: str, fn: Source) -> list[dict]:
        try:
            async with session_maker() as db:
                return await asyncio.wait_for(fn(app, db), SOURCE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            log.warning("Источник состояний не ответил", extra={"source": name, "error": type(exc).__name__})
            return [item(f"source.{name}", "info", name, f"Проверка «{name}» не выполнена ({type(exc).__name__})",
                         "Состояние этой части системы сейчас неизвестно.", "Повторите проверку; если повторяется — журнал событий.", "journal")]

    parts = await asyncio.gather(*(one(n, f) for n, f in _SOURCES))
    items = sorted((x for p in parts for x in p), key=lambda x: LEVELS.index(x["level"]))
    data = {"items": items, "counts": {lv: sum(1 for x in items if x["level"] == lv) for lv in LEVELS}, "checked_at": int(time.time())}
    _cache.update(at=now, data=data)
    return data

