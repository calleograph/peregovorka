"""Слияние источников профиля по настраиваемым приоритетам (чистые функции — без БД и сети)."""
from __future__ import annotations

from .base import FIELDS, SOURCES

# Приоритеты по умолчанию: ФИО и e-mail — каталог; должность, подразделение, телефон — портал, затем каталог; фото — своё, затем портал.
DEFAULT_PRIORITY: dict[str, list[str]] = {
    "display_name": ["ad", "bitrix", "local"],
    "email": ["ad", "bitrix", "local"],
    "title": ["bitrix", "ad", "local"],
    "department": ["bitrix", "ad", "local"],
    "phone": ["bitrix", "ad", "local"],
    "avatar": ["local", "bitrix", "ad"],
}


def parse_priority(text: str, default: list[str]) -> list[str]:
    """«bitrix, ad, local» → список; неизвестные источники отбрасываются, повторы убираются; пусто — значение по умолчанию."""
    out: list[str] = []
    for part in (text or "").replace(";", ",").split(","):
        p = part.strip().lower()
        if p in SOURCES and p not in out:
            out.append(p)
    return out or list(default)


def priorities_from(settings_like: object | None) -> dict[str, list[str]]:
    """Приоритеты из настроек (`priority_<поле>`); отсутствие настроек — значения по умолчанию."""
    out = {k: list(v) for k, v in DEFAULT_PRIORITY.items()}
    if settings_like is None:
        return out
    for key in out:
        raw = getattr(settings_like, f"priority_{key}", "")
        if raw:
            out[key] = parse_priority(raw, out[key])
    return out


def effective(sources: dict[str, dict[str, str]], priority: dict[str, list[str]]) -> dict[str, str]:
    """Итоговые значения: по каждому полю — первый источник из списка приоритета, у которого значение непустое. Поля без значений не возвращаются
    (вызывающий оставляет прежнее значение: временное отсутствие данных не стирает карточку)."""
    out: dict[str, str] = {}
    for f in FIELDS:
        for src in priority.get(f, DEFAULT_PRIORITY[f]):
            val = (sources.get(src) or {}).get(f)
            if val:
                out[f] = val
                break
    return out


def avatar_source(*, has_avatar: bool, current: str | None, priority: list[str], bitrix_available: bool) -> str:
    """Откуда должна быть аватарка: «keep» — оставить как есть, «bitrix» — взять с портала.

    Своё (ручное) фото `current in (None с файлом, "manual")` не перетирается, пока «local» стоит в приоритете раньше «bitrix».
    Если фото нет вообще — берём портал (если он доступен), как бы ни стояли приоритеты."""
    if not bitrix_available:
        return "keep"
    if not has_avatar:
        return "bitrix"
    manual = current in (None, "manual")
    if "bitrix" not in priority:
        return "keep"
    if manual:
        return "bitrix" if "local" not in priority or priority.index("bitrix") < priority.index("local") else "keep"
    return "bitrix"            # уже фото с портала — обновляем по сроку
