"""Локальная CAPTCHA на доказательстве работы (формат ALTCHA, внешних сервисов нет).

Сервер выдаёт задачу: подобрать число `n`, для которого SHA-256(salt + n) равен `challenge`. Подпись `signature = HMAC(ключ, challenge)` не даёт подделать
задачу, срок жизни зашит в `salt` (`?expires=…`), а решённая задача принимается один раз (запись в Redis до истечения срока). Сложность — верхняя граница
числа `maxnumber` (настройка). Задача решается браузером автоматически за доли секунды и заметно дороже для перебора паролей ботом.

Режимы: off — не нужна; after_failures — после N неудачных попыток входа с одного IP за окно; always — всегда. Проверяются вход, вход гостя по ссылке и создание
временной переговорки; режим по числу неудач считает только вход и пароли комнат (там есть «неудача»), для остальных он эквивалентен off.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

from fastapi import HTTPException, Request

log = logging.getLogger("app.captcha")

HEADER = "x-captcha"
_key: bytes | None = None
_ALGO = "SHA-256"


def set_captcha_key(master_key_b64: str | None) -> None:
    """Ключ подписи задач — производный от APP_MASTER_KEY; без него — случайный на процесс (задачи не переживут перезапуск)."""
    global _key
    try:
        raw = base64.b64decode(master_key_b64 or "", validate=True)
    except (ValueError, TypeError):
        raw = b""
    _key = hmac.new(raw, b"peregovorka-captcha-v1", hashlib.sha256).digest() if raw else secrets.token_bytes(32)


def _sig(challenge: str) -> str:
    return hmac.new(_key or b"", challenge.encode(), hashlib.sha256).hexdigest()


def make_challenge(maxnumber: int, ttl_s: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    salt = f"{secrets.token_hex(12)}?expires={int(now) + ttl_s}"
    number = secrets.randbelow(maxnumber + 1)
    challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
    return {"algorithm": _ALGO, "challenge": challenge, "salt": salt, "signature": _sig(challenge), "maxnumber": maxnumber}


def check_payload(raw: str, now: float | None = None) -> tuple[str | None, str | None, int]:
    """(ошибка | None, подпись для защиты от повтора, оставшийся срок в секундах). Коды ошибок: malformed, bad_signature, bad_solution, expired."""
    now = time.time() if now is None else now
    try:
        p = json.loads(base64.b64decode(raw, validate=True))
        challenge, salt, number, signature = str(p["challenge"]), str(p["salt"]), int(p["number"]), str(p["signature"])
        if p.get("algorithm") != _ALGO or number < 0:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return "malformed", None, 0
    if not hmac.compare_digest(signature, _sig(challenge)):
        return "bad_signature", None, 0
    if not hmac.compare_digest(hashlib.sha256(f"{salt}{number}".encode()).hexdigest(), challenge):
        return "bad_solution", None, 0
    try:
        expires = int(salt.split("?expires=", 1)[1])
    except (IndexError, ValueError):
        return "malformed", None, 0
    if expires < now:
        return "expired", None, 0
    return None, signature, max(1, int(expires - now))


# ----------------------------------------------------------------------------------------------- применение в запросах
def _ip(request: Request) -> str:
    from ..auth.deps import client_ip  # noqa: PLC0415

    return client_ip(request)


async def _cfg(app):
    async with app.state.session_maker() as db:
        return await app.state.settings_svc.get(db, "captcha")


async def required(app, scope: str, ip: str) -> bool:
    cfg = await _cfg(app)
    if cfg.mode == "always":
        return True
    if cfg.mode == "after_failures" and scope in ("login", "guest"):
        return int(await app.state.redis.get(f"captcha:fail:{scope}:{ip}") or 0) >= cfg.failures
    return False


async def issue(app) -> dict:
    cfg = await _cfg(app)
    return make_challenge(cfg.difficulty, cfg.ttl_seconds)


async def guard(request: Request, scope: str) -> None:
    """Пропускает запрос с решённой задачей (заголовок X-Captcha); иначе отвечает 428 с новой задачей. Повторное использование решения и просроченное решение — отказ."""
    app, ip = request.app, _ip(request)
    if not await required(app, scope, ip):
        return
    raw = request.headers.get(HEADER, "")
    err: str | None = "missing"
    if raw:
        err, sig, left = check_payload(raw)
        if err is None and not await app.state.redis.set(f"captcha:used:{sig}", "1", nx=True, ex=left + 5):
            err = "replay"
    if err is None:
        return
    if err != "missing":                                   # «нет решения» — штатный первый шаг; всё прочее — подозрительно и пишется в аудит
        from .audit import audit_now  # noqa: PLC0415

        await audit_now(app, actor_user_id=None, actor_name="(CAPTCHA)", action="captcha.rejected", target_type="captcha", target_id=scope, ip=ip, result="denied",
                        actor_type="user", details={"reason": err})
    raise HTTPException(status_code=428, detail={"code": "captcha_required", "message": "Подтвердите, что вы не робот: выполняется проверка.", "reason": err,
                                                 "challenge": await issue(app)})


async def note_failure(request: Request, scope: str) -> None:
    cfg = await _cfg(request.app)
    if cfg.mode != "after_failures":
        return
    key = f"captcha:fail:{scope}:{_ip(request)}"
    r = request.app.state.redis
    if await r.incr(key) == 1:
        await r.expire(key, cfg.window_minutes * 60)


async def note_success(request: Request, scope: str) -> None:
    await request.app.state.redis.delete(f"captcha:fail:{scope}:{_ip(request)}")
