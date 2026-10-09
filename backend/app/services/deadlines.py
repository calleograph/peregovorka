"""Сроки в поручениях: проверка, что фраза — действительно срок, и детерминированное превращение её в дату относительно даты встречи.

Модель не вычисляет даты и не придумывает сроки: она может только процитировать фразу из реплики («в четверг к обеду», «до 23 октября»). Код проверяет, что это
срок (день недели, дата, «сегодня/завтра», «к следующей встрече», …), что эти слова есть в репликах (extraction.py), и пытается однозначно перевести фразу в дату.
Чего однозначно перевести нельзя («к следующей встрече», «до конца недели» — если так сказано), остаётся текстом; время окончания встречи («10:19») сроком не является.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
DATE_HEADER_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

WEEKDAYS = (("понедельник", 0), ("вторник", 1), ("сред", 2), ("четверг", 3), ("пятниц", 4), ("суббот", 5), ("воскресень", 6))
MONTH_FORMS = ((r"январ(?:я|ь|е)", 1), (r"феврал(?:я|ь|е)", 2), (r"март(?:а|е)?", 3), (r"апрел(?:я|ь|е)", 4), (r"ма(?:я|й|е)", 5), (r"июн(?:я|ь|е)", 6),
               (r"июл(?:я|ь|е)", 7), (r"август(?:а|е)?", 8), (r"сентябр(?:я|ь|е)", 9), (r"октябр(?:я|ь|е)", 10), (r"ноябр(?:я|ь|е)", 11), (r"декабр(?:я|ь|е)", 12))
MONTH_RE = re.compile(r"\b(" + "|".join(f"(?P<m{n}>{p})" for p, n in MONTH_FORMS) + r")\b")
WEEKDAY_RE = re.compile(r"\b(?:понедельник|вторник|сред(?:а|ы|у|е|ой)|четверг|пятниц(?:а|ы|у|е|ей)|суббот(?:а|ы|у|е|ой)|воскресень(?:е|я))\w*")
RELATIVE_RE = re.compile(r"\b(?:сегодня|завтра\w*|послезавтра|вчера|на\s+(?:этой|следующей|той)\s+неделе|на\s+следующ\w+\s+(?:неделе|встрече)|к\s+следующ\w+\s+(?:встрече|неделе)|"
                         r"до\s+конца\s+(?:недели|месяца|года|дня)|через\s+\w+\s+(?:дн\w+|недел\w+|месяц\w*)|следующ\w+\s+(?:встреч\w+|недел\w+|месяц\w*))")
# порядковые числительные: «двадцать третьего», «пятнадцатое», «тридцатого»
_UNITS = (("перв", 1), ("втор", 2), ("трет", 3), ("четвёрт", 4), ("четверт", 4), ("пят", 5), ("шест", 6), ("седьм", 7), ("восьм", 8), ("девят", 9))
_TEENS = (("десят", 10), ("одиннадцат", 11), ("двенадцат", 12), ("тринадцат", 13), ("четырнадцат", 14), ("пятнадцат", 15), ("шестнадцат", 16), ("семнадцат", 17),
          ("восемнадцат", 18), ("девятнадцат", 19))
_TENS = (("двадцат", 20), ("тридцат", 30))


def norm(s: str) -> str:
    return s.lower().replace("ё", "е").replace("—", "-")


def is_deadline_phrase(phrase: str) -> bool:
    """Фраза содержит слова срока (день недели, месяц/дата, «сегодня/завтра», «к следующей встрече» …) и не является временем суток вида «10:19»."""
    p = norm(phrase)
    if not p.strip():
        return False
    if re.search(r"\bс\s+\S+\s+по\s+\S+", p) and not re.search(r"\b(?:до|к)\s+", p):
        return False                    # «с 7 по 20 июля» — период (отпуск, дежурство), а не срок исполнения
    if CLOCK_RE.search(p) and not (WEEKDAY_RE.search(p) or MONTH_RE.search(p) or RELATIVE_RE.search(p)):
        return False
    if WEEKDAY_RE.search(p) or MONTH_RE.search(p) or RELATIVE_RE.search(p):
        return True
    if re.search(r"\b(?:до|к)\s+\d{1,2}(?:-?го|-?му|\s+числа)?\b", p):        # «до 23», «к 16-му числу»
        return True
    return _day_only(p) is not None                                              # «до пятнадцатого»


def _ordinal_day(words: list[str]) -> int | None:
    """Последние слова перед названием месяца → день месяца («двадцать третьего» → 23, «пятнадцатое» → 15)."""
    def stem(w: str, table) -> int | None:
        w = w.replace("ё", "е")
        for s, v in table:
            if w.startswith(s.replace("ё", "е")):
                return v
        return None

    if not words:
        return None
    last = words[-1]
    if last.isdigit():
        return int(last)
    unit = stem(last, _UNITS)
    if unit is not None and len(words) >= 2:
        tens = stem(words[-2], _TENS)
        if tens is None and words[-2] in ("двадцать", "тридцать"):
            tens = 20 if words[-2] == "двадцать" else 30
        if tens is not None:
            return tens + unit
    for table in (_TEENS, _TENS, _UNITS):
        v = stem(last, table)
        if v is not None:
            return v
    return None


def _day_only(p: str) -> int | None:
    """«до пятнадцатого», «к двадцать третьему»: число месяца без названия месяца."""
    m = re.search(r"\b(?:до|к)\s+((?:[a-zа-я]+\s+){0,1}[a-zа-я]+)\b", p)
    if not m:
        return None
    day = _ordinal_day(m.group(1).split())
    return day if day and 1 <= day <= 31 and not m.group(1).isdigit() else None


def meeting_date(header: list[str]) -> date | None:
    for h in header:
        if h.lower().startswith("дата:"):
            m = DATE_HEADER_RE.search(h)
            if m:
                try:
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    return None
    return None


def resolve(phrase: str, base: date | None) -> date | None:
    """Фраза срока → дата. Только то, что вычисляется однозначно; иначе None (в протоколе остаётся текст фразы)."""
    if base is None or not phrase or not is_deadline_phrase(phrase):
        return None
    p = norm(phrase)
    if re.search(r"\bпослезавтра\b", p):
        return base + timedelta(days=2)
    if re.search(r"\bзавтра\w*", p):
        return base + timedelta(days=1)
    if re.search(r"\bсегодня\b", p):
        return base
    m = MONTH_RE.search(p)
    if m:
        month = next(n for n in range(1, 13) if m.groupdict().get(f"m{n}"))
        words = re.findall(r"[a-zа-я0-9]+", p[:m.start()])
        day = _ordinal_day(words[-2:] if len(words) >= 2 else words)
        if day is None:
            day = _ordinal_day(words[-1:])
        if day and 1 <= day <= 31:
            for year in (base.year, base.year + 1):
                try:
                    d = date(year, month, day)
                except ValueError:
                    continue
                if d >= base - timedelta(days=31):
                    return d
        return None
    day = _day_only(p)
    if day:
        for add in (0, 1):
            y, mth = (base.year, base.month + add) if base.month + add <= 12 else (base.year + 1, 1)
            try:
                d = date(y, mth, day)
            except ValueError:
                continue
            if d >= base:
                return d
    w = WEEKDAY_RE.search(p)
    if w:
        word = w.group(0)
        for stem, idx in WEEKDAYS:
            if word.startswith(stem):
                delta = (idx - base.weekday()) % 7 or 7
                return base + timedelta(days=delta)
    return None
