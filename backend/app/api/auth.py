from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.service import AuthError
from ..auth.directory import parse_login
from ..models import User
from ..security.passwords import verify_room_password
from ..services import local_admin as la
from ..services.audit import write_audit
from ..services.journal import parse_client
from .schemas import LoginIn, MeOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _me(su: SessionUser) -> MeOut:
    return MeOut(user=UserOut(id=su.user_id, sam_account_name=su.sam_account_name,
                              display_name=su.display_name, is_admin=su.is_admin), csrf_token=su.csrf, local=su.local, must_change_password=su.must_change)


@router.post("/login", response_model=MeOut)
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != settings.public_origin:
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    ip = client_ip(request)
    journal = request.app.state.journal
    ua = parse_client(request.headers.get("user-agent"))
    try:  # в журнал — только корректный логин: в поле «логин» люди нередко по ошибке вводят пароль
        login_shown = parse_login(body.login)[0]
    except Exception:  # noqa: BLE001
        login_shown = "(некорректный формат логина)"
    try:
        result = await request.app.state.auth.login(db, body.login, body.password, ip)
    except AuthError as exc:
        journal.emit("auth", "login_locked" if exc.code == "throttled" else "login_failed", level="warn", user=login_shown, ip=ip, client=ua,
                     message=exc.message, data={"code": exc.code, "retry_after": exc.retry_after, "http": exc.status})
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}, headers=headers) from None
    journal.emit("auth", "login_ok", user=result.session.sam_account_name, ip=ip, client=ua, data={"admin": bool(result.session.is_admin)})
    response.set_cookie(
        settings.cookie_name, result.session_id, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return _me(SessionUser.from_session(result.session_id, result.session))


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, su: SessionUser = Depends(require_user)):
    await request.app.state.sessions.destroy(su.session_id)
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
