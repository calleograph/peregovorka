"""Документ протокола: единая структура → Markdown → (Word / PDF / HTML / простой текст). Модель отвечает за содержание, оформление делает система.

Контракт (то, из чего строятся все форматы; сохраняется в `protocols.meta.document`):

    {"title", "meta": [[подпись, значение], …], "summary", "discussion": [{topic, discussion, result, source}], "decisions": [{decision, source}],
     "tasks": [{task, assignee, deadline, source}], "proposals": [{proposal, source}], "open_questions": [{question, missing, source}],
     "incomplete": bool, "notes": [..]}

Таблицы собирает код: ячейка содержит полный текст (без обрезки и многоточий), перенос и ширину столбцов выбирает формат вывода (export_docs).
"""
from __future__ import annotations

import re

NOT_SET = "не указан"
NO_DECISION = "решение не принималось"


def _cell(s: object) -> str:
    """Текст в ячейку таблицы Markdown: в одну строку, символ «|» заменён (иначе сломает разметку), больше ничего не меняется и не обрезается."""
    return " ".join(str(s or "").replace("|", "/").split()) or "—"


def where(source: dict | None) -> str:
    """«Где в записи»: [время] Имя (чат/схема)."""
    if not source:
        return "—"
    who = f" {source['speaker']}" if source.get("speaker") else ""
    tag = f" ({source['section']})" if source.get("section") else ""
    return f"[{source.get('ts', '')}]{who}{tag}".strip()


_MONTH_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(.*)$")
_RANGE = re.compile(r"^(\d{1,2}):(\d{2})(?:\s*[—–-]\s*окончание:\s*(\d{1,2}):(\d{2}))?\s*$", re.I)


def meta_from_header(header: list[str]) -> list[list[str]]:
    """Шапка стенограммы → сведения о встрече для документа: «Встреча», «Дата» (ДД.ММ.ГГГГ), «Время», «Длительность», «Участвовали»; прочие строки шапки остаются как есть."""
    out: list[list[str]] = []
    for h in header:
        if h.startswith("===") or not h.strip():
            continue
        if ":" not in h:
            out.append(["", h.strip()])
            continue
        a, b = (x.strip() for x in h.split(":", 1))
        low = a.lower()
        if low in ("переговорка", "встреча", "название"):
            out.append(["Встреча", b])
        elif low == "дата":
            m = _MONTH_ISO.match(b)
            out.append(["Дата", f"{m.group(3)}.{m.group(2)}.{m.group(1)}{m.group(4)}" if m else b])
        elif low == "начало":
            m = _RANGE.match(b)
            if not m:
                out.append([a, b])
                continue
            start = f"{int(m.group(1)):02d}:{m.group(2)}"
            if m.group(3):
                end = f"{int(m.group(3)):02d}:{m.group(4)}"
                out.append(["Время", f"{start} — {end}"])
                mins = (int(m.group(3)) * 60 + int(m.group(4)) - int(m.group(1)) * 60 - int(m.group(2))) % 1440
                if 0 < mins:
                    out.append(["Длительность", f"{mins // 60} ч {mins % 60} мин" if mins >= 60 else f"{mins} мин"])
            else:
                out.append(["Время", f"начало в {start}"])
        else:
            out.append([a, b])
    return out


def attendees_markdown(att: dict | None) -> list[str]:
    """«Присутствовали»: сотрудники — таблица ФИО / Должность / Подразделение (по снимку на момент встречи); гости — Имя / Статус. Пустые значения — «—», не выдумываются."""
    if not att or not (att.get("people") or att.get("guests")):
        return []
    L = ["## Присутствовали", ""]
    if att.get("people"):
        cols = [("ФИО", "name"), ("Должность", "title"), ("Подразделение", "department")]
        cols = [(h, k) for h, k in cols if k == "name" or any((p.get(k) or "").strip() for p in att["people"])]      # столбец, где ни у кого нет данных, не выводится (а не ряд прочерков)
        L += _table([h for h, _k in cols], [[p.get(k, "") for _h, k in cols] for p in att["people"]]) + [""]
    if att.get("guests"):
        L += _table(["Имя", "Статус"], [[g.get("name", ""), g.get("status", "Гость")] for g in att["guests"]]) + [""]
    return L


