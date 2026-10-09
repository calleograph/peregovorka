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


def meta_from_header(header: list[str]) -> list[list[str]]:
    out = []
    for h in header:
        if h.startswith("===") or not h.strip():
            continue
        if ":" in h:
            a, b = h.split(":", 1)
            out.append([a.strip(), b.strip()])
        else:
            out.append(["", h.strip()])
    return out


def build_document(header: list[str], items: list, *, summary: str = "", incomplete: bool = False, notes: list[str] | None = None,
                   title: str = "Протокол совещания") -> dict:
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
    return {
        "title": title, "meta": meta_from_header(header), "summary": (summary or "").strip(),
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


def document_to_markdown(doc: dict, *, disclaimer: bool = True) -> str:
    L: list[str] = [f"# {doc.get('title') or 'Протокол совещания'}", ""]
    for label, value in doc.get("meta") or []:
        L += [f"**{label}:** {value}" if label else value, ""]
    if doc.get("incomplete"):
        L += ["> ⚠ [проверить] Документ может быть неполным: часть материалов не удалось разобрать даже после повторной попытки. Сверьте с записью.", ""]
    for n in doc.get("notes") or []:
        L += [f"> [проверить] {n}", ""]
    if disclaimer:
        L += ["_Документ собран автоматически: каждый пункт проверен по тексту и привязан к реплике-источнику; чего в репликах нет — «не указан». Важные пункты сверьте с записью._", ""]
    if doc.get("summary"):
        L += ["## Итог", "", doc["summary"], ""]
    L += ["## Обсуждение", ""]
    d = doc.get("discussion") or []
    L += _table(["№", "Тема", "Что обсуждали", "Чем закончилось", "Где в записи"], [[str(n), r["topic"], r["discussion"], r["result"], r["source"]] for n, r in enumerate(d, 1)]) if d else ["— темы не выделены"]
    L += ["", "## Решения", ""]
    d = doc.get("decisions") or []
    L += _table(["№", "Решение", "Где в записи"], [[str(n), r["decision"], r["source"]] for n, r in enumerate(d, 1)]) if d else ["— не принималось"]
    L += ["", "## Задачи", ""]
    d = doc.get("tasks") or []
    L += _table(["№", "Задача", "Ответственный", "Срок", "Где в записи"], [[str(n), r["task"], r["assignee"], r["deadline"], r["source"]] for n, r in enumerate(d, 1)]) if d else ["— не выделены"]
    d = doc.get("proposals") or []
    if d:
        L += ["", "## Предложения, не принятые как решение", ""] + _table(["№", "Предложение", "Где в записи"], [[str(n), r["proposal"], r["source"]] for n, r in enumerate(d, 1)])
    L += ["", "## Открытые вопросы", ""]
    d = doc.get("open_questions") or []
    L += _table(["№", "Вопрос", "Чего не хватает", "Где в записи"], [[str(n), r["question"], r["missing"], r["source"]] for n, r in enumerate(d, 1)]) if d else ["— нет"]
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
