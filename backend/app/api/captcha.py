"""Выдача задач локальной CAPTCHA (без входа в систему: страница входа и гостевая страница ещё без сессии)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..auth.deps import client_ip
from ..services import captcha

router = APIRouter(prefix="/captcha", tags=["captcha"])
ISSUE_LIMIT, ISSUE_WINDOW_S = 60, 60                 # задач в минуту с одного IP


@router.get("/challenge")
async def challenge(request: Request):
    r = request.app.state.redis
    key = f"captcha:issue:{client_ip(request)}"
    if await r.incr(key) == 1:
        await r.expire(key, ISSUE_WINDOW_S)
    if int(await r.get(key) or 0) > ISSUE_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много запросов проверки. Подождите минуту.", headers={"Retry-After": str(ISSUE_WINDOW_S)})
    return await captcha.issue(request.app)
