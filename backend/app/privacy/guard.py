"""Обязательная защита исходящих запросов к языковым моделям: секреты (пароли, токены, ключи, строки подключения) маскируются всегда."""
from __future__ import annotations

from .secrets import find_secrets

MASK = "[секрет удалён]"


def mask_secrets(text: str) -> tuple[str, int]:
    """(текст без секретов, сколько замаскировано). Пересекающиеся находки объединяются; повторный вызов ничего не меняет."""
    spans = sorted((f.start, f.end) for f in find_secrets(text))
    if not spans:
        return text, 0
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, pos = [], 0
    for s, e in merged:
        out += [text[pos:s], MASK]
        pos = e
    out.append(text[pos:])
    return "".join(out), len(merged)
