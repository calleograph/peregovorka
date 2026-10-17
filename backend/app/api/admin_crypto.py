"""«Администрирование → Безопасность → Шифрование хранилища»: состояние ключей, смена ключа, шифрование уже записанных данных."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..security import storage_crypto as sc
from ..services import storage_encryption as se
from ..services.audit import write_audit
from .config_backup import _reauth

router = APIRouter(prefix="/admin/crypto", tags=["admin"])
JOB_KEY = "crypto:job"


async def _job(request: Request) -> dict:
    raw = await request.app.state.redis.get(JOB_KEY)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


@router.get("")
async def status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await se.ensure_ready(request.app)
    return {"ready": se.ready(), "master_key_set": request.app.state.settings_svc.box is not None, "keys": await se.keys(db), "counts": await se.counts(db), "job": await _job(request),
            "algorithm": "AES-256-GCM"}


@router.post("/rotate")
async def rotate(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Новый активный ключ данных (подтверждение паролем администратора). Прежние ключи сохраняются, чтобы читать старые записи; «переписать» — отдельная операция."""
    await _reauth(request, db, su, str(body.get("password") or ""))
    try:
        kid = await se.rotate(db)
    except sc.StorageCryptoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="crypto.rotate", target_type="storage_key", target_id=str(kid), ip=client_ip(request))
    await db.commit()
    return {"active": kid}


@router.post("/reseal", status_code=202)
async def reseal(request: Request, body: dict = Body(default={}), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Зашифровать уже записанные чат и стенограммы: одной комнаты (`room_id`) или всех комнат с включённым режимом; `rotate_all` — переписать и зашифрованное под активным ключом."""
    if not await se.ensure_ready(request.app):
        raise HTTPException(status_code=409, detail="Ключ шифрования хранилища недоступен (проверьте APP_MASTER_KEY).")
    if (await _job(request)).get("state") == "running":
        raise HTTPException(status_code=409, detail="Задание шифрования уже выполняется.")
    rid = None
    if body.get("room_id"):
        try:
            rid = uuid.UUID(str(body["room_id"]))
        except ValueError:
            raise HTTPException(status_code=422, detail="Некорректный идентификатор комнаты") from None
    rotate_all = bool(body.get("rotate_all"))
    app = request.app
    await app.state.redis.set(JOB_KEY, json.dumps({"state": "running", "done": 0, "skipped": 0, "by": su.display_name}), ex=86400)

    async def work():
        async with app.state.session_maker() as s:
            async def progress(done, skipped):
                await app.state.redis.set(JOB_KEY, json.dumps({"state": "running", "done": done, "skipped": skipped, "by": su.display_name}), ex=86400)
            try:
                res = await se.reseal(s, room_id=rid, rotate_all=rotate_all, progress=progress)
                await app.state.redis.set(JOB_KEY, json.dumps({"state": "done", **res, "by": su.display_name}), ex=86400)
            except Exception as exc:  # noqa: BLE001
                await app.state.redis.set(JOB_KEY, json.dumps({"state": "failed", "error": type(exc).__name__, "by": su.display_name}), ex=86400)
    app.state.protocols.spawn(work(), "crypto-reseal")
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="crypto.reseal", target_type="room" if rid else "system", target_id=str(rid or "all"),
                      ip=client_ip(request), details={"rotate_all": rotate_all})
    await db.commit()
    return {"started": True}
