"""Выгрузка стенограммы в текстовые форматы: Markdown, WebVTT, SRT (JSON и TXT строятся в маршрутах)."""
from __future__ import annotations

from datetime import datetime


def _rel(t: datetime, origin: datetime) -> float:
    return max(0.0, (t - origin).total_seconds())


def _stamp(sec: float, sep: str) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _cue_text(name: str, text: str) -> str:
    one = " ".join(str(text).split())
    return f"{name}: {one}"


def to_vtt(items: list[tuple[datetime, datetime, str, str]], origin: datetime) -> str:
    """items — (начало, конец, имя, текст); время в файле отсчитывается от начала встречи."""
    out = ["WEBVTT", ""]
    for start, end, name, text in items:
        s, e = _rel(start, origin), _rel(end, origin)
        e = max(e, s + 0.5)
        out += [f"{_stamp(s, '.')} --> {_stamp(e, '.')}", _cue_text(name, text).replace("-->", "—>"), ""]
    return "\n".join(out)


def to_srt(items: list[tuple[datetime, datetime, str, str]], origin: datetime) -> str:
    out: list[str] = []
    for i, (start, end, name, text) in enumerate(items, 1):
        s, e = _rel(start, origin), _rel(end, origin)
        e = max(e, s + 0.5)
        out += [str(i), f"{_stamp(s, ',')} --> {_stamp(e, ',')}", _cue_text(name, text), ""]
    return "\n".join(out)


def to_markdown(title: str, header: list[str], items: list[tuple[datetime, datetime, str, str]], clock) -> str:
    lines = [f"# {title}", ""] + [f"- {h}" for h in header] + [""]
    for start, _end, name, text in items:
        lines += [f"**[{clock(start)}] {name}:** {' '.join(str(text).split())}", ""]
    return "\n".join(lines)
