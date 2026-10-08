"""Сверка хранилищ: кнопка «Проверить/синхронизировать хранилище», отчёты о запусках, настройки расписания (группа `storage_sync`)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import StorageSyncRun

router = APIRouter(prefix="/admin/storage-sync", tags=["admin-storage-sync"])


def _run(r: StorageSyncRun, full: bool = False) -> dict:
    d = {"id": str(r.id), "trigger": r.trigger, "actor": r.actor, "status": r.status, "started_at": r.started_at.isoformat(),
         "finished_at": r.finished_at.isoformat() if r.finished_at else None, "checked": r.checked, "missing": r.missing, "restored": r.restored,
         "orphans": r.orphans, "unavailable": r.unavailable}
    if full:
        d["details"] = r.details or {}
    return d


@router.get("")
async def overview(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    cfg = await request.app.state.settings_svc.get(db, "storage_sync")
    runs = (await db.execute(select(StorageSyncRun).order_by(StorageSyncRun.started_at.desc()).limit(20))).scalars().all()
    running = next((r for r in runs if r.status == "running"), None)
    last = next((r for r in runs if r.status != "running"), None)
    return {"settings": cfg.model_dump(), "runs": [_run(r) for r in runs], "running": _run(running) if running else None, "last": _run(last, True) if last else None}


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        r = await db.get(StorageSyncRun, uuid.UUID(run_id))
    except ValueError:
        r = None
    if r is None:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return _run(r, True)


@router.post("/run", status_code=202)
async def start(request: Request, body: dict[str, Any] = Body(default={}), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Запускает сверку в фоне и сразу отвечает; ход — через GET /admin/storage-sync. `force` — применить результат, даже если «пропало почти всё»."""
    if (await db.execute(select(StorageSyncRun.id).where(StorageSyncRun.status == "running"))).first():
        raise HTTPException(status_code=409, detail="Сверка уже идёт. Дождитесь её окончания.")
    cfg = await request.app.state.settings_svc.get(db, "storage_sync")
    rid = uuid.uuid4()
    force = bool(body.get("force"))
    run = StorageSyncRun(id=rid, trigger="manual", actor=su.display_name, status="running")
    db.add(run)
    await db.commit()
    rec = request.app.state.reconciler
    request.app.state.protocols.spawn(rec.run("manual", su.display_name, force=force, guard_percent=cfg.guard_percent, run_id=rid), f"storage-sync-{rid}")  # type: ignore[attr-defined]
    request.app.state.journal.emit("storage", "sync_started", user=su.display_name, ip=client_ip(request), message="Запущена сверка хранилищ", data={"run": str(rid), "force": force})
    return {"run_id": str(rid)}
