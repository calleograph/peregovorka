"""Общие типы: поля профиля, результат источника, интерфейс провайдера."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Поля, которые могут приходить из разных источников и объединяются по приоритетам.
FIELDS = ("display_name", "email", "title", "department", "phone")
SOURCES = ("ad", "bitrix", "local")
LIMITS = {"display_name": 300, "email": 320, "title": 300, "department": 300, "phone": 64}


@dataclass
class SourceProfile:
    """Что один источник знает о человеке. Пустые поля — «не знает» (не «очистить»)."""

    fields: dict[str, str] = field(default_factory=dict)
    external_id: str | None = None
    photo: bytes | None = None          # уже скачанная картинка (обработка и сохранение — на стороне сервиса)
    photo_url: str | None = None


class ProfileProvider(Protocol):
    """Дополнительный источник профилей. `fetch` не должен бросать исключений наружу сервиса: сбой = None/ProviderError."""

    name: str

    async def fetch(self, *, email: str | None, external_id: str | None) -> SourceProfile | None: ...


class ProviderError(Exception):
    """Источник временно недоступен или ответил некорректно (короткий безопасный текст — для журнала)."""


def clean(field_name: str, value: object) -> str | None:
    text = " ".join(str(value or "").split())
    return text[: LIMITS.get(field_name, 300)] or None
