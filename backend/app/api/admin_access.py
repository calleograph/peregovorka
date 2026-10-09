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
from ..auth.directory import DirectoryError
from ..models import CaCertificate, LdapProfile, MailProfile, StorageProfile
from ..services import local_admin as la
from ..services.audit import write_audit
from ..services.ca_bundle import CertError, decode_upload, parse_certificates
from ..services.legacy_ldap import LegacyImportError
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
            "errors": getattr(d, "errors", {}), "active": bool(getattr(d, "configured", True)),
            "legacy": await request.app.state.legacy_ldap.status(db, d), "boot_errors": getattr(request.app.state, "boot_errors", {})}


@router.get("/ldap-legacy")
async def ldap_legacy(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Прежняя настройка LDAP из .env: используется ли она сейчас и можно ли её перенести в управляемые настройки. Пароль не показывается."""
    return await request.app.state.legacy_ldap.status(db, request.app.state.directory)


@router.post("/ldap-legacy/import")
async def ldap_legacy_import(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Перенести настройки»: CA из LDAP_CA_FILE, подключение (пароль шифруется), группы доступа; затем проверка подключения. Не прошла — ничего не сохраняется."""
    try:
        out = await request.app.state.legacy_ldap.import_now(db, su.display_name)
    except LegacyImportError as exc:
        request.app.state.journal.emit("auth", "ldap_legacy_import_failed", level="warn", user=su.display_name, ip=client_ip(request), message=str(exc)[:300])
        raise HTTPException(status_code=409, detail={"message": str(exc), "stages": exc.stages}) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="ldap.legacy_import", target_type="ldap_profile", target_id="legacy",
                      ip=client_ip(request), details={"profiles": out["profiles"], "ca_added": out["ca_added"], "groups_added": out["groups_added"]})
    await db.commit()
    await _reload_directory(request, db)
    request.app.state.legacy_ldap.last_error = ""
    return out


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


# ============================================================ допуск к системе («кто может входить»)
async def _acl_counts(request: Request, db: AsyncSession) -> dict[str, Any]:
    s = request.app.state.settings
    acc = await request.app.state.settings_svc.get(db, "access")
    env_users = bool((s.ldap_access_group_dn or "").strip())
    env_admins = bool((s.ldap_admin_group_dn or "").strip())
    n_users = len(acc.user_groups) + (1 if env_users else 0)        # type: ignore[attr-defined]
    n_admins = len(acc.admin_groups) + (1 if env_admins else 0)     # type: ignore[attr-defined]
    return {"restricted": n_users > 0, "user_groups": n_users, "admin_groups": n_admins, "env_user_group": env_users, "env_admin_group": env_admins}


@router.get("/access/status")
async def access_status(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Настроено ли ограничение входа. Пока список групп допуска пуст, в систему входит любой активный пользователь каталога."""
    return await _acl_counts(request, db)


@router.post("/access/check-user")
async def access_check_user(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """«Проверить пользователя»: найден ли в каталоге, пропустят ли его правила допуска (и по какой группе), получит ли права
    администратора. Пароль не нужен и не проверяется; группы читаются сервисной учётной записью (с вложенными)."""
    login = str(body.get("login") or "").strip()
    if not login or len(login) > 256:
        raise HTTPException(status_code=422, detail="Укажите логин пользователя")
    d = request.app.state.directory
    if not hasattr(d, "lookup"):
        raise HTTPException(status_code=501, detail="Каталог не поддерживает такую проверку")
    try:
        ident = await asyncio.to_thread(d.lookup, login)
    except DirectoryError as exc:
        if exc.code in ("user_not_found", "ambiguous_user"):
            out = {"found": False, "message": "Пользователь не найден в каталоге" if exc.code == "user_not_found" else "Найдено несколько учётных записей — уточните логин (user@домен)"}
            request.app.state.journal.emit("auth", "access_check", user=su.display_name, ip=client_ip(request), message="проверка пользователя: не найден")
            return out
        raise HTTPException(status_code=503, detail="Каталог недоступен или не настроен: " + (exc.detail or exc.code)) from None
    dec = await request.app.state.auth.access_decision(db, ident)
    disabled = bool(getattr(ident, "disabled", False))
    out = {"found": True, "login": ident.sam_account_name, "display_name": ident.display_name, "source": ident.source,
           "account_disabled": disabled, "restricted": dec.restricted,
           "would_log_in": dec.allowed and not disabled, "reason": "account_disabled" if disabled else dec.reason,
           "allowed_via": dec.via, "admin": dec.is_admin, "admin_via": dec.admin_via, "groups_total": len(ident.groups)}
    request.app.state.journal.emit("auth", "access_check", user=su.display_name, ip=client_ip(request),
                                   message=f"проверка пользователя {ident.sam_account_name}: {'пропустят' if out['would_log_in'] else 'не пропустят'}")
    return out


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
    login_n = len(access.user_groups) + (1 if s.ldap_access_group_dn else 0)      # type: ignore[attr-defined]
    storage_n = await _count(db, StorageProfile)
    mail_n = await _count(db, MailProfile, MailProfile.is_active.is_(True))
    steps = [
        {"id": "ldap", "title": "Подключение к каталогу (LDAPS)", "done": ldap_n > 0, "page": "ldap"},
        {"id": "ca", "title": "Сертификаты CA", "done": ca_n > 0, "page": "ca"},
        {"id": "access", "title": "Группы администраторов", "done": groups_n > 0, "page": "access"},
        {"id": "login_acl", "title": "Кто может входить в систему (группы допуска)", "done": login_n > 0, "page": "login_access"},
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
