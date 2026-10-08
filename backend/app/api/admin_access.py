"""Администрирование доступа: подключения LDAPS, CA-сертификаты, мастер первоначальной настройки, сведения о локальном администраторе.

Все действия — только для администраторов, в аудит; пароли и секреты наружу не отдаются (только «задан/не задан»).
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..models import CaCertificate, LdapProfile, MailProfile, StorageProfile
from ..services import local_admin as la
from ..services.audit import write_audit
from ..services.ca_bundle import CertError, decode_upload, parse_certificates
from ..services.settings import SettingsError

router = APIRouter(prefix="/admin", tags=["admin-access"])


async def _reload_directory(request: Request, db: AsyncSession) -> None:
    d = request.app.state.directory
    if hasattr(d, "reload"):
        await d.reload(db)


# ===================================================================== LDAP-подключения
@router.get("/ldap-profiles")
async def ldap_list(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    s = request.app.state.settings
    d = request.app.state.directory
    return {"items": await request.app.state.ldap.list(db),
            "env": {"configured": bool(s.ldap_uri_list), "uris": s.ldap_uri_list, "base_dn": s.ldap_base_dn},   # прежняя настройка из .env — только для сведения
            "errors": getattr(d, "errors", {}), "active": bool(getattr(d, "configured", True))}


@router.post("/ldap-profiles", status_code=201)
async def ldap_create(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        out = await request.app.state.ldap.create(db, body, str(body.get("secret") or ""))
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="ldap.create", target_type="ldap_profile", target_id=out["id"],
                      ip=client_ip(request), details={"name": out["name"], "uri": out["uri"]})
    await db.commit()
    await _reload_directory(request, db)
    return out


@router.patch("/ldap-profiles/{pid}")
async def ldap_update(pid: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        out = await request.app.state.ldap.update(db, pid, body, body.get("secret") if "secret" in body else None)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="ldap.update", target_type="ldap_profile", target_id=pid, ip=client_ip(request),
                      details={"name": out["name"], "changed": sorted(k for k in body if k != "secret"), "secret_changed": body.get("secret") is not None})
    await db.commit()
    await _reload_directory(request, db)
    return out


@router.delete("/ldap-profiles/{pid}", status_code=204)
async def ldap_delete(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        r = await request.app.state.ldap.delete(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="ldap.delete", target_type="ldap_profile", target_id=pid, ip=client_ip(request),
                      details={"name": r.name})
    await db.commit()
    await _reload_directory(request, db)


@router.post("/ldap-profiles/{pid}/move")
async def ldap_move(pid: str, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        await request.app.state.ldap.move(db, pid, -1 if int(body.get("direction", 1)) < 0 else 1)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await db.commit()
    await _reload_directory(request, db)
    return {"items": await request.app.state.ldap.list(db)}


@router.post("/ldap-profiles/{pid}/test")
async def ldap_test(pid: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Реальная проверка: DNS → TCP → TLS с проверкой цепочки по загруженным CA → вход сервисной учётной записи → чтение Base DN."""
    svc = request.app.state.ldap
    try:
        row = await svc.row(db, pid)
    except SettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await request.app.state.ca.rebuild(db)
    res = await asyncio.to_thread(svc.diagnose, row)
    request.app.state.journal.emit("auth", "ldap_test", level="info" if res["ok"] else "warn", user=su.display_name, ip=client_ip(request),
                                   message=f"{row.name}: {'OK' if res['ok'] else 'ошибка'}", data={"stages": [(s["stage"], s["ok"]) for s in res["stages"]]})
    return res


# ===================================================================== CA-сертификаты
@router.get("/ca")
async def ca_list(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return {"items": await request.app.state.ca.list(db)}


@router.post("/ca/inspect")
async def ca_inspect(body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin)):
    """Разбор сертификата ДО сохранения: Subject, Issuer, серийный номер, SHA-256, срок действия, тип."""
    try:
        return {"items": [i.public() for i in parse_certificates(decode_upload(body))]}
    except CertError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/ca", status_code=201)
