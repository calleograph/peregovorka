"""Единая точка входа в защиту для пути «логин + пароль»: блокировки и лимиты → CAPTCHA (по серверным счётчикам) → замедление. Общая для входа в браузере, панели Outlook и пр."""
from __future__ import annotations

import time

from fastapi import HTTPException, Request

from .loginguard import GuardUnavailable, Verdict

BLOCKED_MESSAGE = "Слишком много неудачных попыток. Повторите позже."


async def login_gate(request: Request, login: str, ip: str, scope: str = "login") -> Verdict:
    from ..services import captcha  # noqa: PLC0415

    guard = request.app.state.login_guard
    try:
        verdict = await guard.gate(ip, login)
    except GuardUnavailable:
        raise HTTPException(status_code=503, detail={"code": "protection_unavailable", "message": "Защита входа временно недоступна. Повторите попытку через минуту."}) from None
    if not verdict.allow:
        await request.app.state.secevents.emit("login_blocked", ip=ip, account=(login or "")[:80], result="blocked", rule=verdict.rule, reason="вход отклонён защитой от перебора",
                                               until=time.time() + verdict.retry_after, component="auth")
        raise HTTPException(status_code=429, detail={"code": "throttled", "message": BLOCKED_MESSAGE}, headers={"Retry-After": str(verdict.retry_after)})
    await captcha.guard(request, scope, needed=verdict.captcha)
    return verdict
