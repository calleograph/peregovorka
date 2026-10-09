"""Шаблоны писем для рассылки материалов встречи.

Шаблон — тема, текст, подпись и материалы по умолчанию; переменные вида {{meeting_title}}. Подстановка — только по белому списку имён обычной заменой
строк (не Jinja и не eval: выполнить код из шаблона нельзя). Значения переменных — недоверенный текст (названия, имена): в тему они попадают без
переводов строк, чтобы нельзя было подмешать заголовки письма. Первоначальный шаблон создаётся один раз и далее редактируется администратором.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meeting, MailTemplate, Protocol, User, utcnow

VARIABLES: list[tuple[str, str]] = [
    ("meeting_title", "Название встречи (переговорной)"), ("meeting_date", "Дата встречи, ДД.ММ.ГГГГ"), ("meeting_start", "Время начала, ЧЧ:ММ"),
    ("meeting_end", "Время окончания, ЧЧ:ММ"), ("room_name", "Название виртуальной переговорной"), ("organizer_name", "Кто начал встречу"),
    ("participants", "Участники встречи через запятую"), ("protocol_name", "Название протокола (если сформирован)"),
    ("summary_name", "Название резюме (если сформировано)"), ("project_name", "Проект (появится вместе с проектами; пока пусто)"),
]
NAMES = {n for n, _ in VARIABLES}
_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

DEFAULT_TEMPLATE = {
    "name": "Стандартный",
    "subject": "Материалы встречи «{{meeting_title}}» от {{meeting_date}}",
    "body": ("Добрый день!\n\nНаправляем материалы по результатам встречи «{{meeting_title}}», проведённой {{meeting_date}} в виртуальной переговорной «{{room_name}}».\n\n"
             "К письму приложены сформированные материалы встречи."),
    "signature": "С уважением,\nPeregovorka",
    "materials": ["protocol", "summary"],
}
MAX_NAME, MAX_SUBJECT, MAX_BODY, MAX_SIGNATURE = 120, 300, 8000, 1000


def unknown_variables(*texts: str) -> list[str]:
    return sorted({m for t in texts for m in _VAR.findall(t or "") if m not in NAMES})


def render(text: str, ctx: dict[str, str]) -> str:
    """Подстановка по белому списку; неизвестное имя остаётся как написано (видно в предпросмотре)."""
    return _VAR.sub(lambda m: str(ctx[m.group(1)]) if m.group(1) in NAMES and m.group(1) in ctx else m.group(0), text or "")


def one_line(s: str, limit: int = MAX_SUBJECT) -> str:
    """Тема письма: без переводов строк и управляющих символов (защита от подмешивания заголовков)."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", s or "").strip()[:limit]


def clean_template(raw: dict, *, materials_known: set[str]) -> dict:
    """Проверка шаблона от администратора; ошибка — ValueError с текстом для человека."""
    name = one_line(str(raw.get("name") or ""), MAX_NAME)
    if not name:
        raise ValueError("Укажите название шаблона")
    subject = one_line(str(raw.get("subject") or ""))
    if not subject:
        raise ValueError("Укажите тему письма")
    body = str(raw.get("body") or "").replace("\r\n", "\n").strip()
    if not body:
        raise ValueError("Текст письма не может быть пустым")
    if len(body) > MAX_BODY:
        raise ValueError(f"Текст письма: не больше {MAX_BODY} знаков")
    signature = str(raw.get("signature") or "").replace("\r\n", "\n").strip()
    if len(signature) > MAX_SIGNATURE:
        raise ValueError(f"Подпись: не больше {MAX_SIGNATURE} знаков")
    bad = unknown_variables(subject, body, signature)
    if bad:
        raise ValueError("Неизвестные переменные: " + ", ".join("{{" + b + "}}" for b in bad) + ". Допустимые: " + ", ".join("{{" + n + "}}" for n, _ in VARIABLES))
    mats = [m for m in (raw.get("materials") or []) if m in materials_known]
    return {"name": name, "subject": subject, "body": body, "signature": signature, "materials": list(dict.fromkeys(mats))}


def public(t: MailTemplate) -> dict:
    return {"id": str(t.id), "name": t.name, "subject": t.subject, "body": t.body, "signature": t.signature or "", "materials": list(t.materials or []), "is_default": t.is_default}


async def ensure_default(db: AsyncSession) -> None:
    """Первоначальный шаблон создаётся при первом обращении; дальше администратор меняет его как хочет."""
    if (await db.execute(select(MailTemplate.id).limit(1))).first() is None:
        db.add(MailTemplate(**DEFAULT_TEMPLATE, is_default=True))
        await db.flush()


async def list_templates(db: AsyncSession) -> list[MailTemplate]:
    await ensure_default(db)
    return list((await db.execute(select(MailTemplate).order_by(MailTemplate.is_default.desc(), MailTemplate.name))).scalars())


async def get_template(db: AsyncSession, template_id: str | None) -> MailTemplate | None:
    await ensure_default(db)
    if template_id:
        try:
            t = await db.get(MailTemplate, uuid.UUID(str(template_id)))
        except ValueError:
            t = None
        if t is not None:
            return t
    return (await db.execute(select(MailTemplate).where(MailTemplate.is_default.is_(True)).limit(1))).scalars().first() or (
        await db.execute(select(MailTemplate).limit(1))).scalars().first()


async def meeting_context(db: AsyncSession, meeting: Meeting, tz) -> dict[str, str]:
    start = meeting.started_at.astimezone(tz)
    end = meeting.ended_at.astimezone(tz) if meeting.ended_at else None
    organizer = await db.get(User, meeting.started_by_user_id) if meeting.started_by_user_id else None
    names, seen = [], set()
    for p in sorted(meeting.participants, key=lambda x: x.joined_at):
        if p.user_id not in seen and p.user is not None:
            seen.add(p.user_id)
            names.append(p.user.display_name)
    titles: dict[str, str] = {}
    for kind in ("protocol", "summary"):
        p = (await db.execute(select(Protocol).where(Protocol.meeting_id == meeting.id, Protocol.kind == kind, Protocol.status == "ready")
                              .order_by(Protocol.updated_at.desc()))).scalars().first()
        titles[kind] = (p.title or ("Протокол совещания" if kind == "protocol" else "Краткое резюме")) if p else ""
    return {"meeting_title": meeting.room.name, "meeting_date": start.strftime("%d.%m.%Y"), "meeting_start": start.strftime("%H:%M"),
            "meeting_end": end.strftime("%H:%M") if end else "", "room_name": meeting.room.name, "organizer_name": organizer.display_name if organizer else "",
            "participants": ", ".join(names), "protocol_name": titles["protocol"], "summary_name": titles["summary"], "project_name": ""}


def compose(t: MailTemplate | dict, ctx: dict[str, str]) -> tuple[str, str]:
    """(тема, текст письма с подписью) для встречи."""
    g = (lambda k: getattr(t, k, None)) if not isinstance(t, dict) else t.get
    subject = one_line(render(g("subject") or "", ctx))
    body = render(g("body") or "", ctx).strip()
    sig = render(g("signature") or "", ctx).strip()
    return subject, body + (("\n\n" + sig) if sig else "")


_ = utcnow  # noqa: F841 — модель использует utcnow по умолчанию
