from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.service import AuthError
from ..auth.directory import parse_login
from ..config import origin_allowed
from ..services import captcha
from ..services.audit import audit_now
from ..models import User
from ..security.passwords import verify_room_password
from ..services import local_admin as la
from ..services.audit import write_audit
from ..services.journal import parse_client
from .schemas import LoginIn, MeOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("app.auth")


def _me(su: SessionUser) -> MeOut:
    return MeOut(user=UserOut(id=su.user_id, sam_account_name=su.sam_account_name,
                              display_name=su.display_name, is_admin=su.is_admin), csrf_token=su.csrf, local=su.local, must_change_password=su.must_change, must_enroll_totp=su.must_totp)


@router.post("/login", response_model=MeOut | dict)
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin is not None and not origin_allowed(origin, settings):
        log.warning("Origin отклонён", extra={"origin": origin[:200], "path": "/auth/login"})
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    ip = client_ip(request)
    await captcha.guard(request, "login")          # после порога неудач (или всегда) нужен решённый CAPTCHA-вызов
    journal = request.app.state.journal
    ua = parse_client(request.headers.get("user-agent"))
    try:  # в журнал — только корректный логин: в поле «логин» люди нередко по ошибке вводят пароль
        login_shown = parse_login(body.login)[0]
    except Exception:  # noqa: BLE001
        login_shown = "(некорректный формат логина)"
    try:
        result = await request.app.state.auth.login(db, body.login, body.password, ip, request.headers.get("user-agent") or "")
    except AuthError as exc:
        if exc.code in ("invalid_credentials", "throttled"):
            await captcha.note_failure(request, "login")
        denied = exc.code in ("access_denied", "throttled")
        await audit_now(request.app, actor_user_id=None, actor_name=login_shown, action="auth.login_locked" if exc.code == "throttled" else "auth.login_denied" if exc.code == "access_denied" else "auth.login_failed",
                        target_type="user", target_id=login_shown[:80], ip=ip, result="denied" if denied else "failure", actor_type="user",
                        details={"code": exc.code, "reason": getattr(exc, "reason", None), "http": exc.status})
        if exc.code == "access_denied":   # пароль верный, но правила допуска не пропускают: отдельное событие для аудита
            journal.emit("auth", "login_denied_acl", level="warn", user=login_shown, ip=ip, client=ua,
                         message="Вход запрещён: пользователь не входит ни в одну разрешённую группу" if exc.reason == "not_in_allowed_groups"
                         else "Вход запрещён: подключение каталога не предназначено для входа пользователей",
                         data={"code": exc.code, "reason": exc.reason, "http": exc.status})
        else:
            journal.emit("auth", "login_locked" if exc.code == "throttled" else "login_failed", level="warn", user=login_shown, ip=ip, client=ua,
                         message=exc.message, data={"code": exc.code, "retry_after": exc.retry_after, "http": exc.status})
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message, **exc.extra}, headers=headers) from None
    await captcha.note_success(request, "login")
    if result.challenge:                       # пароль верен, нужен второй фактор: сессию и cookie выдаст /auth/login/totp
        response.headers["Cache-Control"] = "no-store"
        return JSONResponse({"totp_required": True, "challenge": result.challenge}, headers={"Cache-Control": "no-store"})
    return await _open_session(request, response, db, result, ip=ip, ua=ua)


