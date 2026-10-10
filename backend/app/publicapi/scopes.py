"""Права (scopes) публичного API. Каждый перечисленный scope реально проверяется каким-то маршрутом."""
from __future__ import annotations

SCOPES: dict[str, str] = {
    "rooms:read": "Список и карточки комнат",
    "meetings:read": "Список и карточки встреч, участники и их подключения",
    "meetings:end": "Завершить идущую встречу",
    "transcripts:read": "Стенограмма встреч (JSON, TXT, Markdown, VTT, SRT)",
    "protocols:read": "Читать и выгружать протоколы",
    "summaries:read": "Читать и выгружать резюме",
    "maps:read": "Читать карты разговора",
    "recordings:read": "Метаданные аудиозаписей",
    "messages:read": "Сообщения чата встречи",
}

# Классы ограничения частоты: чтение, изменения, тяжёлые операции (языковая модель), скачивание
RATE_CLASSES = ("read", "write", "ai", "download")


def valid_scopes(values: list[str]) -> list[str]:
    bad = [v for v in values if v not in SCOPES]
    if bad:
        raise ValueError("Неизвестные права: " + ", ".join(bad))
    return sorted(set(values))