async def ca_add(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        added, existing = await request.app.state.ca.add(db, decode_upload(body), str(body.get("label") or ""), su.display_name, confirm_non_ca=bool(body.get("confirm_non_ca")))
    except CertError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="ca.add", target_type="ca_certificate", target_id=",".join(i.sha256[:12] for i in added) or "-",
                      ip=client_ip(request), details={"added": [{"subject": i.subject, "sha256": i.sha256} for i in added], "already": len(existing)})
    await db.commit()
    await _reload_directory(request, db)
    return {"added": [i.public() for i in added], "already_present": [i.public() for i in existing]}


@router.delete("/ca/{cert_id}", status_code=204)
async def ca_delete(cert_id: str, request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    try:
        row = await request.app.state.ca.remove(db, cert_id)
    except CertError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="ca.delete", target_type="ca_certificate", target_id=cert_id, ip=client_ip(request),
                      details={"subject": row.subject, "sha256": row.sha256})
    await db.commit()
    await _reload_directory(request, db)


# ============================================================ локальный администратор и мастер
@router.get("/local-admin")
async def local_admin_info(su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    u = await la.find_local(db)
    if u is None:
        return {"exists": False, "recovery": "./scripts/admin-reset.sh --create"}
    return {"exists": True, "username": u.sam_account_name, "is_active": u.is_active, "must_change_password": u.must_change_password,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "password_changed_at": u.password_changed_at.isoformat() if u.password_changed_at else None, "recovery": "./scripts/admin-reset.sh"}


async def _count(db: AsyncSession, model, *where) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


@router.get("/setup/status")
async def setup_status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Шаги мастера первоначальной настройки: что уже сделано. Мастер показывается, пока не завершён или не пропущен."""
    s = request.app.state.settings
    svc = request.app.state.settings_svc
    setup = await svc.get(db, "setup")
    access = await svc.get(db, "access")
    ldap_n = await _count(db, LdapProfile, LdapProfile.enabled.is_(True)) + (1 if s.ldap_uri_list else 0)
    ca_n = await _count(db, CaCertificate) + (1 if s.ldap_ca_file else 0)
    groups_n = len(access.admin_groups) + (1 if s.ldap_admin_group_dn else 0)   # type: ignore[attr-defined]
    storage_n = await _count(db, StorageProfile)
    mail_n = await _count(db, MailProfile, MailProfile.is_active.is_(True))
    steps = [
        {"id": "ldap", "title": "Подключение к каталогу (LDAPS)", "done": ldap_n > 0, "page": "ldap"},
        {"id": "ca", "title": "Сертификаты CA", "done": ca_n > 0, "page": "ca"},
        {"id": "access", "title": "Группы администраторов", "done": groups_n > 0, "page": "access"},
        {"id": "storage", "title": "Файловое хранилище", "done": storage_n > 0, "page": "storages"},
        {"id": "mail", "title": "Исходящая почта", "done": mail_n > 0, "page": "mail"},
        {"id": "check", "title": "Проверка системы", "done": False, "page": "system"},
    ]
    return {"completed": setup.completed, "skipped": setup.skipped, "steps": steps,   # type: ignore[attr-defined]
            "show": (not setup.completed) and su.local}                               # type: ignore[attr-defined]


@router.post("/setup/complete")
async def setup_complete(request: Request, body: dict[str, Any] = Body(default={}), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    skipped = [str(x)[:20] for x in (body.get("skipped") or []) if isinstance(x, str)][:10]
    await request.app.state.settings_svc.update(db, "setup", {"completed": True, "skipped": skipped}, actor=su.display_name)
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="setup.complete", target_type="system", target_id="setup",
                      ip=client_ip(request), details={"skipped": skipped})
    await db.commit()
    return {"completed": True}
