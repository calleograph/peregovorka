"""Аудит административных действий — отдельная таблица, не технический лог."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import request_id_var, scrub
from ..models import AuditLog


async def write_audit(db: AsyncSession, *, actor_user_id, actor_name: str, action: str, target_type: str,
                      target_id: str, details: dict | None = None, ip: str | None = None) -> None:
    rid = request_id_var.get()
    db.add(AuditLog(actor_user_id=actor_user_id, actor_name=actor_name, action=action, target_type=target_type,
                    target_id=str(target_id), details=scrub(details or {}), request_id=None if rid == "-" else rid, ip=ip))
