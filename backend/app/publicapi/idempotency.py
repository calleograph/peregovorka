"""`Idempotency-Key` для операций создания: повтор того же запроса (сеть оборвалась, клиент повторил) возвращает прежний ответ и не создаёт дубль.

Правила
* ключ — на интеграцию (чужой ключ не пересекается): 1–64 символа `A-Za-z0-9_.:-`;
* тот же ключ и тот же запрос → прежний ответ с заголовком `Idempotent-Replay: true`;
* тот же ключ, но другой запрос → `409 idempotency_conflict`;
* первый запрос ещё выполняется → `409 idempotency_in_progress` + `Retry-After: 1` (дубль не запускается, пока идёт оригинал);
* сохраняются только успешные ответы (2xx): после ошибки запрос с тем же ключом можно повторить;
* записи живут `idempotency_ttl_hours` (по умолчанию 24 часа).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ApiIdempotency, utcnow
from .errors import ApiError

KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
STALE_S = 120            # «выполняется» дольше этого — оригинал, скорее всего, оборвался (перезапуск): ключ освобождается


def request_hash(method: str, path: str, body: object) -> str:
    return hashlib.sha256(json.dumps([method, path, body], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


async def run(request: Request, db: AsyncSession, client_id, body: object, action: Callable[[], Awaitable[tuple[int, dict, dict]]]) -> JSONResponse:
    """`action` возвращает (код, тело, заголовки). Без заголовка `Idempotency-Key` просто выполняется."""
    key = request.headers.get("idempotency-key")
    if key is None:
        status, payload, headers = await action()
        return JSONResponse(payload, status_code=status, headers=headers)
    if not KEY_RE.match(key):
        raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key: 1–64 символа из набора A-Z a-z 0-9 _ . : -")
    h = request_hash(request.method, request.url.path, body)
    row = ApiIdempotency(client_id=client_id, key=key, request_hash=h, state="in_progress")
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        old = (await db.execute(select(ApiIdempotency).where(ApiIdempotency.client_id == client_id, ApiIdempotency.key == key))).scalars().first()
        if old is None:                                    # запись успели удалить — просто повторим попытку один раз
            return await run(request, db, client_id, body, action)
        if old.request_hash != h:
            raise ApiError(409, "idempotency_conflict", "Этот Idempotency-Key уже использован для другого запроса.")
        if old.state == "done" and old.response_body is not None:
            stored = old.response_body
            return JSONResponse(stored["body"], status_code=old.response_status or 200, headers={**stored.get("headers", {}), "Idempotent-Replay": "true"})
        age = (utcnow() - (old.created_at if old.created_at.tzinfo else old.created_at.replace(tzinfo=utcnow().tzinfo))).total_seconds()
        if age > STALE_S:
            await db.execute(delete(ApiIdempotency).where(ApiIdempotency.id == old.id))
            await db.commit()
            return await run(request, db, client_id, body, action)
        raise ApiError(409, "idempotency_in_progress", "Запрос с этим Idempotency-Key ещё выполняется. Повторите через секунду.", headers={"Retry-After": "1"})
    row_id = row.id                                         # до возможного rollback: после него атрибуты объекта устаревают
    try:
        status, payload, headers = await action()
    except BaseException:
        await db.rollback()
        await db.execute(delete(ApiIdempotency).where(ApiIdempotency.id == row_id))     # ошибка не фиксируется: повтор с тем же ключом допустим
        await db.commit()
        raise
    if 200 <= status < 300:
        r2 = await db.get(ApiIdempotency, row_id)
        if r2 is not None:
            r2.state, r2.response_status, r2.response_body = "done", status, {"body": payload, "headers": headers}
            await db.commit()
    else:
        await db.execute(delete(ApiIdempotency).where(ApiIdempotency.id == row_id))
        await db.commit()
    return JSONResponse(payload, status_code=status, headers=headers)


async def prune(session_maker, ttl_hours: int) -> int:
    async with session_maker() as db:
        res = await db.execute(delete(ApiIdempotency).where(ApiIdempotency.created_at < utcnow() - timedelta(hours=ttl_hours)))
        await db.commit()
        return res.rowcount or 0
