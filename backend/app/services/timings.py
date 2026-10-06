"""Метрики времени подключения: где именно теряются секунды при входе в комнату.

Источники пишут в Redis-списки `timings:<имя>` (последние 200 значений, живут сутки):
  join_backend_ms        — backend: обработка POST /join (БД + токен), без сети и прокси;
  join_api_ms            — браузер: полный круг POST /join (сеть + прокси + backend);
  signaling_connect_ms   — браузер: от room.connect до установленного сигналинга LiveKit;
  ice_connect_ms         — браузер: от сигналинга до готового медиасоединения (ICE/DTLS);
  participant_active_ms  — браузер: от нажатия «Войти» до состояния «подключено» в комнате;
  microphone_publish_ms  — браузер: от запроса микрофона до публикации трека;
  asr_join_ms            — ASR: от команды старта сессии до входа воркера в комнату LiveKit;
  room_create_ms         — браузер: от ответа /join до созданного объекта Room;
  livekit_connect_ms     — браузер: весь Room.connect (сигналинг + ICE);
  get_user_media_ms      — браузер: getUserMedia микрофона (включая запрос разрешения), идёт ПАРАЛЛЕЛЬНО подключению;
  backend_ws_connect_ms  — браузер: открытие WebSocket событий приложения (независимо от LiveKit);
  total_join_ms          — браузер: от нажатия «Войти» до состояния «подключено»;
  asr_first_segment_ms   — ASR: от входа воркера до первой опубликованной реплики встречи.
Сравнение этих средних отделяет тормоз backend от прокси, сигналинга, ICE, публикации медиа и ASR.
"""
from __future__ import annotations

import math

from redis.asyncio import Redis

NAMES = ("join_backend_ms", "join_api_ms", "signaling_connect_ms", "ice_connect_ms", "participant_active_ms",
         "microphone_publish_ms", "asr_join_ms", "asr_first_segment_ms",
         "room_create_ms", "livekit_connect_ms", "get_user_media_ms", "backend_ws_connect_ms", "total_join_ms")
KEEP = 200
TTL_SECONDS = 86400
MAX_MS = 600_000


def key(name: str) -> str:
    return f"timings:{name}"


async def record(redis: Redis, name: str, ms: float | int | None) -> None:
    """Записать одно измерение; значения вне разумных границ и неизвестные имена игнорируются."""
    if name not in NAMES or ms is None:
        return
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return
    if not (0 <= v <= MAX_MS) or math.isnan(v):
        return
    k = key(name)
    await redis.lpush(k, f"{v:.0f}")
    await redis.ltrim(k, 0, KEEP - 1)
    await redis.expire(k, TTL_SECONDS)


def _stats(values: list[float]) -> dict:
    values = sorted(values)
    n = len(values)
    return {"n": n, "avg": round(sum(values) / n), "p95": round(values[min(n - 1, int(n * 0.95))]), "max": round(values[-1])}


async def averages(redis: Redis) -> dict[str, dict | None]:
    """Среднее, p95 и максимум по каждой метрике; None — измерений ещё не было."""
    out: dict[str, dict | None] = {}
    for name in NAMES:
        raw = await redis.lrange(key(name), 0, KEEP - 1)
        vals = []
        for x in raw:
            try:
                vals.append(float(x))
            except ValueError:
                continue
        out[name] = _stats(vals) if vals else None
    return out
