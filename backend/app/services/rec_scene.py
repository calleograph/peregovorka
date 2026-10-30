"""Общая сцена видеозаписи: что видит страница-рекордер (шаблон Egress).

Egress открывает в своём браузере нашу страницу `/rec/template`, которая входит в комнату LiveKit и рисует сцену встречи. Страница не пользователь и не гость:
у неё нет сеанса, поэтому доступ к состоянию — по короткоживущему подписанному токену ОДНОЙ встречи (`rt`). Токен выдаётся при старте записи, ничего не даёт кроме
чтения того, что и так видят участники: общую сцену ведущего, признак активности на доске и саму схему.

Что считается общей сценой (по убыванию приоритета; решает страница по этим данным, см. frontend/src/recorder/scene.ts):
  1. Явная сцена ведущего («Показать всем»: камера, экран, доска) — её видят все участники и так же пишет запись.
  2. Показ экрана любым участником (как раньше в раскладке «speaker»).
  3. Совместная работа над доской: правка схемы любым участником за последние BOARD_ACTIVE_S секунд — доска крупно, пока работа идёт, затем возврат к участникам.
  4. Обычная конференция: говорящий и миниатюры.
Локальные закрепления и открытые панели отдельных участников на запись не влияют: сервер их не знает.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from ..config import Settings

BOARD_ACTIVE_S = 25                 # столько секунд после последней правки схемы доска считается «в работе»
TOKEN_TTL_S = 12 * 3600             # запись одной встречи; новый старт (фрагмент) выдаёт новый токен
PATCH_TAIL = 400


def _key(settings: Settings) -> bytes:
    master = (settings.app_master_key or settings.livekit_api_secret or "").encode()
    return hmac.new(master, b"peregovorka/recorder-scene/v1", hashlib.sha256).digest()


def make_token(settings: Settings, meeting_id: uuid.UUID, *, now: float | None = None, ttl: int = TOKEN_TTL_S) -> str:
    exp = int((now if now is not None else time.time()) + ttl)
    body = f"{meeting_id.hex}.{exp}"
    sig = base64.urlsafe_b64encode(hmac.new(_key(settings), body.encode(), hashlib.sha256).digest()[:18]).decode().rstrip("=")
    return f"{body}.{sig}"


def check_token(settings: Settings, token: str, *, now: float | None = None) -> uuid.UUID | None:
    """Встреча, которой принадлежит токен, или None (подпись неверна / срок вышел / мусор)."""
    try:
        mid, exp, sig = token.split(".")
        want = base64.urlsafe_b64encode(hmac.new(_key(settings), f"{mid}.{exp}".encode(), hashlib.sha256).digest()[:18]).decode().rstrip("=")
        if not hmac.compare_digest(sig, want) or int(exp) < (now if now is not None else time.time()):
            return None
        return uuid.UUID(hex=mid)
    except (ValueError, TypeError):
        return None


def board_touch_key(meeting_id: uuid.UUID) -> str:
    return f"board:last:{meeting_id}"


async def mark_board_activity(redis, meeting_id: uuid.UUID) -> None:
    """Вызывается при каждой правке схемы: по этой отметке страница-рекордер решает, показывать ли доску."""
    await redis.set(board_touch_key(meeting_id), f"{time.time():.3f}", ex=3600)


async def board_age_s(redis, meeting_id: uuid.UUID) -> float | None:
    raw = await redis.get(board_touch_key(meeting_id))
    if not raw:
        return None
    try:
        return max(0.0, time.time() - float(raw))
    except ValueError:
        return None


def scene_payload(*, stage: dict, board_age: float | None, board: dict, ended: bool) -> dict:
    """Снимок для страницы-рекордера (чистая функция: проверяется тестами без Redis и БД)."""
    return {"ended": ended, "stage": {"items": list(stage.get("items") or []), "at": stage.get("at")},
            "board": {"active": board_age is not None and board_age <= BOARD_ACTIVE_S and not ended, "age_s": None if board_age is None else round(board_age, 1),
                      "seq": board.get("seq", 0), "xml": board.get("xml"), "patches": board.get("patches", [])},
            "now": time.time()}


def dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
