"""Серии встреч: разбор шаблона повторения (надстройка, Graph) и ближайшее занятие в поясе организатора."""
from __future__ import annotations

from datetime import datetime, timezone

from app.services import recurrence as rc

U = timezone.utc
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=U)          # пятница


def nxt(p, now=NOW, tz="UTC"):
    r = rc.next_occurrence(p, tz, now)
    return (r[0].strftime("%Y-%m-%d %H:%M"), r[1].strftime("%H:%M")) if r else None


def base(**kw):
    return {"interval": 1, "start": "2026-09-01", "end": None, "count": None, "time": "10:00", "minutes": 60, **kw}


def test_daily_weekly_weekday_and_interval():
    assert nxt(base(type="daily")) == ("2026-10-24 10:00", "11:00")
    assert nxt(base(type="daily", time="12:30")) == ("2026-10-23 12:30", "13:30")
    assert nxt(base(type="daily", time="11:30")) == ("2026-10-23 11:30", "12:30"), "идущее сейчас занятие — ещё «предстоящее»"
    assert nxt(base(type="weekly", days=["monday", "wednesday"])) == ("2026-10-26 10:00", "11:00")
    assert nxt(base(type="weekly", days=["weekday"])) == ("2026-10-26 10:00", "11:00")
    # раз в две недели по вторникам с 1 сентября (вт): 1.09, 15.09, 29.09, 13.10, 27.10
    assert nxt(base(type="weekly", interval=2, days=["tuesday"])) == ("2026-10-27 10:00", "11:00")


def test_monthly_yearly_nth_and_short_months():
    assert nxt(base(type="monthly", day=15)) == ("2026-11-15 10:00", "11:00")
    assert nxt(base(type="monthly", day=31), now=datetime(2026, 11, 2, tzinfo=U)) == ("2026-11-30 10:00", "11:00"), "31-е в ноябре — последний день"
    assert nxt(base(type="monthly_nth", days=["thursday"], nth=5)) == ("2026-10-29 10:00", "11:00"), "последний четверг октября"
    assert nxt(base(type="monthly_nth", days=["monday"], nth=1)) == ("2026-11-02 10:00", "11:00")
    assert nxt(base(type="yearly", day=1, month=3)) == ("2027-03-01 10:00", "11:00")
    assert nxt(base(type="yearly_nth", days=["friday"], nth=4, month=11)) == ("2026-11-27 10:00", "11:00")


def test_series_end_by_date_and_by_count():
    assert nxt(base(type="daily", end="2026-10-22")) is None, "серия закончилась"
    assert nxt(base(type="daily", start="2026-10-20", count=3)) is None, "20, 21, 22 — три занятия уже прошли"
    assert nxt(base(type="daily", start="2026-10-20", count=5)) == ("2026-10-24 10:00", "11:00")


def test_time_zone_of_organizer_and_dst():
    assert nxt(base(type="daily"), tz="Europe/Moscow") == ("2026-10-24 07:00", "08:00"), "10:00 по Москве = 07:00 UTC"
    # Нью-Йорк: 1 ноября 2026 — переход на зимнее время; 10:00 местного — 14:00 UTC до и 15:00 UTC после
    assert nxt(base(type="daily"), now=datetime(2026, 10, 31, 15, 0, tzinfo=U), tz="America/New_York") == ("2026-11-01 15:00", "16:00")
    assert nxt(base(type="daily"), tz="Нет/Такого") == ("2026-10-24 10:00", "11:00"), "неизвестный пояс — UTC, без ошибки"


def test_office_and_graph_patterns_are_normalized_and_garbage_rejected():
    office = {"type": "weekly", "props": {"interval": 1, "days": ["mon", "thu"]}, "start_date": "2026-10-01", "end_date": None, "start_time": "09:30", "duration": 45}
    assert rc.from_office(office) == {"type": "weekly", "interval": 1, "start": "2026-10-01", "end": None, "count": None, "time": "09:30", "minutes": 45, "days": [0, 3]}
    assert rc.from_office({**office, "type": "monthly", "props": {"interval": 1, "dayOfWeek": "fri", "weekNumber": "last"}})["type"] == "monthly_nth"
    g = {"pattern": {"type": "relativeMonthly", "interval": 1, "daysOfWeek": ["thursday"], "index": "last"},
         "range": {"type": "numbered", "startDate": "2026-10-01", "numberOfOccurrences": 10}}
    p = rc.from_graph(g, datetime(2026, 10, 29, 7, 0, tzinfo=U), datetime(2026, 10, 29, 8, 30, tzinfo=U), "Europe/Moscow")
    assert p["type"] == "monthly_nth" and p["time"] == "10:00" and p["minutes"] == 90 and p["count"] == 10 and p["nth"] == 5
    for bad in ({}, {"type": "hourly"}, {**base(type="daily"), "time": "25:00"}, {**base(type="daily"), "minutes": 0}, {**base(type="weekly"), "days": []},
                {**base(type="monthly"), "day": 40}, {**base(type="daily"), "start": "2026-13-40"}, {**base(type="daily"), "interval": "x"}):
        assert rc.validate(bad) is None, bad
    assert rc.from_office("<script>") is None and rc.from_graph(None, None, None, None) is None
    stored = rc.from_office(office)
    assert rc.validate(stored) == stored, "сохранённый (уже нормализованный) шаблон проходит проверку без изменений"
    assert nxt(stored) == ("2026-10-26 09:30", "10:15")
