"""Bitrix24 как источник профилей: проверка соединения, пробный запрос по e-mail, ручная синхронизация. Секреты не возвращаются."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..services.audit import write_audit

router = APIRouter(prefix="/admin/bitrix24", tags=["admin-bitrix24"])


@router.post("/check")
async def check(request: Request, su: SessionUser = Depends(require_admin)):
    ok, msg, ms = await request.app.state.enrichment.check()
    return {"ok": ok, "message": msg, "ms": ms}


@router.post("/lookup")
async def lookup(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin)):
    email = str(body.get("email") or "").strip()
    if not email or len(email) > 320 or "@" not in email:
        raise HTTPException(status_code=422, detail="Укажите e-mail сотрудника")
    return await request.app.state.enrichment.preview(email)


@router.post("/sync")
async def sync(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Обновить профили пользователей из Bitrix24 сейчас (до 200 человек за запуск, самых недавних по входу)."""
    res = await request.app.state.enrichment.sync_all(limit=200, force=True)
    if res.get("status") == "disabled":
        raise HTTPException(status_code=409, detail="Интеграция с Bitrix24 выключена")
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="bitrix24.sync", target_type="integration", target_id="bitrix24",
                      ip=client_ip(request), details=res)
    await db.commit()
    return res
