"""Присутствовавшие на встрече: единый источник для всех документов (протокол, DOCX/HTML/PDF, рассылка).

Для сотрудников — снимок (snapshot) данных на момент встречи: ФИО, должность, подразделение, e-mail, телефон. Он сохраняется в строке участника один раз (при завершении встречи
или при первом формировании документа, если встреча старая) и больше не меняется: протокол полугодовой давности показывает должность того времени, даже если человек
сменил место работы, а его запись в каталоге удалена. Профиль пользователя при этом обновляется отдельно при следующих входах. Живой запрос к каталогу не выполняется.
Для гостей — имя и статус; должность и подразделение не придумываются.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GuestParticipant, Meeting, User

FIELDS = ("name", "title", "department", "email", "phone")


def snapshot_of(user: User) -> dict:
    """Только разрешённые поля карточки; пустое остаётся пустым (ничего не домысливается)."""
    return {"name": user.display_name, "title": user.title or "", "department": user.department or "", "email": user.email or "", "phone": user.phone or ""}


async def ensure_snapshots(db: AsyncSession, meeting: Meeting) -> int:
    """Дописывает снимок тем участникам, у кого его ещё нет (по текущему профилю). Уже сохранённые не трогает. Возвращает число дописанных."""
    n = 0
    for p in meeting.participants:
        if p.snapshot is None and p.user is not None:
            p.snapshot = snapshot_of(p.user)
            n += 1
    if n:
        await db.flush()
    return n


async def attendees(db: AsyncSession, meeting: Meeting) -> dict:
    """{"people": [{name, title, department, email, phone}], "guests": [{name, status}]} — люди в порядке первого входа, без повторов."""
    await ensure_snapshots(db, meeting)
    people: list[dict] = []
    seen: set = set()
    for p in sorted(meeting.participants, key=lambda x: x.joined_at):
        if p.user_id in seen:
            continue
        seen.add(p.user_id)
        snap = p.snapshot or (snapshot_of(p.user) if p.user is not None else {"name": "Участник"})
        people.append({k: str(snap.get(k) or "") for k in FIELDS})
    guests = (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id == meeting.id).order_by(GuestParticipant.joined_at))).scalars().all()
    return {"people": people, "guests": [{"name": g.label, "status": "Телефон" if g.is_phone else "Гость"} for g in guests]}
