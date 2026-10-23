"""Серии встреч из календаря Outlook: шаблон повторения и ближайшее занятие (для «Предстоящих встреч»: дата, отсчёт, сортировка).

Шаблон приходит от надстройки (Office.js `item.recurrence`) или из серверной сверки (Microsoft Graph `recurrence`, EWS `Recurrence`) и хранится
в одном виде (`rooms.outlook_recurrence`):

    {"type": "daily" | "weekly" | "monthly" | "monthly_nth" | "yearly" | "yearly_nth", "interval": 1, "days": [0, 2] (пн = 0), "day": 15, "nth": 1..5 (5 — последний),
     "month": 1..12, "start": "ГГГГ-ММ-ДД", "end": "ГГГГ-ММ-ДД" | None, "count": N | None, "time": "ЧЧ:ММ", "minutes": 60}

Время занятий — в часовом поясе организатора (`rooms.outlook_tz`, его присылает надстройка); перенос или отмена отдельного занятия (исключения серии)
не учитываются. Неизвестный или неполный шаблон — None: тогда серия показывается без даты следующего занятия, как раньше.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TYPES = ("daily", "weekly", "monthly", "monthly_nth", "yearly", "yearly_nth")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WEEK = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
         "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
_NTH = {"first": 1, "second": 2, "third": 3, "fourth": 4, "last": 5}
_MONTH = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
HORIZON_DAYS = 800                          # дальше двух лет серию не ищем (так же ограничен срок жизни комнаты серии)


def _days(raw) -> list[int]:
    items = raw if isinstance(raw, list) else str(raw or "").split()
    out = set()
    for d in items:
        if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6:      # уже нормализованный шаблон (пн = 0)
            out.add(d)
            continue
        d = str(d).strip().lower()
        if d in ("weekday",):
            out.update(range(5))
        elif d in _WEEK:
            out.add(_WEEK[d])
    return sorted(out)


def _month(v) -> int | None:
    if isinstance(v, int) and 1 <= v <= 12:
        return v
    s = str(v or "").strip().lower()[:3]
    return _MONTH.get(s) or (int(s) if s.isdigit() and 1 <= int(s) <= 12 else None)


def validate(p) -> dict | None:
    """Проверка и нормализация шаблона на границе доверия (надстройка — клиент). Неверный — None."""
    if not isinstance(p, dict) or p.get("type") not in TYPES:
        return None
    try:
        interval = int(p.get("interval") or 1)
        minutes = int(p.get("minutes") or 0)
    except (TypeError, ValueError):
        return None
    start, end, tm = str(p.get("start") or ""), p.get("end"), str(p.get("time") or "")
    if not (1 <= interval <= 99 and 1 <= minutes <= 24 * 60 and _DATE.match(start) and _TIME.match(tm)):
        return None
    if end is not None and not (isinstance(end, str) and _DATE.match(end)):
        end = None
    count = p.get("count")
    count = int(count) if isinstance(count, int) and 1 <= count <= 999 else None
    out = {"type": p["type"], "interval": interval, "start": start, "end": end, "count": count, "time": tm, "minutes": minutes}
    if p["type"] == "weekly":
        out["days"] = _days(p.get("days"))
        if not out["days"]:
            return None
    if p["type"] in ("monthly", "yearly"):
        day = p.get("day")
        if not (isinstance(day, int) and 1 <= day <= 31):
            return None
        out["day"] = day
    if p["type"] in ("monthly_nth", "yearly_nth"):
        days, nth = _days(p.get("days")), p.get("nth")
        if len(days) != 1 or nth not in (1, 2, 3, 4, 5):
            return None
        out["days"], out["nth"] = days, nth
    if p["type"] in ("yearly", "yearly_nth"):
        out["month"] = _month(p.get("month"))
        if not out["month"]:
            return None
    try:
        date.fromisoformat(start)
    except ValueError:
        return None
    return out


def from_office(r) -> dict | None:
    """Шаблон из надстройки: {type, props, start_date, end_date, start_time, duration} — поля Office.js Recurrence/SeriesTime."""
    if not isinstance(r, dict):
        return None
    kind, props = str(r.get("type") or ""), r.get("props") if isinstance(r.get("props"), dict) else {}
    base = {"interval": props.get("interval") or 1, "start": r.get("start_date"), "end": r.get("end_date"), "time": r.get("start_time"), "minutes": r.get("duration")}
    if kind == "daily":
        return validate({**base, "type": "daily"})
    if kind == "weekday":
        return validate({**base, "type": "weekly", "days": ["weekday"]})
    if kind == "weekly":
        return validate({**base, "type": "weekly", "days": props.get("days")})
    if kind in ("monthly", "yearly"):
        if props.get("dayOfMonth"):
            return validate({**base, "type": kind, "day": props.get("dayOfMonth"), "month": props.get("month")})
        return validate({**base, "type": f"{kind}_nth", "days": [props.get("dayOfWeek")], "nth": _NTH.get(str(props.get("weekNumber") or "")), "month": props.get("month")})
    return None


def from_graph(rec, start_utc: datetime | None, end_utc: datetime | None, local_tz: str | None) -> dict | None:
    """Шаблон Microsoft Graph (patternedRecurrence); время занятия — из начала мастер-события (UTC) в поясе организатора."""
    if not isinstance(rec, dict) or start_utc is None or end_utc is None:
        return None
    pat, rng = rec.get("pattern") or {}, rec.get("range") or {}
    local = start_utc.astimezone(_zone(local_tz))
    kind = {"daily": "daily", "weekly": "weekly", "absoluteMonthly": "monthly", "relativeMonthly": "monthly_nth",
            "absoluteYearly": "yearly", "relativeYearly": "yearly_nth"}.get(str(pat.get("type") or ""))
    if not kind:
        return None
    return validate({"type": kind, "interval": pat.get("interval") or 1, "days": pat.get("daysOfWeek"), "day": pat.get("dayOfMonth"),
                     "nth": _NTH.get(str(pat.get("index") or "")), "month": pat.get("month"), "start": rng.get("startDate") or local.date().isoformat(),
                     "end": rng.get("endDate") if rng.get("type") == "endDate" else None,
                     "count": rng.get("numberOfOccurrences") if rng.get("type") == "numbered" else None,
                     "time": local.strftime("%H:%M"), "minutes": int((end_utc - start_utc).total_seconds() // 60)})


def _zone(name: str | None):
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _last_day(y: int, m: int) -> int:
    return ((date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)).day


def _nth_weekday(y: int, m: int, wd: int, nth: int) -> date:
    if nth == 5:
        d = date(y, m, _last_day(y, m))
        return d - timedelta(days=(d.weekday() - wd) % 7)
    d = date(y, m, 1) + timedelta(days=(wd - date(y, m, 1).weekday()) % 7)
    return d + timedelta(weeks=nth - 1)


def _matches(p: dict, d: date, first: date) -> bool:
    t, n = p["type"], p["interval"]
    if t == "daily":
        return (d - first).days % n == 0
    if t == "weekly":
        weeks = ((d - timedelta(days=d.weekday())) - (first - timedelta(days=first.weekday()))).days // 7
        return d.weekday() in p["days"] and weeks % n == 0
    months = (d.year - first.year) * 12 + d.month - first.month
    if t == "monthly":
        return months % n == 0 and d.day == min(p["day"], _last_day(d.year, d.month))
    if t == "monthly_nth":
        return months % n == 0 and d == _nth_weekday(d.year, d.month, p["days"][0], p["nth"])
    years = d.year - first.year
    if t == "yearly":
        return years % n == 0 and d.month == p["month"] and d.day == min(p["day"], _last_day(d.year, d.month))
    return years % n == 0 and d.month == p["month"] and d == _nth_weekday(d.year, d.month, p["days"][0], p["nth"])     # yearly_nth


def next_occurrence(p: dict | None, tz_name: str | None, now: datetime) -> tuple[datetime, datetime] | None:
    """Ближайшее занятие серии, которое ещё не закончилось (идущее сейчас — тоже): (начало, окончание) в UTC. None — серия закончилась или шаблона нет."""
    p = validate(p) if p else None
    if p is None:
        return None
    tz = _zone(tz_name)
    first = date.fromisoformat(p["start"])
    last = date.fromisoformat(p["end"]) if p["end"] else None
    hh, mm = map(int, p["time"].split(":"))
    dur = timedelta(minutes=p["minutes"])
    today = now.astimezone(tz).date()
    # с подсчётом занятий («N раз») идём от начала серии, иначе — со вчерашнего дня (занятие могло начаться вчера и ещё идти)
    d, seen = (first if p["count"] else max(first, today - timedelta(days=1))), 0
    stop = today + timedelta(days=HORIZON_DAYS)
    while d <= stop and (last is None or d <= last):
        if _matches(p, d, first):
            seen += 1
            if p["count"] and seen > p["count"]:
                return None
            start = datetime.combine(d, time(hh, mm), tz).astimezone(timezone.utc)
            if start + dur > now:
                return start, start + dur
        d += timedelta(days=1)
    return None
