from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.service import AuthError
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
    try:
        result = await request.app.state.auth.login(db, body.login, body.password, client_ip(request))
    except AuthError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}, headers=headers) from None
    response.set_cookie(
        settings.cookie_name, result.session_id, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return _me(SessionUser.from_session(result.session_id, result.session))


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, su: SessionUser = Depends(require_user)):
    await request.app.state.sessions.destroy(su.session_id)
    response.delete_cookie(request.app.state.settings.cookie_name, path="/")
    response.status_code = 204


@router.get("/me", response_model=MeOut)
async def me(su: SessionUser = Depends(require_user)):
    return _me(su)
