"""«Администрирование → Сервер → Резервная копия конфигурации»: шифрованный экспорт и импорт настроек.

Архив не сохраняется на сервере ни на каком этапе: собирается в памяти, шифруется и сразу уходит в ответе; при импорте приходит в теле запроса и обрабатывается в памяти (между шагами
«проверить» и «применить» сервер ничего не хранит — архив отправляется повторно). Пароль архива генерируется сервером, показывается один раз и нигде не сохраняется: он передаётся
заголовком ответа/запроса (не в адресе, не в журналах приложения). Выгрузка и применение требуют повторного ввода пароля администратора. В аудит пишутся только факт, администратор, время и итог.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..auth.service import AuthError
from ..services.audit import write_audit
from ..services.config_backup import checks, container, exporter, importer
from ..services.config_backup import registry as R

router = APIRouter(prefix="/admin/config", tags=["admin"])
_lock = asyncio.Lock()
FAIL_LIMIT, FAIL_WINDOW_S = 10, 900


class ExportIn(BaseModel):
    password: str                                   # пароль администратора (повторное подтверждение)


async def _reauth(request: Request, db: AsyncSession, su: SessionUser, password: str) -> None:
    try:
        ok = await request.app.state.auth.verify_current(db, su, password, client_ip(request))
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    if not ok:
        raise HTTPException(status_code=403, detail="Неверный пароль администратора.")


async def _body(request: Request) -> bytes:
    """Тело запроса (архив) с жёстким пределом размера: читается потоком и обрывается при превышении."""
    try:
        if int(request.headers.get("content-length") or 0) > container.MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"Файл слишком большой: архив настроек не бывает больше {container.MAX_FILE_BYTES >> 20} МиБ.")
    except ValueError:
        pass
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > container.MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"Файл слишком большой: архив настроек не бывает больше {container.MAX_FILE_BYTES >> 20} МиБ.")
    if not buf:
        raise HTTPException(status_code=400, detail="Файл архива не передан.")
    return bytes(buf)


async def _open(request: Request, su: SessionUser, blob: bytes) -> tuple[dict, dict]:
    """Расшифровка архива с ограничением перебора пароля (на администратора)."""
    redis = request.app.state.redis
    key = f"cfgimport:fail:{su.user_id}"
    if int(await redis.get(key) or 0) >= FAIL_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много неверных попыток ввода пароля архива. Повторите через несколько минут.")
    pw = request.headers.get("x-archive-password", "")
    try:
        header, payload = await asyncio.to_thread(container.open_, blob, pw)
    except container.ContainerError as exc:
        if exc.code == "bad_password":
            n = await redis.incr(key)
            if n == 1:
                await redis.expire(key, FAIL_WINDOW_S)
        raise HTTPException(status_code=409 if exc.code == "too_new" else 400, detail={"code": exc.code, "message": exc.message}) from None
    await redis.delete(key)
    return header, payload


@router.get("/info")
async def info(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Что войдёт в копию и что нет — показывается перед скачиванием (без значений)."""
    return {**await exporter.describe(db), "schema": R.SCHEMA_VERSION, "format": R.FORMAT_VERSION, "password_length": container.PASSWORD_LEN}


@router.post("/export")
async def export(body: ExportIn, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await _reauth(request, db, su, body.password)
    s = request.app.state
    try:
        payload = await exporter.build_payload(db, s.settings_svc, app_version=s.settings.app_version, public_url=s.settings.app_public_url, branding=s.branding)
    except exporter.ExportError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    password = container.generate_password()
    blob = await asyncio.to_thread(container.seal, payload, password, app_version=s.settings.app_version)
    del payload
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="config.export", target_type="config", target_id="export", ip=client_ip(request),
                      details={"result": "ok", "size_bytes": len(blob)})
    await db.commit()
    name = f"peregovorka-config-{datetime.now().strftime('%Y%m%d-%H%M')}.pgcfg"
    return Response(blob, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "X-Archive-Password": password, "Access-Control-Expose-Headers": "X-Archive-Password, Content-Disposition",
        "Cache-Control": "no-store, no-cache, max-age=0", "Pragma": "no-cache", "X-Accel-Buffering": "no"})          # без буферизации прокси: шифртекст не оседает на диске промежуточных серверов