async def _open_session(request: Request, response: Response, db: AsyncSession, result, *, ip: str, ua, second_factor: str | None = None) -> MeOut:
    settings = request.app.state.settings
    journal = request.app.state.journal
    d = result.decision
    await audit_now(request.app, actor_user_id=uuid.UUID(result.session.user_id), actor_name=result.session.display_name or result.session.sam_account_name, action="auth.login",
                    target_type="user", target_id=str(result.session.user_id), ip=ip,
                    details={"login": result.session.sam_account_name, "local": bool(getattr(result.session, "local", False)), "admin": bool(result.session.is_admin),
                             **({"second_factor": second_factor} if second_factor else {}), **({"totp_enrollment_required": True} if result.session.must_totp else {})})
    journal.emit("auth", "login_ok", user=result.session.sam_account_name, ip=ip, client=ua,
                 data={"admin": bool(result.session.is_admin), **({"allowed_via": d.via[:5], "admin_via": d.admin_via[:5], "restricted": d.restricted} if d else {"local": bool(result.session.local)})})
    # Дополнительные источники профиля (Bitrix24): пересчёт по сохранённым данным — без сети; обновление с портала — в фоне. Вход их не ждёт и от них не зависит.
    try:
        if result.user is not None and result.user.auth_source == "ad":
            await request.app.state.enrichment.on_login(db, result.user)
            request.app.state.enrichment.spawn(result.user.id)
    except Exception:  # noqa: BLE001
        pass
    response.set_cookie(
        settings.cookie_name, result.session_id, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return _me(SessionUser.from_session(result.session_id, result.session))


class TotpLoginIn(BaseModel):
    challenge: str = Field(min_length=10, max_length=100)
    code: str = Field(min_length=6, max_length=32)


@router.post("/login/totp", response_model=MeOut)
async def login_totp(body: TotpLoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Второй шаг входа: код из приложения-аутентификатора или резервный код."""
    origin = request.headers.get("origin")
    if origin is not None and not origin_allowed(origin, request.app.state.settings):
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    ip = client_ip(request)
    ua = parse_client(request.headers.get("user-agent"))
    try:
        result = await request.app.state.auth.complete_totp(db, body.challenge, body.code, ip)
    except AuthError as exc:
        await audit_now(request.app, actor_user_id=None, actor_name="(2FA)", action="auth.totp_failed", target_type="user", target_id="-", ip=ip,
                        result="denied" if exc.code in ("throttled", "totp_expired") else "failure", details={"code": exc.code})
        request.app.state.journal.emit("auth", "totp_failed", level="warn", ip=ip, client=ua, message=exc.message, data={"code": exc.code})
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message, **exc.extra}, headers=headers) from None
    if result.backup_used:
        await audit_now(request.app, actor_user_id=result.user.id, actor_name=result.user.display_name, action="auth.backup_code_used", target_type="user",
                        target_id=str(result.user.id), ip=ip)
    return await _open_session(request, response, db, result, ip=ip, ua=ua, second_factor="backup_code" if result.backup_used else "totp")


class FreeSlotIn(BaseModel):
    free_token: str = Field(min_length=10, max_length=100)
    session_id: str = Field(min_length=4, max_length=40)           # публичный номер сессии из ответа 409 session_limit


@router.post("/login/free-slot", response_model=MeOut)
async def login_free_slot(body: FreeSlotIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """После ответа 409 `session_limit`: завершить выбранную пользователем сессию и войти. Остальные устройства продолжают работать."""
    origin = request.headers.get("origin")
    if origin is not None and not origin_allowed(origin, request.app.state.settings):
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    ip = client_ip(request)
    try:
        result = await request.app.state.auth.finish_free_slot(db, body.free_token, body.session_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None
    await audit_now(request.app, actor_user_id=result.user.id, actor_name=result.user.display_name, action="auth.session_ended", target_type="user", target_id=str(result.user.id), ip=ip,
                    details={"by": "self", "reason": "session_limit", "session": body.session_id})
    return await _open_session(request, response, db, result, ip=ip, ua=parse_client(request.headers.get("user-agent")), second_factor=None)


@router.get("/sessions")
async def my_sessions(request: Request, su: SessionUser = Depends(require_user)):
    """«Активные устройства»: сессии этой учётной записи (браузер/тип устройства, адрес, последняя активность)."""
    store = request.app.state.sessions
    items = [{k: v for k, v in x.items() if k != "_key"} for x in await store.list_user(str(su.user_id), su.session_id)]
    items.sort(key=lambda x: (not x["current"], -(x["last_seen"] or 0)))
    return {"items": items}


@router.delete("/sessions/{pub}", status_code=204)
async def end_session(pub: str, request: Request, su: SessionUser = Depends(require_user)):
    """Завершить конкретную сессию (в том числе текущую = выход). Другие сессии не затрагиваются."""
    if not await request.app.state.sessions.destroy_pub(str(su.user_id), pub):
        raise HTTPException(status_code=404, detail="Сессия не найдена или уже завершена")
    await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="auth.session_ended", target_type="user", target_id=str(su.user_id), ip=client_ip(request),
                    details={"by": "self", "session": pub})


@router.post("/sessions/end-others")
async def end_other_sessions(request: Request, su: SessionUser = Depends(require_user)):
    """Завершить все сессии, кроме текущей («выйти на всех остальных устройствах»)."""
    n = await request.app.state.sessions.destroy_user(str(su.user_id), keep=su.session_id)
    await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="auth.sessions_ended_others", target_type="user", target_id=str(su.user_id), ip=client_ip(request),
                    details={"count": n})
    return {"ended": n}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, su: SessionUser = Depends(require_user)):
    await request.app.state.sessions.destroy(su.session_id)
    await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="auth.logout", target_type="user", target_id=str(su.user_id), ip=client_ip(request))
    request.app.state.journal.emit("auth", "logout", user=su.sam_account_name, ip=client_ip(request), client=parse_client(request.headers.get("user-agent")))
    response.delete_cookie(request.app.state.settings.cookie_name, path="/")
    response.status_code = 204


@router.get("/me", response_model=MeOut)
async def me(su: SessionUser = Depends(require_user)):
    return _me(su)


@router.post("/change-password", status_code=204)
async def change_password(request: Request, body: dict = Body(...), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Смена пароля ЛОКАЛЬНОГО администратора (доменные пароли меняются в домене). Прочие сессии этого пользователя завершаются."""
    if not su.local:
        raise HTTPException(status_code=403, detail="Пароль доменной учётной записи меняется в домене.")
    user = await db.get(User, su.user_id)
    cur, new = str(body.get("current_password") or ""), str(body.get("new_password") or "")
    if user is None or not user.password_hash or not verify_room_password(user.password_hash, cur):
        raise HTTPException(status_code=403, detail="Текущий пароль указан неверно.")
    try:
        la.validate_new_password(new, user.sam_account_name, old=cur)
    except la.LocalAdminError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await la.set_password(db, user, new, must_change=False)
    await write_audit(db, actor_user_id=user.id, actor_name=user.display_name, action="local_admin.password_changed", target_type="user",
                      target_id=str(user.id), ip=client_ip(request), details={"user": user.sam_account_name})
    await db.commit()
    await request.app.state.sessions.destroy_user(str(user.id), keep=su.session_id)
    await request.app.state.sessions.update(su.session_id, must_change=False)
    request.app.state.journal.emit("auth", "local_admin_password_changed", user=user.sam_account_name, ip=client_ip(request))
