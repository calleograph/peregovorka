"""Аудит действий — отдельная таблица, не технический лог.

Запись содержит: время (UTC), тип события (`action`), исполнителя (идентификатор, имя, тип: user | guest | system | service), IP, User-Agent, сеанс
(короткий необратимый отпечаток идентификатора сеанса, не сам идентификатор), объект (тип и идентификатор), результат (success | denied | failure),
идентификатор запроса и подробности. Пароли, токены, ключи и содержимое документов в подробности не попадают (`scrub`).

Каждая запись подписывается HMAC-SHA256 ключом, производным от `APP_MASTER_KEY`: `verify` находит изменённые записи и пропуски в нумерации. Это обнаруживает
правку и удаление записей из середины журнала, но не защищает от того, кто владеет самим ключом и базой одновременно, и не видит удаление последних записей.
Каждая запись аудита дополнительно отражается в журнале событий (категория admin).
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging_setup import request_id_var, scrub
from ..models import AuditLog, utcnow

_sink: Callable[..., None] | None = None
_key: bytes | None = None
# сведения о текущем запросе (IP, User-Agent, отпечаток сеанса): их выставляет middleware, а write_audit подхватывает — вызовы не нужно менять
audit_ctx: contextvars.ContextVar[dict[str, str | None]] = contextvars.ContextVar("audit_ctx", default={})

RESULTS = ("success", "denied", "failure")
ACTOR_TYPES = ("user", "guest", "system", "service")


def set_journal_sink(fn: Callable[..., None] | None) -> None:
    """Приёмник зеркалирования в журнал событий (Journal.emit); задаётся при запуске приложения."""
    global _sink
    _sink = fn


def set_audit_key(master_key_b64: str | None) -> None:
    """Ключ подписи записей — производный от APP_MASTER_KEY (сам ключ шифрования настроек не используется напрямую)."""
    global _key
    try:
        raw = base64.b64decode(master_key_b64 or "", validate=True)
    except (ValueError, TypeError):
        raw = b""
    _key = hmac.new(raw, b"peregovorka-audit-v1", hashlib.sha256).digest() if raw else None


def session_fingerprint(session_id: str | None) -> str | None:
    """Отпечаток идентификатора сеанса: по нему связывают записи одного сеанса, а сам идентификатор восстановить нельзя."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:16] if session_id else None


def _canonical(f: dict[str, Any]) -> bytes:
    return json.dumps(f, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def _fields(row: AuditLog) -> dict[str, Any]:
    at: datetime = row.at
    return {"at": at.isoformat(), "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None, "actor_name": row.actor_name, "action": row.action,
            "target_type": row.target_type, "target_id": row.target_id, "details": row.details or {}, "request_id": row.request_id, "ip": row.ip,
            "actor_type": row.actor_type, "user_agent": row.user_agent, "session_id": row.session_id, "result": row.result}


def sign(row: AuditLog) -> str | None:
    return hmac.new(_key, _canonical(_fields(row)), hashlib.sha256).hexdigest() if _key else None


def build(*, actor_user_id, actor_name: str, action: str, target_type: str, target_id: str, details: dict | None = None, ip: str | None = None,
          result: str = "success", actor_type: str | None = None) -> AuditLog:
    ctx = audit_ctx.get()
    rid = request_id_var.get()
    row = AuditLog(at=utcnow(), actor_user_id=actor_user_id, actor_name=actor_name, action=action, target_type=target_type, target_id=str(target_id),
                   details=scrub(details or {}), request_id=None if rid == "-" else rid, ip=ip or ctx.get("ip"),
                   actor_type=actor_type or ("user" if actor_user_id else "system"), user_agent=ctx.get("user_agent"), session_id=ctx.get("session"),
                   result=result if result in RESULTS else "failure")
    row.sig = sign(row)
    return row


async def write_audit(db: AsyncSession, *, actor_user_id, actor_name: str, action: str, target_type: str, target_id: str, details: dict | None = None,
                      ip: str | None = None, result: str = "success", actor_type: str | None = None) -> None:
    row = build(actor_user_id=actor_user_id, actor_name=actor_name, action=action, target_type=target_type, target_id=target_id, details=details, ip=ip,
                result=result, actor_type=actor_type)
    db.add(row)
    if _sink is not None:
        _sink("admin", action, level="info" if row.result == "success" else "warn", user=actor_name, ip=row.ip,
              message=f"{target_type} {str(target_id)[:40]}", data=row.details or None)


async def audit_now(app, **kw) -> None:
    """Запись в отдельном сеансе БД с немедленной фиксацией: для отказов и ошибок, после которых запрос завершается исключением (общий сеанс откатился бы)."""
    try:
        async with app.state.session_maker() as db:
            await write_audit(db, **kw)
            await db.commit()
    except Exception:  # noqa: BLE001 — сбой журнала не должен ломать сам запрос
        import logging  # noqa: PLC0415

        logging.getLogger("app.audit").exception("Не удалось записать событие аудита", extra={"action": kw.get("action")})


async def verify(db: AsyncSession, *, batch: int = 2000, limit_report: int = 50) -> dict:
    """Проверка целостности: подпись каждой записи и непрерывность нумерации. Записи, созданные до включения подписи, считаются отдельно (`unsigned`)."""
    if _key is None:
        return {"signed": False, "checked": 0, "ok": 0, "unsigned": 0, "bad": [], "gaps": [], "note": "Ключ подписи недоступен (APP_MASTER_KEY не задан): проверка невозможна."}
    checked = ok = unsigned = 0
    bad: list[int] = []
    gaps: list[list[int]] = []
    last_id: int | None = None
    after = 0
    while True:
        rows = (await db.execute(select(AuditLog).where(AuditLog.id > after).order_by(AuditLog.id).limit(batch))).scalars().all()
        if not rows:
            break
        for r in rows:
            checked += 1
            if last_id is not None and r.id != last_id + 1 and len(gaps) < limit_report:
                gaps.append([last_id, r.id])
            last_id = r.id
            if r.sig is None:
                unsigned += 1
            elif hmac.compare_digest(r.sig, sign(r) or ""):
                ok += 1
            elif len(bad) < limit_report:
                bad.append(r.id)
        after = rows[-1].id
    return {"signed": True, "checked": checked, "ok": ok, "unsigned": unsigned, "bad": bad, "gaps": gaps,
            "intact": not bad and not gaps,
            "note": "Проверены подпись записей и непрерывность номеров. Удаление самых последних записей и подмена одновременно ключа и базы так не обнаруживаются."}
