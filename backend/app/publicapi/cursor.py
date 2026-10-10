"""Непрозрачные курсоры постраничного вывода (keyset): позиция в упорядоченной выборке, а не номер страницы — устойчиво к добавлению записей."""
from __future__ import annotations

import base64
import json

from .errors import ApiError


def encode(*parts: str | int) -> str:
    raw = json.dumps(list(parts), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(token: str, n: int) -> list:
    """Разбор курсора; любая порча — 400 `invalid_cursor` (а не 500)."""
    try:
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if isinstance(data, list) and len(data) == n and all(isinstance(x, (str, int)) for x in data):
            return data
    except (ValueError, TypeError):
        pass
    raise ApiError(400, "invalid_cursor", "Курсор недействителен: начните выборку сначала.")
