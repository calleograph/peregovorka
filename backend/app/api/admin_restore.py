"""Администрирование → Хранилища → «Восстановление материалов»: сканирование хранилищ, предварительный отчёт, восстановление выбранного. Правила — в services/restore.py.
Все ручки только для администратора; тяжёлая работа идёт в фоне (запрос лишь запускает задание и возвращает его идентификатор)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import RestoreJob, utcnow
from ..services.audit import write_audit
from ..services.restore import MAX_ITEMS, STALE_AFTER, RestoreBusy

router = APIRouter(prefix="/admin/restore", tags=["admin-restore"])


def _view(j: RestoreJob, *, full: bool = False) -> dict[str, Any]:
    stale = j.status == "running" and (j.heartbeat_at is None or utcnow() - j.heartbeat_at > STALE_AFTER)
    out = {"id": str(j.id), "kind": j.kind, "parent_id": str(j.parent_id) if j.parent_id else None, "status": "stale" if stale else j.status, "params": j.params, "progress": j.progress or {},
           "error": j.error, "started_by": j.started_by_name, "created_at": j.created_at, "finished_at": j.finished_at}
    if full:
        out["result"] = j.result
    elif j.result:
        out["counts"] = j.result.get("counts")
    return out


def _svc(request: Request):
    return request.app.state.restore


@router.get("")
async def overview(su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    jobs = (await db.execute(select(RestoreJob).order_by(RestoreJob.created_at.desc()).limit(15))).scalars().all()
    return {"jobs": [_view(j) for j in jobs], "limits": {"max_items": MAX_ITEMS}}


@router.get("/jobs/{job_id}")
async def job(job_id: uuid.UUID, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    j = await db.get(RestoreJob, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    return _view(j, full=True)


@router.post("/scan", status_code=202)
async def scan(request: Request, body: dict = Body(default={}), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    sub = str(body.get("subpath") or "").strip().strip("/")[:300]
    if ".." in sub.split("/") or "\\" in sub or "\x00" in sub:
        raise HTTPException(status_code=422, detail="Подкаталог указан неверно")
    params = {"subpath": sub, "deep": bool(body.get("deep"))}
    try:
        j = await _svc(request).start("scan", user_id=su.user_id, user_name=su.display_name, params=params)
    except RestoreBusy as exc:
        raise HTTPException(status_code=409, detail={"code": "busy", "message": str(exc)}) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="restore.scan", target_type="storage", target_id=str(j.id), ip=client_ip(request), details=params)
    await db.commit()
    return _view(j)


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    j = await db.get(RestoreJob, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    if j.status == "running":
        await _svc(request).cancel(job_id)
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="restore.cancel", target_type="storage", target_id=str(job_id), ip=client_ip(request))
        await db.commit()
    return {"id": str(job_id), "cancelling": j.status == "running"}


@router.post("/apply", status_code=202)
async def apply(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Восстановить выбранное из результата сканирования. Нужно явное подтверждение: операция добавляет встречи и материалы в базу (исходные файлы не трогаются)."""
    if body.get("confirm") is not True:
        raise HTTPException(status_code=422, detail={"code": "confirm_required", "message": "Подтвердите восстановление."})
    try:
        scan_id = uuid.UUID(str(body.get("scan_id")))
    except ValueError:
        raise HTTPException(status_code=422, detail="Не указано сканирование") from None
    scan_job = await db.get(RestoreJob, scan_id)
    if scan_job is None or scan_job.kind != "scan" or scan_job.status != "done" or not scan_job.result:
        raise HTTPException(status_code=409, detail={"code": "no_scan", "message": "Результат сканирования недоступен: выполните сканирование заново."})
    ids = body.get("items")
    known = {i["id"] for i in scan_job.result["items"]}
    if not isinstance(ids, list) or not ids or len(ids) > MAX_ITEMS or not all(isinstance(x, str) and x in known for x in ids):
        raise HTTPException(status_code=422, detail={"code": "bad_items", "message": "Выберите встречи из результата сканирования."})
    try:
        j = await _svc(request).start("restore", user_id=su.user_id, user_name=su.display_name, params={"items": sorted(set(ids))}, parent_id=scan_id)
    except RestoreBusy as exc:
        raise HTTPException(status_code=409, detail={"code": "busy", "message": str(exc)}) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="restore.apply", target_type="storage", target_id=str(j.id), ip=client_ip(request),
                      details={"scan": str(scan_id), "items": len(set(ids))})
    await db.commit()
    return _view(j)
