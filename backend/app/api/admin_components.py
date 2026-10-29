"""Админка: дополнительные компоненты сервера — состояние и действия (включить видеоподсистему, перезапустить Egress, SIP, LiveKit, web).

Только администратор сервера. Действие выполняет помощник обновлений на хосте по белому списку (services/server_components.py); каждое — в аудит."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..services import server_components as sc
from ..services.audit import write_audit

router = APIRouter(prefix="/admin/components", tags=["admin"])


def _channel(request: Request):
    return sc.channel_for(request.app.state.settings)


@router.get("")
async def components(request: Request, su: SessionUser = Depends(require_admin)):
    ch = _channel(request)
    f = sc.facts(ch)
    s = request.app.state.settings
    return {"helper": sc.helper_block(ch), "facts_ts": (f or {}).get("ts"), "free_gb": (f or {}).get("free_gb"),
            "items": [sc.describe(name, f, s) for name in sc.COMPONENTS]}


@router.post("/refresh")
async def refresh(request: Request, su: SessionUser = Depends(require_admin)):
    """Обновить сведения о службах (помощник опрашивает Docker; ничего не меняет)."""
    ch = _channel(request)
    h = sc.helper_block(ch)
    if not h["available"]:
        raise HTTPException(status_code=409, detail=h["message"])
    if h["busy"]:
        return {"request_id": None, "busy": True}
    return {"request_id": ch.request("component", by=su.sam_account_name, op="state")}


@router.post("/{name}/{op}")
async def run(name: str, op: str, request: Request, body: dict = Body(default={}), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if name not in sc.COMPONENTS or op not in sc.OPS:
        raise HTTPException(status_code=404, detail="Неизвестный компонент или действие")
    ch = _channel(request)
    f = sc.facts(ch)
    if op not in sc.allowed_ops(name, f, request.app.state.settings):
        raise HTTPException(status_code=409, detail="Это действие сейчас недоступно для компонента: обновите состояние")
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail={"message": "Нужно подтверждение", "consequences": sc.OPS[op]["consequences"]})
    h = sc.helper_block(ch)
    if not h["available"] or not h["privileged"]:
        raise HTTPException(status_code=409, detail=h["message"])
    if h["busy"]:
        raise HTTPException(status_code=409, detail="Помощник уже выполняет обновление, исправление или другое действие — дождитесь завершения")
    try:
        rid = ch.request("component", by=su.sam_account_name, op=op)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Не удалось передать запрос помощнику ({getattr(exc, 'strerror', None) or exc})") from None
    request.app.state.journal.emit("system", "component_requested", level="warn", user=su.sam_account_name, ip=client_ip(request),
                                   message=f"{sc.OPS[op]['title']}", data={"component": name, "op": op, "request_id": rid})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="system.component", target_type="system", target_id=f"{name}:{op}",
                      ip=client_ip(request), details={"component": name, "op": op, "request_id": rid})
    await db.commit()
    return {"request_id": rid}
