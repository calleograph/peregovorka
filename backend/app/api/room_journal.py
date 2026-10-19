"""Журнал действий комнаты: события всех её встреч в одном месте (для руководителя этой комнаты и администратора).

Источники — существующие: аудит (`audit_log`: вход/выход, выдача и отзыв доступа, создание, правка и удаление материалов, настройки рассылки, скачивания), письма (`mail_messages`: кому, когда,
доставлено или нет) и сами встречи (начало и завершение). Новой параллельной таблицы нет. Время хранится в UTC; интерфейс показывает его в выбранном часовом поясе. Пароли, токены и содержимое
материалов в журнал не попадают (аудит чистится `scrub`, письма — только получатель и результат). Обычному участнику журнал недоступен.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, get_db, require_user
from ..models import AuditLog, MailMessage, Meeting, Room
from ..services import roles

router = APIRouter(prefix="/rooms/{room_id}/journal", tags=["room-journal"])

LABELS = {
    "meeting.join": ("Вход в комнату", "participants"), "meeting.leave": ("Выход из комнаты", "participants"), "meeting.end": ("Встреча завершена", "meeting"),
    "meeting.force_end": ("Встреча завершена администратором", "meeting"), "meeting.join_denied": ("Вход отклонён", "participants"), "meeting.kick": ("Участник удалён из встречи", "participants"),
    "meeting.grant": ("Доступ к встрече выдан", "access"), "meeting.grant_group": ("Доступ к встрече выдан группе", "access"), "meeting.revoke": ("Доступ к встрече отозван", "access"),
    "meeting.settings.update": ("Изменены настройки встречи", "settings"), "meeting.materials.send": ("Материалы отправлены вручную", "mail"), "meeting.recording": ("Запись включена/выключена", "meeting"),
    "meeting.transcription": ("Транскрибация включена/выключена", "meeting"), "protocol.create": ("Материал сформирован или сформирован заново", "materials"), "protocol.edit": ("Материал изменён", "materials"),
    "protocol.delete": ("Материал удалён", "materials"), "protocol.cancel": ("Формирование материала отменено", "materials"), "protocol.export": ("Материал скачан или экспортирован", "materials"),
    "map.create": ("Карта разговора сформирована", "materials"), "map.recreate": ("Карта разговора сформирована заново", "materials"), "map.edit_topic": ("Карта разговора изменена", "materials"),
    "map.delete": ("Карта разговора удалена", "materials"), "map.export_html": ("Карта разговора скачана", "materials"), "transcript.download": ("Стенограмма скачана", "materials"),
    "recording.download": ("Запись скачана", "files"), "recording.play": ("Запись прослушана", "files"), "recording.delete": ("Запись удалена", "files"),
    "room.manage.update": ("Изменены настройки комнаты", "settings"), "room.update": ("Изменены настройки комнаты администратором", "settings"),
}
KINDS = {"participants": "Участники", "meeting": "Встреча", "access": "Доступ", "materials": "Материалы", "mail": "Рассылка", "settings": "Настройки", "files": "Файлы"}


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@router.get("")
async def journal(room_id: uuid.UUID, request: Request, date_from: datetime | None = Query(default=None, alias="from"), date_to: datetime | None = Query(default=None, alias="to"),
                  meeting_id: uuid.UUID | None = None, user: str | None = Query(default=None, max_length=100), kind: str | None = Query(default=None, max_length=20),
                  limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    room = await db.get(Room, room_id)
    if room is None or not roles.can_manage_room(room, su):
        raise HTTPException(status_code=404, detail="Комната не найдена или доступ закрыт")        # обычный пользователь и руководитель ДРУГОЙ комнаты ничего не узнают
    meetings = (await db.execute(select(Meeting).where(Meeting.room_id == room_id).order_by(Meeting.started_at.desc()).limit(2000))).scalars().unique().all()
    ids = {str(m.id): m for m in meetings}
    rid = str(room_id)
    if meeting_id is not None and str(meeting_id) not in ids:
        raise HTTPException(status_code=404, detail="Встреча не найдена в этой комнате")
    events: list[dict] = []
    # --- аудит
    cond = or_(AuditLog.details["room_id"].as_string() == rid, and_(AuditLog.target_type == "room", AuditLog.target_id == rid),
               and_(AuditLog.target_type == "meeting", AuditLog.target_id.in_(list(ids) or ["-"])))
    q = select(AuditLog).where(cond).order_by(AuditLog.at.desc(), AuditLog.id.desc())
    if date_from:
        q = q.where(AuditLog.at >= _aware(date_from))
    if date_to:
        q = q.where(AuditLog.at <= _aware(date_to))
    for a in (await db.execute(q.limit(1500))).scalars().all():
        label, k = LABELS.get(a.action, (a.action, "other"))
        d = a.details or {}
        mid = d.get("meeting_id") or (a.target_id if a.target_type == "meeting" else None)
        events.append({"at": _aware(a.at).isoformat(), "kind": k, "action": a.action, "title": label, "actor": a.actor_name, "meeting_id": mid, "result": a.result,
                       "details": {x: d[x] for x in ("user", "via", "group", "members", "added", "rights", "by", "before", "after", "kinds", "count") if x in d}})
    # --- письма
    mq = select(MailMessage).where(MailMessage.meeting_id.in_([m.id for m in meetings] or [uuid.UUID(int=0)])).order_by(MailMessage.created_at.desc()).limit(1500)
    for r in (await db.execute(mq)).scalars().all():
        if date_from and _aware(r.created_at) < _aware(date_from) or date_to and _aware(r.created_at) > _aware(date_to):
            continue
        ok = r.state == "sent"
        events.append({"at": _aware(r.sent_at or r.created_at).isoformat(), "kind": "mail", "action": "mail.sent" if ok else f"mail.{r.state}",
                       "title": "Письмо отправлено" if ok else "Письмо в очереди" if r.state in ("queued", "sending") else "Письмо не доставлено", "actor": r.requested_by or "система",
                       "meeting_id": str(r.meeting_id) if r.meeting_id else None, "result": "success" if ok else "failure" if r.state == "failed" else "pending",
                       "details": {"recipient": r.recipient, "kinds": list(r.kinds or []), "trigger": r.trigger, **({"error": r.last_error} if r.last_error else {})}})
    # --- начало и завершение встреч
    for m in meetings:
        for when, title, who in ((m.started_at, "Встреча началась", m.started_by.display_name if getattr(m, "started_by", None) else "—"), (m.ended_at, "Встреча завершена", "система")):
            if not when:
                continue
            if date_from and _aware(when) < _aware(date_from) or date_to and _aware(when) > _aware(date_to):
                continue
            if title == "Встреча завершена" and any(e["action"] in ("meeting.end", "meeting.force_end") and e["meeting_id"] == str(m.id) for e in events):
                continue
            events.append({"at": _aware(when).isoformat(), "kind": "meeting", "action": "meeting.start" if title == "Встреча началась" else "meeting.ended", "title": title, "actor": who,
                           "meeting_id": str(m.id), "result": "success", "details": {}})
    if meeting_id:
        events = [e for e in events if e["meeting_id"] == str(meeting_id)]
    if user:
        u = user.lower()
        events = [e for e in events if u in (e["actor"] or "").lower() or u in str(e["details"].get("user", "")).lower() or u in str(e["details"].get("recipient", "")).lower()]
    if kind:
        events = [e for e in events if e["kind"] == kind]
    events.sort(key=lambda e: e["at"], reverse=True)
    names = {mid: m.started_at.isoformat() for mid, m in ids.items()}
    return {"items": events[offset:offset + limit], "total": len(events), "kinds": KINDS, "meetings": [{"id": mid, "started_at": t} for mid, t in list(names.items())[:200]]}
