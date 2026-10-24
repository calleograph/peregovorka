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


# ------------------------------------------------------------------------------------------------ исключения серии: перенос и отмена одного занятия
WEEKLY_MSK = base(type="weekly", days=["monday"], start="2026-09-07", tz="Europe/Moscow")      # пн 10:00 МСК = 07:00 UTC; ближайшее — 26.10


def test_cancelled_occurrence_is_skipped_and_the_rest_of_the_series_continues():
    assert nxt(WEEKLY_MSK) == ("2026-10-26 07:00", "08:00")
    exc = {"cancelled": ["2026-10-26"], "moved": {}}
    assert rc.next_occurrence(WEEKLY_MSK, None, NOW, exc)[0].strftime("%Y-%m-%d %H:%M") == "2026-11-02 07:00", "отменённое занятие пропущено, следующее — по шаблону"
    two = {"cancelled": ["2026-10-26", "2026-11-02"]}
    assert rc.next_occurrence(WEEKLY_MSK, None, NOW, two)[0].date().isoformat() == "2026-11-09"
    # «N раз»: отменённое занятие входит в счёт (как в Outlook) — после отмены последнего серия закончилась
    counted = {**WEEKLY_MSK, "count": 8}                                              # 7.09 … 26.10 — восемь понедельников
    assert rc.next_occurrence(counted, None, NOW) is not None and rc.next_occurrence(counted, None, NOW, exc) is None


def test_moved_occurrence_replaces_only_its_own_slot():
    later = {"moved": {"2026-10-26": ["2026-10-27T11:00:00Z", "2026-10-27T12:30:00Z"]}}
    s, e = rc.next_occurrence(WEEKLY_MSK, None, NOW, later)
    assert (s.strftime("%Y-%m-%d %H:%M"), e.strftime("%H:%M")) == ("2026-10-27 11:00", "12:30"), "перенесённое занятие — в новое время и с новой длительностью"
    assert rc.next_occurrence(WEEKLY_MSK, None, datetime(2026, 10, 27, 13, 0, tzinfo=U), later)[0].date().isoformat() == "2026-11-02", "остальные занятия — по шаблону"
    earlier = {"moved": {"2026-10-26": ["2026-10-24T08:00:00Z", "2026-10-24T09:00:00Z"]}}
    assert rc.next_occurrence(WEEKLY_MSK, None, NOW, earlier)[0].date().isoformat() == "2026-10-24"
    # занятие прошлой недели перенесли на воскресенье — оно раньше ближайшего по шаблону
    from_past = {"moved": {"2026-10-19": ["2026-10-25T08:00:00Z", "2026-10-25T09:00:00Z"]}}
    assert rc.next_occurrence(WEEKLY_MSK, None, NOW, from_past)[0].date().isoformat() == "2026-10-25"
    # перенесённое занятие уже прошло — его исходное место не «воскресает»
    done = {"moved": {"2026-10-26": ["2026-10-23T08:00:00Z", "2026-10-23T09:00:00Z"]}}
    assert rc.next_occurrence(WEEKLY_MSK, None, NOW, done)[0].date().isoformat() == "2026-11-02"
    both = {"cancelled": ["2026-10-26"], "moved": {"2026-10-26": ["2026-10-27T11:00:00Z", "2026-10-27T12:00:00Z"]}}
    assert rc.next_occurrence(WEEKLY_MSK, None, NOW, both)[0].date().isoformat() == "2026-11-02", "отменено после переноса — не показываем"


def test_broken_exceptions_are_ignored_not_fatal():
    for raw in (None, {}, "x", {"cancelled": "2026-10-26"}, {"moved": []}, {"moved": {"2026-10-26": ["мусор"]}}, {"moved": {"2026-10-26": ["2026-10-27T11:00:00", "2026-10-27T12:00:00Z"]}},
                {"moved": {"2026-10-26": ["2026-10-27T12:00:00Z", "2026-10-27T11:00:00Z"]}}, {"cancelled": [None, 5, "26.10.2026"]}):
        assert rc.next_occurrence(WEEKLY_MSK, None, NOW, raw)[0].date().isoformat() == "2026-10-26", raw


def test_build_exceptions_uses_series_local_date_and_is_idempotent():
    from zoneinfo import ZoneInfo
    msk = ZoneInfo("Europe/Moscow")
    # EWS: исходные начала в UTC; 25.10 21:30 UTC — уже 26.10 по Москве
    deleted = [datetime(2026, 10, 25, 21, 30, tzinfo=U), datetime(2026, 11, 2, 7, 0, tzinfo=U), datetime(2026, 11, 2, 7, 0, tzinfo=U)]
    moved = [(datetime(2026, 11, 9, 7, 0, tzinfo=U), datetime(2026, 11, 10, 8, 0, tzinfo=U), datetime(2026, 11, 10, 9, 0, tzinfo=U))]
    a = rc.build_exceptions(deleted, moved, msk)
    assert a == {"cancelled": ["2026-10-26", "2026-11-02"], "moved": {"2026-11-09": ["2026-11-10T08:00:00Z", "2026-11-10T09:00:00Z"]}}
    assert rc.build_exceptions(deleted, moved, msk) == a, "повторная сверка — те же исключения, без дублей"
    # Graph: даты из occurrenceId (OID.<мастер>.ГГГГ-ММ-ДД) — уже в поясе серии
    assert rc.build_exceptions(["2026-10-26", "мусор", "2026-10-26"], [], msk)["cancelled"] == ["2026-10-26"]


