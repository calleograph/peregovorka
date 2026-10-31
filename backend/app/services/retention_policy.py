"""Действующие сроки хранения материалов комнаты: свой срок комнаты или общий срок сервера (группа настроек general).

None — бессрочно; 0 — удалять сразу после обработки; N — дней после завершения встречи (текст) или создания записи (аудио, видео).
Срок публичной ссылки на запись эфира (broadcasts.access_until) — отдельная вещь: истечение ссылки файл не удаляет.
"""
from __future__ import annotations

from datetime import datetime, timezone

KINDS = ("text", "audio", "video")


def effective(room, general) -> dict:
    """{'text': дни|None, 'audio': …, 'video': …, 'custom': {'text': bool, …}} — что действует для этой комнаты сейчас."""
    out: dict = {"custom": {}}
    for k in KINDS:
        custom = bool(getattr(room, f"{k}_retention_custom", False))
        out["custom"][k] = custom
        out[k] = getattr(room, f"{k}_retention_days") if custom else getattr(general, f"default_{k}_retention_days")
    return out


def inherited_paused(general, now: datetime | None = None) -> bool:
    """Общие сроки пока не применяются (запас после смены политики — general.retention_cleanup_from)."""
    raw = (getattr(general, "retention_cleanup_from", "") or "").strip()
    if not raw:
        return False
    try:
        start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) < start
