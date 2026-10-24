"""Настольный клиент Peregovorka для Windows (desktop/): сведения о сервере для мастера подключения и «доверенное устройство» — вход без пароля после перезапуска.

Токен доверенного устройства выдаётся и принимается только запросами главного процесса клиента: без заголовка Origin и с Sec-Fetch-Site «none» или без него
(браузер для запросов страницы ставит Origin и Sec-Fetch-Site = same-origin/cross-site всегда, страница не может их изменить). Поэтому даже XSS на странице не получит долгоживущий токен: он никогда не попадает в веб-содержимое, клиент хранит его
средствами Windows (DPAPI). Подробно — docs/DESKTOP.md.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.service import AuthError
from ..models import User
from ..services.audit import audit_now
from ..services.journal import parse_client

router = APIRouter(prefix="/desktop", tags=["desktop"])

API_LEVEL = 1                  # растёт при несовместимых изменениях протокола клиента; клиент сообщает, если не знает такого уровня
MIN_CLIENT = "1.0.0"           # старые клиенты получают понятное сообщение «обновите приложение»
_NO_STORE = {"Cache-Control": "no-store"}


def _desktop_only(request: Request) -> None:
    # Запрос страницы (fetch/форма) браузер всегда помечает Origin и Sec-Fetch-Site = same-origin/same-site/cross-site — подделать их скрипт не может.
    # Сетевой модуль главного процесса Electron Origin не ставит, а Sec-Fetch-Site у него «none».
    site = request.headers.get("sec-fetch-site")
    if request.headers.get("origin") is not None or (site is not None and site != "none") or request.headers.get("x-peregovorka-desktop") != "1":
        raise HTTPException(status_code=403, detail={"code": "desktop_only", "message": "Доступно только приложению Peregovorka для Windows."})


async def _days(request: Request, db: AsyncSession) -> int:
    return int((await request.app.state.settings_svc.get(db, "auth_security")).desktop_trust_days)


@router.get("/info")
async def info(request: Request, db: AsyncSession = Depends(get_db)):
    """Без входа: что за сервер и что он умеет — мастер подключения проверяет совместимость до авторизации."""
    s = request.app.state.settings
    site = await request.app.state.settings_svc.get(db, "site")
    days = await _days(request, db)
    await db.commit()
    return JSONResponse({"product": "peregovorka", "api": API_LEVEL, "min_client": MIN_CLIENT, "server_version": s.app_version, "name": site.short_name or site.name,
                         "icon": f"/api/v1/public/site/asset/favicon?v={site.favicon_v}" if site.favicon_v else None, "cookie": s.cookie_name,
                         "trust_days": days, "features": ["trust", "notify", "deep_links"]}, headers={"Cache-Control": "no-cache"})


@router.post("/trust")
async def trust(request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Доверить это устройство: токен для входа без пароля после перезапуска клиента. Срок — `auth_security.desktop_trust_days` от входа паролем."""
    _desktop_only(request)
    days = await _days(request, db)
    if days <= 0:
        raise HTTPException(status_code=404, detail={"code": "trust_disabled", "message": "Сохранение входа на этом сервере отключено администратором."})
    store = request.app.state.sessions
    data = await store.get(su.session_id, touch=False)
    if data is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    user = await db.get(User, su.user_id)
    pwd = await request.app.state.auth.trust_pwd(user) if user is not None else None
    tok, exp = await store.trust_issue(str(su.user_id), data.pub, days=days, params={"is_admin": su.is_admin, "groups": sorted(su.groups), "local": su.local},
                                       born=data.created_at, pwd=pwd)
    await audit_now(request.app, actor_user_id=su.user_id, actor_name=su.display_name, action="auth.desktop_trust", target_type="user", target_id=str(su.user_id),
                    ip=client_ip(request), details={"days": days})
    return JSONResponse({"token": tok, "expires_at": exp}, headers=_NO_STORE)


@router.post("/resume")
async def resume(request: Request, response: Response, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """Новая сессия по токену доверенного устройства (вместо пароля). 401 — токен больше не действует (клиент забывает его и показывает вход);
    503 — каталог временно недоступен (клиент повторит позже, токен сохраняется); 409 — нет места в лимите сеансов (покажет вход)."""
    _desktop_only(request)
    ip = client_ip(request)
    ua = request.headers.get("user-agent") or ""
    try:
        res, nxt = await request.app.state.auth.resume_device(db, str(body.get("token") or ""), ip, ua)
    except AuthError as exc:
        if exc.code == "trust_invalid" and exc.reason:
            await audit_now(request.app, actor_user_id=None, actor_name="(доверенное устройство)", action="auth.desktop_resume_denied", target_type="user", target_id="-",
                            ip=ip, result="denied", details={"reason": exc.reason})
        headers = {"Retry-After": "30"} if exc.status == 503 else None
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}, headers=headers) from None
    sess = res.session
    await audit_now(request.app, actor_user_id=uuid.UUID(sess.user_id), actor_name=sess.display_name or sess.sam_account_name, action="auth.desktop_resume", target_type="user",
                    target_id=str(sess.user_id), ip=ip)
    request.app.state.journal.emit("auth", "login_ok", user=sess.sam_account_name, ip=ip, client=parse_client(ua), data={"via": "desktop_trust"})
    s = request.app.state.settings
    out = JSONResponse({**nxt, "user": {"id": sess.user_id, "display_name": sess.display_name}, "must_enroll_totp": sess.must_totp}, headers=_NO_STORE)
    out.set_cookie(s.cookie_name, res.session_id, httponly=True, secure=s.cookie_secure, samesite="lax", path="/")
    return out


@router.post("/forget", status_code=204)
async def forget(request: Request, body: dict = Body(...)):
    """Клиент удаляет организацию или выходит: токен отзывается и на сервере (выход через страницу отзывает его и сам)."""
    _desktop_only(request)
    await request.app.state.sessions.trust_forget(str(body.get("token") or ""))
    return Response(status_code=204)