# ------------------------------------------------------------------------------------------------ часовой пояс серии — из события календаря
def test_series_time_zone_comes_from_the_calendar_not_from_the_browser():
    assert rc.zone_name("Russian Standard Time") == "Europe/Moscow" and rc.zone_name("W. Europe Standard Time") == "Europe/Berlin"
    assert rc.zone_name("Pacific Standard Time") == "America/Los_Angeles" and rc.zone_name("UTC") == "Etc/UTC" and rc.zone_name("Europe/Berlin") == "Europe/Berlin"
    for bad in ("tzone://Microsoft/Custom", "Customized Time Zone", "", None, "x" * 100, "../../etc/passwd"):
        assert rc.zone_name(bad) is None, bad
    # надстройка: время серии 10:00 в поясе серии (Берлин), браузер организатора — в Екатеринбурге
    office = {"type": "weekly", "props": {"interval": 1, "days": ["mon"]}, "start_date": "2026-09-07", "end_date": None, "start_time": "10:00", "duration": 60,
              "tz": "W. Europe Standard Time"}
    p = rc.from_office(office)
    assert p["tz"] == "Europe/Berlin" and rc.validate(p) == p
    assert nxt(p, tz="Asia/Yekaterinburg") == ("2026-10-26 09:00", "10:00"), "10:00 по Берлину (зимнее время) = 09:00 UTC, пояс браузера не влияет"
    assert "tz" not in rc.from_office({**office, "tz": "tzone://Microsoft/Custom"}), "свой пояс Outlook — запасной пояс организатора"
    # Graph: recurrenceTimeZone серии, иначе пояс события (originalStartTimeZone)
    g = {"pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["thursday"]}, "range": {"type": "noEnd", "startDate": "2026-10-01", "recurrenceTimeZone": "Pacific Standard Time"}}
    p = rc.from_graph(g, datetime(2026, 10, 1, 16, 0, tzinfo=U), datetime(2026, 10, 1, 17, 0, tzinfo=U), "Europe/Moscow")
    assert p["tz"] == "America/Los_Angeles" and p["time"] == "09:00"
    p2 = rc.from_graph({**g, "range": {"type": "noEnd", "startDate": "2026-10-01"}}, datetime(2026, 10, 1, 7, 0, tzinfo=U), datetime(2026, 10, 1, 8, 0, tzinfo=U), "UTC", "Russian Standard Time")
    assert p2["tz"] == "Europe/Moscow" and p2["time"] == "10:00"
    p3 = rc.from_graph({**g, "range": {"type": "noEnd", "startDate": "2026-10-01"}}, datetime(2026, 10, 1, 7, 0, tzinfo=U), datetime(2026, 10, 1, 8, 0, tzinfo=U), "Europe/Moscow")
    assert "tz" not in p3 and p3["time"] == "10:00", "пояса в событии нет — пояс организатора из надстройки"


def test_daylight_saving_transitions_keep_local_time_of_the_series():
    # Европа: 25.10.2026 — переход на зимнее время, 28.03.2027 — на летнее; занятие в 10:00 по Берлину
    ber = base(type="weekly", days=["monday"], start="2026-09-07", tz="Europe/Berlin")
    assert nxt(ber, now=datetime(2026, 10, 19, 0, 0, tzinfo=U)) == ("2026-10-19 08:00", "09:00"), "летнее время: UTC+2"
    assert nxt(ber, now=datetime(2026, 10, 20, 0, 0, tzinfo=U)) == ("2026-10-26 09:00", "10:00"), "после перехода: UTC+1"
    assert nxt(ber, now=datetime(2027, 3, 23, 0, 0, tzinfo=U)) == ("2027-03-29 08:00", "09:00"), "после перехода на летнее: снова UTC+2"
    # США переходят на неделю позже Европы (1.11.2026): серия по Лос-Анджелесу в 9:00
    la = base(type="daily", start="2026-10-01", time="09:00", tz="America/Los_Angeles")
    assert nxt(la, now=datetime(2026, 10, 31, 20, 0, tzinfo=U)) == ("2026-11-01 17:00", "18:00") and nxt(la, now=datetime(2026, 10, 30, 12, 0, tzinfo=U)) == ("2026-10-30 16:00", "17:00")
    # ночь перехода на летнее: 02:30 не существует — занятие в 03:30 местного (01:30 UTC); ночь перехода на зимнее: 02:30 бывает дважды — первое
    night = base(type="daily", start="2027-03-01", time="02:30", tz="Europe/Berlin")
    assert nxt(night, now=datetime(2027, 3, 28, 0, 0, tzinfo=U)) == ("2027-03-28 01:30", "02:30")
    assert nxt({**night, "start": "2026-10-01"}, now=datetime(2026, 10, 25, 0, 0, tzinfo=U)) == ("2026-10-25 00:30", "01:30")
    # перенос занятия через границу перехода: исходная дата — в поясе серии, новое время — точный момент UTC
    exc = rc.build_exceptions([], [(datetime(2026, 10, 26, 9, 0, tzinfo=U), datetime(2026, 10, 24, 8, 0, tzinfo=U), datetime(2026, 10, 24, 9, 0, tzinfo=U))], rc.series_zone(ber, None))
    assert exc["moved"] == {"2026-10-26": ["2026-10-24T08:00:00Z", "2026-10-24T09:00:00Z"]}
    assert rc.next_occurrence(ber, None, datetime(2026, 10, 24, 10, 0, tzinfo=U), exc)[0].date().isoformat() == "2026-11-02"
