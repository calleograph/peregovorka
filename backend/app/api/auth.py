from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.service import AuthError
from ..auth.directory import parse_login
from ..services.journal import parse_client
from .schemas import LoginIn, MeOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _me(su: SessionUser) -> MeOut:
    return MeOut(user=UserOut(id=su.user_id, sam_account_name=su.sam_account_name,
                              display_name=su.display_name, is_admin=su.is_admin), csrf_token=su.csrf)


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
