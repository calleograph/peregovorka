"""Администрирование → Безопасность → CAPTCHA → «Проверить CAPTCHA»: тестовая выдача и проверка ТОЙ ЖЕ задачей, что на странице входа
(`security/imgcaptcha`, `services/captcha`), но с отдельным назначением `test`: такая задача не принимается ни при входе, ни при входе гостя, ни при создании
переговорки (проверка назначения), поэтому обойти ею CAPTCHA нельзя. Счётчики неудачных входов, блокировки и события защиты входа не затрагиваются.
Только администратор (CSRF проверяет `require_admin`), свой лимит запросов, каждая проверка — в аудит; правильный ответ нигде не возвращается."""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_admin
from ..security import imgcaptcha
from ..services import captcha
from ..services.audit import write_audit

router = APIRouter(prefix="/admin/security/captcha-test", tags=["admin-security"])
SCOPE = "test"
PER_MINUTE = 30
VERDICTS = {
    "passed": ("ok", "Проверка пройдена."),
    "wrong": ("fail", "Неверный ответ. Картинка использована, ниже новая."),
    "expired": ("fail", "Срок проверки истёк или она уже использована. Ниже новая."),
    "context": ("fail", "Проверка выдана для другого адреса или назначения."),
    "missing": ("fail", "Введите символы с картинки."),
    "malformed": ("fail", "Ответ повреждён."), "bad_signature": ("fail", "Подпись задачи не совпала."),
    "bad_solution": ("fail", "Решение неверное."), "replay": ("fail", "Это решение уже использовано."),
}


async def _limit(request: Request, su: SessionUser) -> None:
    r = request.app.state.redis
    key = f"captcha:test:{su.user_id}"
    if await r.incr(key) == 1:
        await r.expire(key, 60)
    if int(await r.get(key) or 0) > PER_MINUTE:
        raise HTTPException(status_code=429, detail="Слишком много проверок. Подождите минуту.", headers={"Retry-After": "60"})


async def _diag(request: Request, cfg) -> dict:
    """Фактическая активная конфигурация и состояние того, от чего зависит CAPTCHA: хранилище задач (Redis) и шрифт картинки."""
    redis_ok, redis_err = True, ""
    try:
        await request.app.state.redis.ping()
    except Exception as exc:  # noqa: BLE001
        redis_ok, redis_err = False, type(exc).__name__
    font = "DejaVu"
    if cfg.kind == "image":
        from PIL import ImageFont  # noqa: PLC0415

        font = "DejaVu"
        for p in imgcaptcha._FONTS:                            # noqa: SLF001
            try:
                ImageFont.truetype(p, 20)
                break
            except OSError:
                continue
        else:
            font = "встроенный запасной (картинка может выглядеть иначе)"
    return {"mode": cfg.mode, "kind": cfg.kind, "length": cfg.length, "ttl_seconds": cfg.ttl_seconds, "failures": cfg.failures, "window_minutes": cfg.window_minutes,
            "difficulty": cfg.difficulty, "redis": redis_ok, "redis_error": redis_err or None, "font": font if cfg.kind == "image" else None,
            "scopes": ["вход", "вход гостя по ссылке", "создание временной переговорки"],
            "note": "Проверка использует тот же код и хранилище, что страница входа, но задача действительна только здесь: на вход она не принимается и счётчики входа не меняет."}


@router.post("/challenge")
async def test_challenge(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await _limit(request, su)
    cfg = await captcha._cfg(request.app)                      # noqa: SLF001
    diag = await _diag(request, cfg)
    ip = client_ip(request)
    if not diag["redis"]:
        raise HTTPException(status_code=503, detail={"code": "captcha_unavailable", "message": "Хранилище проверок (Redis) недоступно: CAPTCHA выдать нельзя, вход при этом закрыт.", "diag": diag})
    try:
        if cfg.kind == "image":
            ch = await imgcaptcha.issue(request.app.state.redis, ip, SCOPE, ttl_s=cfg.ttl_seconds, length=cfg.length)
        else:
            ch = {"kind": "pow", **captcha.make_challenge(cfg.difficulty, cfg.ttl_seconds)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={"code": "captcha_unavailable", "message": f"Не удалось создать проверку ({type(exc).__name__}). Подробности в журнале сервера.", "diag": diag}) from None
    return {"challenge": ch, "diag": diag}


@router.post("/verify")
async def test_verify(request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Проверка ответа. Результат — человеческое сообщение; причина отказа — код (wrong/expired/context/missing/…). Однократно: задача удаляется при любой проверке."""
    await _limit(request, su)
    cfg = await captcha._cfg(request.app)                      # noqa: SLF001
    ip = client_ip(request)
    err: str | None
    try:
        if cfg.kind == "image":
            err = await imgcaptcha.verify(request.app.state.redis, str(body.get("id") or ""), str(body.get("answer") or ""), ip, SCOPE)
        else:
            err, sig, left = captcha.check_payload(str(body.get("solution") or ""))
            if err is None and not await request.app.state.redis.set(f"captcha:used:{sig}", "1", nx=True, ex=left + 5):
                err = "replay"
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={"code": "captcha_unavailable", "message": "Ошибка сервиса: не удалось проверить ответ (хранилище недоступно)."}) from None
    reason = err or "passed"
    level, message = VERDICTS.get(reason, ("fail", "Проверка не пройдена."))
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="captcha.test", target_type="captcha", target_id=SCOPE, ip=ip,
                      result="success" if err is None else "denied", details={"reason": reason, "kind": cfg.kind, "id_hash": hashlib.sha256(str(body.get("id") or "").encode()).hexdigest()[:12]})
    await db.commit()
    return {"ok": err is None, "reason": reason, "level": level, "message": message}