@router.post("/import/inspect")
async def inspect(request: Request, su: SessionUser = Depends(require_admin)):
    """Только заголовок (без пароля): версия, дата, совместимость — чтобы сообщить «архив новее сервера» до ввода пароля."""
    blob = await _body(request)
    try:
        h, _ = container.read_header(blob)
    except container.ContainerError as exc:
        raise HTTPException(status_code=409 if exc.code == "too_new" else 400, detail={"code": exc.code, "message": exc.message}) from None
    return {"app_version": h.get("app_version"), "created_at": h.get("created_at"), "schema": h.get("schema"), "format": h.get("format"), "size_bytes": len(blob)}


@router.post("/import/preview")
async def import_preview(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    blob = await _body(request)
    _, payload = await _open(request, su, blob)
    try:
        payload = importer.validate_payload(payload)
    except importer.ImportError_ as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid", "message": exc.message, "details": exc.details}) from None
    s = request.app.state
    return await importer.preview(payload, db, current_version=s.settings.app_version, current_url=s.settings.app_public_url)


@router.post("/import/apply")
async def import_apply(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    blob = await _body(request)
    await _reauth(request, db, su, request.headers.get("x-admin-password", ""))
    if request.headers.get("x-import-confirm") != "yes":
        raise HTTPException(status_code=400, detail="Не получено подтверждение применения.")
    _, payload = await _open(request, su, blob)
    try:
        payload = importer.validate_payload(payload)
    except importer.ImportError_ as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid", "message": exc.message, "details": exc.details}) from None
    s = request.app.state
    if _lock.locked():
        raise HTTPException(status_code=409, detail="Импорт уже выполняется.")
    async with _lock:
        try:
            res = await importer.apply(payload, db, s.settings_svc, branding=s.branding)
            await db.commit()
        except importer.ImportError_ as exc:
            await db.rollback()
            await _audit_fail(request, su, db, exc.message)
            raise HTTPException(status_code=422, detail={"code": "apply_failed", "message": exc.message, "details": exc.details}) from None
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            await _audit_fail(request, su, db, type(exc).__name__)
            raise HTTPException(status_code=500, detail={"code": "apply_failed", "message": "Импорт не выполнен: ошибка при записи настроек. Все изменения отменены, сервер остался в прежнем состоянии.", "details": [type(exc).__name__]}) from None
        file_problems = importer.write_files(res.pop("_files"), s.branding)
        # перечитать то, что приложение держит в памяти: набор CA и подключения к каталогу
        refresh: list[str] = []
        try:
            await s.ca.rebuild(db)
            if hasattr(s.directory, "reload"):
                await s.directory.reload(db)
        except Exception as exc:  # noqa: BLE001
            refresh.append(f"не удалось применить подключения без перезапуска ({type(exc).__name__}) — перезапустите приложение")
        report = await checks.run_checks(request.app, db)
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="config.import", target_type="config", target_id="import", ip=client_ip(request),
                          details={"result": "ok", "tables": res["tables"], "settings_groups": len(res["settings"]), "checks": checks.summarize(report)})
        await db.commit()
    return {"ok": True, "applied": {k: v for k, v in res.items() if not k.startswith("_")}, "files_problems": file_problems, "refresh_problems": refresh,
            "checks": report, "summary": checks.summarize(report)}


async def _audit_fail(request: Request, su: SessionUser, db: AsyncSession, why: str) -> None:
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="config.import", target_type="config", target_id="import", ip=client_ip(request),
                      details={"result": "failed", "reason": why[:200]})
    await db.commit()
