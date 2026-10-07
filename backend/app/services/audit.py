"""Аудит административных действий — отдельная таблица, не технический лог.

Каждая запись аудита дополнительно отражается в журнале событий (категория admin), чтобы весь ход работы системы был виден в одном месте.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import request_id_var, scrub
from ..models import AuditLog

_sink: Callable[..., None] | None = None


def set_journal_sink(fn: Callable[..., None] | None) -> None:
    """Приёмник зеркалирования в журнал событий (Journal.emit); задаётся при запуске приложения."""
    global _sink
    _sink = fn


async def write_audit(db: AsyncSession, *, actor_user_id, actor_name: str, action: str, target_type: str,
                      target_id: str, details: dict | None = None, ip: str | None = None) -> None:
    rid = request_id_var.get()
    clean = scrub(details or {})
    db.add(AuditLog(actor_user_id=actor_user_id, actor_name=actor_name, action=action, target_type=target_type,
                    target_id=str(target_id), details=clean, request_id=None if rid == "-" else rid, ip=ip))
    if _sink is not None:
        _sink("admin", action, level="info", user=actor_name, ip=ip, message=f"{target_type} {str(target_id)[:40]}", data=clean or None)
