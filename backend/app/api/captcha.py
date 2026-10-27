"""Выдача задач CAPTCHA (без входа в систему: страница входа и гостевая страница ещё без сессии)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..auth.deps import client_ip
from ..security.imgcaptcha import Throttled
from ..services import captcha

router = APIRouter(prefix="/captcha", tags=["captcha"])
SCOPES = ("login", "guest", "temp_room")


@router.get("/challenge")
async def challenge(request: Request, scope: str = Query("login")):
    """Новая задача (картинка или вычислительная — по настройке). Привязана к адресу клиента и назначению; выдача ограничена по адресу."""
    if scope not in SCOPES:
        scope = "login"
    try:
        return await captcha.issue(request.app, client_ip(request), scope)
    except Throttled:
        raise HTTPException(status_code=429, detail="Слишком много запросов проверки. Подождите минуту.", headers={"Retry-After": "60"}) from None
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Проверка «я не робот» временно недоступна. Повторите попытку позже.") from None