def inject_attendees(md: str, att: dict | None) -> str:
    """Свободный режим (Markdown от модели): раздел «Присутствовали» добавляется кодом сразу после первого заголовка, если модель его не написала."""
    block = attendees_markdown(att)
    if not block or re.search(r"^#{1,6}\s*Присутствовали", md, re.M | re.I):
        return md
    lines = md.splitlines()
    at = next((i + 1 for i, ln in enumerate(lines) if ln.lstrip().startswith("#")), 0)
    return "\n".join(lines[:at] + [""] + block + lines[at:]).rstrip() + "\n"


def build_document(header: list[str], items: list, *, summary: str = "", incomplete: bool = False, notes: list[str] | None = None,
                   title: str = "Протокол встречи", attendees: dict | None = None) -> dict:
    """Структура документа из проверенных пунктов (extraction.Item). Ничего не добавляется от себя: чего нет — «не указан» / «решение не принималось»."""
    topics = sorted((i for i in items if i.kind == "topic"), key=lambda i: i.sec)
    decisions = [i for i in items if i.kind == "decision"]
    tasks = [i for i in items if i.kind == "task"]
    opens = [i for i in items if i.kind == "open"]
    discussion = []
    for n, t in enumerate(topics):
        end = topics[n + 1].sec if n + 1 < len(topics) else 10 ** 9
        got = [d.text for d in decisions if t.sec <= d.sec < end]
        res = "Решение: " + "; ".join(got) if got else NO_DECISION
        q = [o.text for o in opens if t.sec <= o.sec < end]
        if q:
            res += ". Остался открытым: " + "; ".join(q)
        discussion.append({"topic": t.text, "discussion": t.detail or "подробности в записи", "result": res, "source": where(t.sources[0] if t.sources else None)})
    task_rows = []
    for t in tasks:
        owner = t.assignee or "не назначен"
        if not t.assignee and t.assignee_guess:
            owner = f"не определён (модель предположила: {t.assignee_guess}; в репликах не подтверждено)"
        due = (t.due + (f" ({t.due_date})" if t.due_date else "")) if t.due else NOT_SET
        task_rows.append({"task": t.text, "assignee": owner, "deadline": due, "source": where(t.sources[0] if t.sources else None)})
    meta = meta_from_header(header)
    if attendees and (attendees.get("people") or attendees.get("guests")):
        meta = [m for m in meta if not m[0].lower().startswith("участвовали")]       # список имён заменяется таблицами «Присутствовали»
    return {
        "attendees": attendees or None,
        "title": title, "meta": meta, "summary": (summary or "").strip(),
        "discussion": discussion,
        "decisions": [{"decision": d.text, "source": where(d.sources[0] if d.sources else None)} for d in decisions],
        "tasks": task_rows,
        "proposals": [{"proposal": p.text, "source": where(p.sources[0] if p.sources else None)} for p in items if p.kind == "proposal"],
        "open_questions": [{"question": o.text, "missing": "ответ в записи не прозвучал", "source": where(o.sources[0] if o.sources else None)} for o in opens],
        "incomplete": bool(incomplete), "notes": list(notes or []),
    }


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out


def _numbered(rows: list[str]) -> list[str]:
    return [f"{n}. {r}" for n, r in enumerate(rows, 1)]


def _with_src(text: str, source: str) -> str:
    one = " ".join(str(text or "").split())
    return f"{one} — _{source}_" if source and source != "—" else one


