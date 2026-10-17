"""Сквозное шифрование (E2EE) звука и видео встречи на штатных возможностях LiveKit.

Схема: при начале встречи в комнате с режимом `full` или `trusted` сервер Peregovorka создаёт общий ключ встречи и выдаёт его каждому вошедшему участнику (пользователю и гостю)
вместе с токеном; браузеры шифруют и расшифровывают звук и видео сами (Insertable Streams / RTCRtpScriptTransform), сервер LiveKit пересылает зашифрованные пакеты и
содержимого не видит. Ключ действует одну встречу и после её завершения удаляется; хранится в Redis (не в базе и не в резервных копиях).

Режимы комнаты (`rooms.e2ee_mode`):
* `off` — E2EE нет;
* `full` — полное E2EE: у сервера ключа нет в служебных компонентах, поэтому серверная транскрибация и запись звука для встречи недоступны;
* `trusted` — E2EE с явно разрешённым доверенным компонентом: служба распознавания речи входит в комнату как скрытый участник и получает ключ встречи, расшифровывает звук
  у себя (стенограмма и запись работают); ключ при этом передаётся и через Redis — это явно сообщается пользователям.
Чат, доска и сигналы встречи идут через сервер Peregovorka по TLS и E2EE в этом смысле не покрываются (их защита при хранении — отдельный режим комнаты).
Ключ — случайная строка из 256 бит: клиенты (JS) и служба (Python) выводят из неё ключи одинаково (PBKDF2, совместимо между SDK LiveKit).
"""
from __future__ import annotations

import json
import secrets

from ..models import Room

MODES = ("off", "full", "trusted")
TTL_S = 3 * 86400            # запасной срок: ключ удаляется при завершении встречи, TTL страхует от «осиротевших» ключей


def key_name(meeting_id) -> str:
    return f"e2ee:{meeting_id}"


async def begin(redis, room: Room, meeting_id) -> dict | None:
    """Создать ключ встречи, если у комнаты включено E2EE. Повторный вызов для той же встречи возвращает прежний ключ."""
    if room.e2ee_mode not in ("full", "trusted"):
        return None
    existing = await get(redis, meeting_id)
    if existing is not None:
        return existing
    info = {"key": secrets.token_urlsafe(32), "mode": room.e2ee_mode}
    if not await redis.set(key_name(meeting_id), json.dumps(info), nx=True, ex=TTL_S):          # гонка двух первых входящих: победитель один
        return await get(redis, meeting_id)
    return info


async def get(redis, meeting_id) -> dict | None:
    raw = await redis.get(key_name(meeting_id))
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) and d.get("key") and d.get("mode") in ("full", "trusted") else None
    except ValueError:
        return None


async def drop(redis, meeting_id) -> None:
    await redis.delete(key_name(meeting_id))


def blocks_server_features(info: dict | None) -> bool:
    """Полное E2EE: серверная транскрибация и запись звука невозможны (у сервера нет ключа)."""
    return bool(info) and info["mode"] == "full"


def asr_key(info: dict | None) -> str | None:
    """Ключ для службы распознавания — только в режиме с доверенным компонентом."""
    return info["key"] if info and info["mode"] == "trusted" else None
