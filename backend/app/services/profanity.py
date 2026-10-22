"""Маскирование нецензурной лексики в ТЕКСТЕ стенограммы (настройка комнаты «Скрывать нецензурную лексику в стенограмме», по умолчанию включена).

Хранится исходный текст распознавания; маскируется только выдача: живая стенограмма, история, субтитры, выгрузки, письма, материалы для протокола. Поэтому смена
настройки сразу действует и для старых стенограмм, без повторного распознавания. Слово заменяется звёздочками той же длины; время, говорящий и остальной текст не меняются.

Производительность: правила собраны в одно регулярное выражение один раз при загрузке; обычная реплика проходит быструю предпроверку по корням (без разбора слов)
и возвращается как есть. Внешних запросов, моделей и очередей нет; обработка — микросекунды на реплику (замер в тестах).
Словарь — `app/assets/profanity_ru.txt` (см. формат там); новые слова и исключения добавляются без изменения кода.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

DICT = Path(__file__).resolve().parent.parent / "assets" / "profanity_ru.txt"
_HOMO = str.maketrans({"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у", "k": "к", "m": "м", "t": "т", "h": "н", "b": "в", "ё": "е"})
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_REPEAT = re.compile(r"(.)\1{2,}")
# предпроверка: в тексте есть хотя бы один корень (по нормализованной строке той же длины)
_QUICK = re.compile(r"ху[йеяюи]|п[иея]зд|еб|бля|муд|манд|залуп|г[ао]ндон|пид|пед|сук|суч|шлюх|дроч|хер")


def _norm(word: str) -> str:
    return _REPEAT.sub(r"\1", word.lower().translate(_HOMO))


@lru_cache(maxsize=1)
def _rules() -> tuple[re.Pattern, frozenset[str]]:
    prefixes: dict[str, str] = {}
    parts: list[str] = []
    exceptions: set[str] = set()
    for raw in DICT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key.startswith("prefix "):
            prefixes[key.split()[1]] = value
        elif key == "match":
            parts.append("(?:" + re.sub(r"\{(\w+)\}", lambda m: prefixes[m.group(1)], value) + ")")
        elif key == "except":
            exceptions.update(_norm(w) for w in value.split())
    return re.compile("|".join(parts)), frozenset(exceptions)


def is_profane(word: str) -> bool:
    n = _norm(word)
    rules, exceptions = _rules()
    return n not in exceptions and rules.search(n) is not None


def mask(text: str | None) -> str | None:
    """Текст со скрытыми нецензурными словами (звёздочки той же длины). Без совпадений возвращается тот же объект строки."""
    if not text or not _QUICK.search(text.lower().translate(_HOMO)):
        return text
    return _WORD.sub(lambda m: "*" * len(m.group()) if is_profane(m.group()) else m.group(), text)


def maybe(text: str | None, enabled: bool) -> str | None:
    return mask(text) if enabled else text
