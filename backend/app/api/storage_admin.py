"""Администрирование хранилища записей: показатели (из готового фонового снимка) и перенос данных между локальным диском и внешним хранилищем.
Все эндпоинты — только администратору; запуск, остановка и возобновление переноса пишутся в аудит."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, get_db, require_admin
from ..models import StorageMigration, StorageTransfer, StorageTransferItem
from ..services.storage_migration import CATEGORIES, GROUPS, MigrationError
from ..services.transfer import TransferError

router = APIRouter(prefix="/admin/storage", tags=["admin"])


class TransferIn(BaseModel):
    direction: str = Field(pattern="^(to_external|to_local)$")
    meeting_id: uuid.UUID | None = None       # пусто — все подходящие записи


@router.get("/stats")
async def stats(request: Request, su: SessionUser = Depends(require_admin)):
    """Готовый снимок (мгновенно). Замер идёт в фоне — страница его не ждёт."""
    return await request.app.state.storage_stats.snapshot()


@router.post("/stats/refresh", status_code=202)
async def refresh_stats(request: Request, su: SessionUser = Depends(require_admin)):
    started = request.app.state.storage_stats.refresh_in_background()
    return {"started": started}


@router.get("/transfers")
async def list_transfers(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.transfers
    rows = (await db.execute(select(StorageTransfer).order_by(StorageTransfer.created_at.desc()).limit(20))).scalars().all()
    return [await svc.summary(db, j) for j in rows]


@router.post("/transfers", status_code=201)
async def start_transfer(body: TransferIn, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.transfers
    try:
        job = await svc.create(db, body.direction, meeting_id=body.meeting_id, actor=su.display_name)
    except TransferError as exc:
        raise HTTPException(409, str(exc)) from None
    return await svc.summary(db, job)


@router.get("/transfers/{job_id}")
async def get_transfer(job_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.transfers
    job = await db.get(StorageTransfer, job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    items = (await db.execute(select(StorageTransferItem).where(StorageTransferItem.transfer_id == job_id, StorageTransferItem.state.in_(("failed", "skipped")) | (StorageTransferItem.error.is_not(None)))
                              .order_by(StorageTransferItem.id).limit(50))).scalars().all()
    return {**await svc.summary(db, job), "problems": [{"recording_id": str(i.recording_id), "state": i.state, "error": i.error} for i in items]}


@router.post("/transfers/{job_id}/cancel")
async def cancel_transfer(job_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.transfers
    job = await svc.cancel(db, job_id, su.display_name)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    return await svc.summary(db, job)


@router.post("/transfers/{job_id}/resume")
async def resume_transfer(job_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.transfers
    try:
        job = await svc.resume(db, job_id, su.display_name)
    except TransferError as exc:
        raise HTTPException(409, str(exc)) from None
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    return await svc.summary(db, job)


# ----------------------------------------------------------------------------- перенос между хранилищами
class MigrationIn(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    groups: list[str] | None = None           # пусто — полный перенос


class MigrationRetry(BaseModel):
    overwrite: bool = False


class MigrationClean(BaseModel):
    confirm: str = Field(max_length=200)


def _refused(exc: MigrationError) -> HTTPException:
    return HTTPException(409, str(exc))


@router.get("/migrations")
async def list_migrations(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(StorageMigration).order_by(StorageMigration.created_at.desc()).limit(10))).scalars().all()
    return {"items": [request.app.state.migrations.view(j) for j in rows], "groups": GROUPS, "categories": CATEGORIES}


@router.post("/migrations", status_code=201)
async def create_migration(body: MigrationIn, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.migrations
    try:
        return svc.view(await svc.create(db, str(body.source_id), str(body.target_id), body.groups, actor=su.display_name))
    except MigrationError as exc:
        raise _refused(exc) from None


@router.get("/migrations/{job_id}")
async def get_migration(job_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.migrations
    job = await db.get(StorageMigration, job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    return {**svc.view(job), "events": list(job.events or [])[-100:], "problems": await svc.problems(db, job_id)}


@router.post("/migrations/{job_id}/start")
async def start_migration(job_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.migrations
    try:
        return svc.view(await svc.start(db, job_id, su.display_name))
    except MigrationError as exc:
        raise _refused(exc) from None


@router.post("/migrations/{job_id}/cancel")
async def cancel_migration(job_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.migrations
    try:
        return svc.view(await svc.cancel(db, job_id, su.display_name))
    except MigrationError as exc:
        raise _refused(exc) from None


@router.post("/migrations/{job_id}/retry")
async def retry_migration(job_id: uuid.UUID, body: MigrationRetry, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.migrations
    try:
        return svc.view(await svc.retry(db, job_id, overwrite=body.overwrite, actor=su.display_name))
    except MigrationError as exc:
        raise _refused(exc) from None


@router.post("/migrations/{job_id}/clean")
async def clean_migration(job_id: uuid.UUID, body: MigrationClean, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    svc = request.app.state.migrations
    try:
        return svc.view(await svc.clean(db, job_id, body.confirm, su.display_name))
    except MigrationError as exc:
        raise _refused(exc) from None
