"""«Администрирование → Система → Автоматическая копия настроек»: расписание, ключ шифрования, проверка и скачивание копий с сетевого хранилища."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..services.audit import write_audit
from ..services.auto_backup import BackupError, new_key, run_out
from ..services.settings import SettingsError
from .config_backup import _reauth

router = APIRouter(prefix="/admin/auto-backup", tags=["admin"])


def _svc(request: Request):
    return request.app.state.auto_backup


@router.get("")
async def status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await _svc(request).status(db)


@router.put("/settings")
async def save_settings(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Сохранение расписания. Ключ шифрования через эту ручку не меняется (только `POST /key` с подтверждением пароля)."""
    if "archive_key" in body:
        raise HTTPException(status_code=422, detail="Ключ шифрования копий создаётся отдельной кнопкой.")
    svc = request.app.state.settings_svc
    cur = await svc.get(db, "auto_backup")
    if body.get("enabled") and not cur.archive_key:
        raise HTTPException(status_code=422, detail="Сначала создайте ключ шифрования копий: без него копия не может быть зашифрована.")
    if body.get("enabled") and not (body.get("profile_id") or cur.profile_id):
        raise HTTPException(status_code=422, detail="Выберите хранилище для копий.")
    try:
        if body.get("profile_id"):
            await request.app.state.files.validate_choice(db, {"profile_id": body["profile_id"]})
        changed = await svc.update(db, "auto_backup", body, actor=su.display_name)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="backup.settings", target_type="config_backup", target_id="settings",
                      ip=client_ip(request), details={"changed": changed})
    await db.commit()
    return await _svc(request).status(db)


@router.post("/key")
async def make_key(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Новый ключ шифрования копий (подтверждение паролем администратора). Показывается один раз: прежние копии открываются прежним ключом."""
    await _reauth(request, db, su, str(body.get("password") or ""))
    key = new_key()
    try:
        await request.app.state.settings_svc.update(db, "auto_backup", {"archive_key": key}, actor=su.display_name)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="backup.key_create", target_type="config_backup", target_id="key", ip=client_ip(request))
    await db.commit()
    return Response(json.dumps({"key": key}), media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/test")
async def test_storage(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    cfg = await request.app.state.settings_svc.get(db, "auto_backup")
    if not cfg.profile_id:
        raise HTTPException(status_code=422, detail="Выберите хранилище для копий.")
    try:
        return {"message": await request.app.state.files.test_profile(db, cfg.profile_id)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from None


@router.post("/run")
async def run_now(request: Request, su: SessionUser = Depends(require_admin)):
    try:
        run = await _svc(request).create("manual", su.display_name, su.user_id)
    except BackupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return run_out(run)


@router.post("/verify")
async def verify(request: Request, body: dict[str, Any] = Body(default={}), su: SessionUser = Depends(require_admin)):
    return run_out(await _svc(request).verify(body.get("name") or None, su.display_name, su.user_id))


@router.get("/files")
async def files(request: Request, su: SessionUser = Depends(require_admin)):
    try:
        return await _svc(request).files()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from None


@router.post("/download")
async def download(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Скачивание копии из хранилища: только администратор, с повторным вводом пароля; событие пишется в аудит с IP."""
    await _reauth(request, db, su, str(body.get("password") or ""))
    name = str(body.get("name") or "")
    try:
        blob = await _svc(request).fetch(name, su.display_name, su.user_id, client_ip(request))
    except BackupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return Response(blob, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
