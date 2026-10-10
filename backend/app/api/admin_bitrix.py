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


@router.post("/diagnose")
async def diagnose(request: Request, su: SessionUser = Depends(require_admin)):
    """Пошаговая диагностика: соединение, владелец webhook, выданные права (scope), чтение сотрудников и подразделений. Ничего не сохраняется."""
    return await request.app.state.enrichment.diagnose()


@router.post("/sync-user")
async def sync_one_user(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Проверка на одном человеке (логин или e-mail). Без `apply` — показать, что изменится; с `apply: true` — обновить карточку сейчас."""
    ident = str(body.get("user") or "").strip()
    if not ident or len(ident) > 320:
        raise HTTPException(status_code=422, detail="Укажите логин или e-mail пользователя")
    apply = body.get("apply") is True
    res = await request.app.state.enrichment.sync_user(ident, apply=apply)
    if apply:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="bitrix24.sync_user", target_type="integration", target_id="bitrix24",
                          ip=client_ip(request), details={"ok": res.get("ok"), "changed": sorted((res.get("changed") or {}))})
        await db.commit()
    return res


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
