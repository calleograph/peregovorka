"""«Что нового» для окна обновления: CHANGELOG.md → разделы по версиям между установленной и доступной → группы по смыслу и важные предупреждения.

Источник — тот же CHANGELOG.md, из которого workflow релиза собирает описание GitHub Release (поэтому отдельно заметки релиза не запрашиваются: сервер
может быть без выхода в интернет, а текст совпадает). Помощник обновлений кладёт в `remote.json` разделы новее установленной версии; здесь они разбираются.

Группа пункта определяется подзаголовком релиза (`### Новые возможности` / `### Улучшения` / `### Исправления` / `### Для администраторов`), если он есть, иначе по словам в тексте пункта. Важные пункты выделяются
по признакам (порты, схема БД, LiveKit, ручное действие, интернет при обновлении) и по фактам репозитория (изменились миграции или .env.example).
"""
from __future__ import annotations

import re

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SECTION_RE = re.compile(r"^##\s+\[?(\d+\.\d+\.\d+)\]?(.*)$")

GROUPS = [
    ("features", "Новые возможности"),
    ("improvements", "Улучшения"),
    ("fixes", "Исправления"),
    ("ui", "Изменения интерфейса"),
    ("config", "Конфигурация"),
    ("db", "База данных и миграции"),
    ("admin", "Что потребуется от администратора"),
    ("limits", "Известные ограничения"),
]
TITLES = dict(GROUPS)

_HEADING_MAP = [
    (re.compile(r"администратор", re.I), "admin"),             # «Для администраторов» — раньше «нов…»: заголовок однозначен
    (re.compile(r"нов|функци|возможност", re.I), "features"),
    (re.compile(r"улучшен", re.I), "improvements"),
    (re.compile(r"исправ|ошиб", re.I), "fixes"),
    (re.compile(r"интерфейс", re.I), "ui"),
    (re.compile(r"конфиг|настрой", re.I), "config"),
    (re.compile(r"баз|миграц", re.I), "db"),
    (re.compile(r"администратор|действи|требует", re.I), "admin"),
    (re.compile(r"ограничен|известн", re.I), "limits"),
]
_RULES = [
    ("fixes", re.compile(r"\bисправл|\bошибк|\bсбой|не работал|ломал|устранен|устранён|\bневерн", re.I)),
    ("limits", re.compile(r"известн\w+ ограничен|ограничени[ея]:|не проверен|пока не (?:умеет|поддерж)|не реализован", re.I)),
    ("db", re.compile(r"миграц|схем\w+ (?:БД|баз)|таблиц\w+ (?:БД|базы)|структур\w+ (?:БД|баз)", re.I)),
    ("admin", re.compile(r"администратор\w* (?:нужно|необходимо|должен|должна|потребуется)|потребуется|требуется (?:от администратор|выполнить|вручную)|вручную|после обновления (?:нужно|выполните)|один раз выполните", re.I)),
    ("config", re.compile(r"\.env|переменн\w+ окружен|параметр\w* (?:конфигурац|установк)|порт[ыа]?\b|compose|конфигурац", re.I)),
    ("ui", re.compile(r"интерфейс|страниц|кнопк|окно|окна|диалог|раздел «|меню|вкладк|подпис|отображ|показыва", re.I)),
]
_ALERTS = [
    ("db", re.compile(r"миграц|схем\w+ (?:БД|баз)|структур\w+ (?:БД|баз)", re.I), "Изменяется схема базы данных: перед миграцией делается резервная копия, миграции применяются автоматически."),
    ("port", re.compile(r"\bпорт(?:ы|ов|а|у)?\b|\bports?\b|UDP|TCP\b", re.I), "Затронуты сетевые порты: проверьте файрвол и маршрутизацию."),
    ("restart", re.compile(r"перезапуск\w* (?:LiveKit|сервер\w* звонков)|LiveKit\w* (?:будет )?перезапущ|пересозда\w+ (?:контейнер|LiveKit)", re.I), "Потребуется перезапуск LiveKit: идущие звонки прервутся."),
    ("manual", re.compile(r"вручную|один раз выполните|после обновления (?:нужно|выполните)|администратор\w* (?:нужно|необходимо|должен)|необходимо (?:выполнить|вручную)", re.I), "Требуется ручное действие администратора."),
    ("download", re.compile(r"(?:скача|загруз)\w+[^.]{0,60}(?:интернет|ГБ|МБ)|нужен интернет", re.I), "При обновлении потребуется интернет и место на диске для загрузки."),
    ("breaking", re.compile(r"несовместим|больше не (?:скачива|поддержива|предлага)|снят\w* с вооружения|удал[её]н|убран", re.I), "Есть изменения, которые убирают прежнее поведение."),
]


