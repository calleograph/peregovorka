"""Публичные идентификаторы с типом: `mtg_<32 hex>`. Чужой префикс не принимается."""
from __future__ import annotations

import uuid

PREFIX = {"room": "rom", "meeting": "mtg", "protocol": "pro", "map": "map", "recording": "rec", "job": "job", "user": "usr", "guest": "gst", "message": "msg"}


def pub(kind: str, value: uuid.UUID | int) -> str:
    if isinstance(value, int):
        return f"{PREFIX[kind]}_{value:08d}"
    return f"{PREFIX[kind]}_{value.hex}"


def parse(kind: str, text: str) -> uuid.UUID | None:
    """UUID из публичного идентификатора нужного типа; иначе None (вызывающий отвечает 404: не различаем «не тот формат» и «нет такого»)."""
    pre = PREFIX[kind] + "_"
    if not isinstance(text, str) or not text.startswith(pre) or len(text) != len(pre) + 32:
        return None
    try:
        return uuid.UUID(hex=text[len(pre):])
    except ValueError:
        return None