def document_to_markdown(doc: dict, *, disclaimer: bool = True) -> str:
    """Содержание документа в Markdown. Пустые разделы не выводятся (кроме «Обсуждений»). Оформление в HTML — services/glass_html.py и страница приложения."""
    L: list[str] = [f"# {doc.get('title') or 'Протокол встречи'}", ""]
    for label, value in doc.get("meta") or []:
        L += [f"**{label}:** {value}" if label else value, ""]
    if doc.get("incomplete"):
        L += ["> ⚠ [проверить] Документ может быть неполным: часть материалов не удалось разобрать даже после повторной попытки. Сверьте с записью.", ""]
    for n in doc.get("notes") or []:
        L += [f"> [проверить] {n}", ""]
    if disclaimer:
        L += ["_Документ собран автоматически: каждый пункт проверен по тексту и привязан к реплике-источнику; чего в репликах нет — «не указан». Важные пункты сверьте с записью._", ""]
    L += attendees_markdown(doc.get("attendees"))
    if doc.get("summary"):
        L += ["## Краткие итоги", "", doc["summary"], ""]
    L += ["## Обсуждения", ""]
    d = doc.get("discussion") or []
    if d:
        for n, r in enumerate(d, 1):
            L += [f"### {n}. {_cell(r['topic'])}", "", " ".join(str(r["discussion"]).split()), "", f"**Чем закончилось:** {' '.join(str(r['result']).split())}", ""]
            if r.get("source") and r["source"] != "—":
                L += [f"_Где в записи: {r['source']}_", ""]
    else:
        L += ["— темы не выделены", ""]
    d = doc.get("decisions") or []
    if d:
        L += ["## Решения", ""] + _numbered([_with_src(r["decision"], r["source"]) for r in d]) + [""]
    d = doc.get("tasks") or []
    if d:
        L += ["## Задачи и поручения", ""] + _table(["№", "Задача", "Ответственный", "Срок", "Где в записи"], [[str(n), r["task"], r["assignee"], r["deadline"], r["source"]] for n, r in enumerate(d, 1)]) + [""]
    d = doc.get("proposals") or []
    if d:
        L += ["## Предложения, не принятые как решение", ""] + _numbered([_with_src(r["proposal"], r["source"]) for r in d]) + [""]
    d = doc.get("open_questions") or []
    if d:
        L += ["## Открытые вопросы", ""] + _numbered([_with_src(r["question"], r["source"]) for r in d]) + [""]
    return "\n".join(L).rstrip() + "\n"


def summary_document(doc: dict) -> str:
    """Краткое резюме (вид «резюме»): один абзац итога, при его отсутствии — перечень проверенных пунктов."""
    return doc.get("summary") or ""


_ACCENT = (("решени", "ok"), ("задач", "info"), ("поручени", "info"), ("открыт", "warn"), ("предложени", "muted"))


def section_accent(heading: str) -> str | None:
    """Мягкий цветовой акцент раздела по его заголовку: решения — зелёный, задачи — голубой, открытые вопросы — янтарный."""
    h = heading.lower()
    return next((a for key, a in _ACCENT if key in h), None)


def warn_text(text: str) -> bool:
    return bool(re.search(r"\[проверить\]", text))


INCOMPLETE_NOTE = ("> ⚠ [проверить] Документ может быть неполным: ответ модели был оборван по лимиту длины. "
                   "Сверьте с записью или сформируйте заново, выбрав другую модель.")


def with_incomplete_notice(md: str, meta: dict | None) -> str:
    """Оборванный по лимиту длины документ не выдаётся за полный: во всех выгрузках (HTML, Word, PDF, Markdown, письмо, публичный API) сверху стоит заметная пометка.
    У собранного кодом документа такая пометка уже есть (`incomplete`) — второй раз не добавляется."""
    if (meta or {}).get("truncated") and "может быть неполным" not in md:
        return INCOMPLETE_NOTE + "\n\n" + md
    return md