def _vt(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def split_sections(text: str) -> list[tuple[str, str]]:
    """[(версия, тело)] в порядке следования в тексте (новая версия первой)."""
    out: list[tuple[str, list[str]]] = []
    for line in (text or "").splitlines():
        m = SECTION_RE.match(line.strip())
        if m:
            out.append((m.group(1), []))
        elif out:
            out[-1][1].append(line.rstrip())
    return [(v, "\n".join(body).strip()) for v, body in out]


def _bullets(body: str) -> list[tuple[str | None, str]]:
    """[(подзаголовок или None, текст пункта)]; продолжения строк склеиваются с пунктом."""
    items: list[tuple[str | None, str]] = []
    head: str | None = None
    cur: list[str] | None = None
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("###"):
            if cur:
                items.append((head, " ".join(cur)))
            cur, head = None, s.lstrip("#").strip()
        elif s[:2] in ("- ", "* "):
            if cur:
                items.append((head, " ".join(cur)))
            cur = [s[2:].strip()]
        elif cur is not None:
            cur.append(s)
        else:
            cur = [s]
    if cur:
        items.append((head, " ".join(cur)))
    return items


def classify(text: str, heading: str | None = None) -> str:
    if heading:
        for rx, gid in _HEADING_MAP:
            if rx.search(heading):
                return gid
    for gid, rx in _RULES:
        if rx.search(text):
            return gid
    return "features"


def alerts_for(text: str) -> list[str]:
    return [kind for kind, rx, _msg in _ALERTS if rx.search(text)]


def build(text: str, installed: str | None, available: str | None, *, migrations_changed: int = 0, env_changed: bool = False) -> dict:
    """Структура «что нового» между установленной (не включая) и доступной (включая) версиями."""
    lo = _vt(installed) if installed and VERSION_RE.match(installed) else None
    hi = _vt(available) if available and VERSION_RE.match(available) else None
    groups: dict[str, list[dict]] = {gid: [] for gid, _ in GROUPS}
    important: list[dict] = []
    versions: list[str] = []
    for ver, body in split_sections(text):
        v = _vt(ver)
        if (lo is not None and v <= lo) or (hi is not None and v > hi):
            continue
        versions.append(ver)
        for heading, item in _bullets(body):
            gid = classify(item, heading)
            kinds = alerts_for(item)
            row = {"version": ver, "text": item, "alerts": kinds}
            groups[gid].append(row)
            if kinds:
                important.append({"version": ver, "kinds": kinds, "text": item})
    facts: list[dict] = []
    if migrations_changed > 0:
        facts.append({"kind": "db", "text": f"Изменены файлы миграций базы ({migrations_changed}): перед применением делается резервная копия, обновление применит их само."})
    if env_changed:
        facts.append({"kind": "config", "text": "Изменился шаблон .env.example: безопасные новые параметры допишутся в .env автоматически, остальные обновление покажет списком."})
    return {
        "installed": installed, "available": available, "versions": versions,
        "groups": [{"id": gid, "title": title, "items": groups[gid]} for gid, title in GROUPS if groups[gid]],
        "important": important, "facts": facts,
        "alert_text": {kind: msg for kind, _rx, msg in _ALERTS},
        "empty": not any(groups.values()),
    }
