"""Календарь Outlook внутри Peregovorka: согласие сотрудника, список событий (Exchange + встречи Peregovorka), создание, перенос и отмена встречи.

Принципы (docs/OUTLOOK_CALENDAR.md):
* Сервер читает и меняет календарь сотрудника, только если администратор включил раздел, сотрудник сам подключил календарь (`calendar_links`) и состоит в разрешённой группе.
  Успешный вход в Peregovorka прав на ящик Exchange не даёт: права проверяет сам Exchange (приложение Graph, имперсонация EWS) и ошибка отказа показывается как есть.
* Событие создаётся в ящике самого сотрудника (Graph `/users/{почта}/events`, EWS с имперсонацией этого ящика), организатор — он, приглашения рассылает Exchange.
* Существующие механизмы не дублируются: комнаты и приглашённые — `outlook.ensure/set_invitees`, сверка изменений и отмен из Outlook — `outlook_sync` (идентификаторы события записываются в комнату),
  «Предстоящие встречи» — `upcoming`.
* Повторный запрос не создаёт дубль: ключ запроса (`key`) — идентификатор комнаты, `transactionId` Graph и запись в Redis.
* Недоступность Exchange не ломает раздел: показываются последние известные события со значком «данные могли устареть» и встречи Peregovorka.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import CalendarLink, Meeting, Room, User, utcnow
from . import outlook as ol
from . import recurrence as rc
from . import upcoming as up
from .calendar_exchange import CalEvent, CalendarError, EventSpec, make_calendar_client
from .temp_rooms import TempRoomError

log = logging.getLogger("app.calendar")
MAX_RANGE = timedelta(days=45)           # месяц с хвостами соседних недель
CACHE_TTL_S = 60
STALE_TTL_S = 15 * 60
LIST_LIMIT, LIST_WINDOW_S = 40, 60       # запросов списка в минуту с одного пользователя
WRITE_LIMIT = 20
MAX_ATTENDEES = 200
_SLOT = asyncio.Semaphore(4)              # одновременных обращений к Exchange в одном процессе
_LOC_MEET = re.compile(r"/m/([a-z0-9]{20,32})(?![a-z0-9])")
_LOC_ROOM = re.compile(r"/rooms/([A-Za-z0-9_.-]{2,120})")
_EMAIL = ol.EMAIL_RE


class CalendarDenied(Exception):
    def __init__(self, code: str, message: str, status: int = 403):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------------------------------------------ допуск
def reason_off(cfg: Any, su: SessionUser, email: str) -> tuple[str, str] | None:
    """Почему календарь этому сотруднику недоступен: (код, пояснение) или None."""
    if not (cfg.enabled and cfg.calendar_enabled):
        return "disabled", "Раздел «Календарь» не включён администратором."
    if cfg.sync_mode == "off":
        return "no_connection", "Администратор не настроил подключение к Exchange (Администрирование → Microsoft Outlook → серверная сверка)."
    wanted = {g for g in (cfg.calendar_groups or "").split(";") if g}
    if wanted and not (su.is_admin or wanted & {g.lower() for g in su.groups}):
        return "group", "Календарь доступен только сотрудникам из выбранных администратором групп."
    if not email or not _EMAIL.match(email):
        return "no_email", "В вашем профиле нет адреса почты. Он берётся из каталога: обратитесь к администратору."
    return None


async def _user_email(db: AsyncSession, su: SessionUser) -> str:
    return ((await db.execute(select(User.email).where(User.id == su.user_id))).scalar_one_or_none() or "").strip().lower()


async def status(db: AsyncSession, su: SessionUser, cfg: Any) -> dict:
    email = await _user_email(db, su)
    off = reason_off(cfg, su, email)
    link = await db.get(CalendarLink, su.user_id)
    return {"available": off is None, "reason": off[0] if off else None, "message": off[1] if off else None, "connected": link is not None and off is None,
            "email": email or None, "can_write": bool(cfg.calendar_write and off is None and ol.allowed(cfg, su)), "mode": cfg.sync_mode,
            "guests_allowed": bool(cfg.guest_links), "last_ok_at": link.last_ok_at if link else None, "last_error": link.last_error if link else None}


# ------------------------------------------------------------------------------------------------ ограничения запросов
async def _limit(redis, key: str, limit: int, window: int) -> None:
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, window)
    if n > limit:
        raise CalendarDenied("rate", "Слишком много обращений к календарю. Подождите минуту.", 429)


def _cache_key(uid, a: datetime, b: datetime) -> str:
    return f"cal:ev:{uid}:{int(a.timestamp())}:{int(b.timestamp())}"


async def forget(redis, uid) -> None:
    """Сброс кэша событий сотрудника (после создания, переноса, отмены, смены согласия)."""
    async for k in redis.scan_iter(match=f"cal:ev:{uid}:*", count=100):
        await redis.delete(k)


# ------------------------------------------------------------------------------------------------ согласие
async def connect(db: AsyncSession, su: SessionUser, cfg: Any, redis, transport=None) -> dict:
    """Мастер подключения: проверяет, что Exchange отдаёт календарь именно этого ящика, и только затем записывает согласие. Пароль не запрашивается и не хранится."""
    email = await _user_email(db, su)
    off = reason_off(cfg, su, email)
    if off:
        raise CalendarDenied(off[0], off[1])
    await _limit(redis, f"cal:connect:{su.user_id}", 10, 600)
    try:
        client = make_calendar_client(cfg, transport)
        now = utcnow()
        async with _SLOT:
            await client.list_events(email, now, now + timedelta(days=1))
    except CalendarError as exc:
        raise CalendarDenied("exchange", f"Не удалось получить доступ к вашему календарю: {exc}", 502) from None
    link = await db.get(CalendarLink, su.user_id)
    if link is None:
        link = CalendarLink(user_id=su.user_id, email=email, consented_at=now)
        db.add(link)
    link.email, link.last_ok_at, link.last_error = email, now, None
    await db.flush()
    await forget(redis, su.user_id)
    return await status(db, su, cfg)


async def disconnect(db: AsyncSession, su: SessionUser, redis) -> None:
    link = await db.get(CalendarLink, su.user_id)
    if link is not None:
        await db.delete(link)
        await db.flush()
    await forget(redis, su.user_id)


async def _link_for(db: AsyncSession, su: SessionUser, cfg: Any) -> CalendarLink:
    email = await _user_email(db, su)
    off = reason_off(cfg, su, email)
    if off:
        raise CalendarDenied(off[0], off[1])
    link = await db.get(CalendarLink, su.user_id)
    if link is None:
        raise CalendarDenied("not_connected", "Подключите календарь: это разрешение на показ и ведение ваших встреч из Peregovorka.", 409)
    if link.email != email:                      # адрес в каталоге изменился: согласие относилось к другому ящику
        await db.delete(link)
        await db.commit()                        # удаление должно сохраниться, хотя запрос закончится отказом
        raise CalendarDenied("not_connected", "Адрес вашей почты в каталоге изменился. Подключите календарь заново.", 409)
    return link


# ------------------------------------------------------------------------------------------------ представление события
def _tz(name: str | None) -> str:
    try:
        ZoneInfo(name or "")
        return name or "UTC"
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"


async def _rooms_for(db: AsyncSession, events: list[CalEvent], base_host: str) -> tuple[dict, dict]:
    """Комнаты Peregovorka, к которым относятся события: по идентификатору события (запись надстройки или календаря) или по ссылке в поле «Место»."""
    ids = {e.id for e in events} | {e.series_id for e in events if e.series_id}
    codes, slugs = set(), set()
    for e in events:
        m = _LOC_MEET.search(e.location or "")
        if m and _host_ok(e.location, base_host):
            codes.add(ol.PREFIX + m.group(1))
        r = _LOC_ROOM.search(e.location or "")
        if r and _host_ok(e.location, base_host):
            slugs.add(r.group(1))
    by_id: dict[str, Room] = {}
    by_slug: dict[str, Room] = {}
    if ids:
        cond = or_(Room.outlook_rest_id.in_(ids), Room.outlook_ews_id.in_(ids), Room.outlook_series_id.in_(ids))
        for r in (await db.execute(select(Room).where(cond))).scalars():
            for k in (r.outlook_rest_id, r.outlook_ews_id, r.outlook_series_id):
                if k:
                    by_id[k] = r
    if codes or slugs:
        for r in (await db.execute(select(Room).where(Room.slug.in_(codes | slugs)))).scalars():
            by_slug[r.slug] = r
    return by_id, by_slug


def _host_ok(text: str, base_host: str) -> bool:
    m = re.search(r"https?://([^/\s]+)", text or "")
    return bool(m) and m.group(1).lower() == base_host


def _status(ev_cancelled: bool, start: datetime, end: datetime, now: datetime, room_state: str | None) -> str:
    if ev_cancelled or room_state == "cancelled":
        return "cancelled"
    if room_state == "live":
        return "live"
    if end < now:
        return "ended"
    return "scheduled"


def _view(e: CalEvent, rooms_by_id: dict, rooms_by_slug: dict, live: set, su: SessionUser, base: str, now: datetime, cfg: Any) -> dict:
    room = rooms_by_id.get(e.id) or (rooms_by_id.get(e.series_id) if e.series_id else None)
    if room is None:
        m = _LOC_MEET.search(e.location or "")
        room = rooms_by_slug.get(ol.PREFIX + m.group(1)) if m else None
    permanent = None
    if room is None:
        r = _LOC_ROOM.search(e.location or "")
        cand = rooms_by_slug.get(r.group(1)) if r else None
        if cand is not None and cand.lifetime != "temporary":
            permanent = cand
    kind = "event"
    if room is not None:
        kind = "series" if (room.recurring or e.kind != "single") else "meeting"
    elif permanent is not None:
        kind = "permanent"
    target = room or permanent
    mine = bool(room is not None and room.created_by_user_id == su.user_id)
    st = ol.state(room, now, meeting_active=room.id in live) if room is not None else None
    out = {"id": e.id, "source": "exchange", "title": e.subject or "Без темы", "start": e.start, "end": e.end, "all_day": e.all_day, "kind": kind,
           "status": _status(e.cancelled, e.start, e.end, now, st), "recurring": bool((room is not None and room.recurring) or e.kind != "single"), "location": e.location if not target else None, "etag": e.change,
           "room_id": str(room.id) if room is not None else None, "mine": mine,
           "join_url": (ol.join_link(base, room) if room is not None else (f"{base}/rooms/{permanent.slug}" if permanent is not None else None)),
           "guest_url": ol.guest_link(base, room, cfg) if (room is not None and mine) else None,
           "editable": bool(mine and room is not None and not room.recurring and room.cancelled_at is None and (room.outlook_rest_id or room.outlook_ews_id) and e.kind == "single")}
    return out


def _local_view(it: dict, su: SessionUser, base: str) -> dict:
    """Встреча Peregovorka, которой нет среди событий Exchange (например, создана надстройкой без идентификатора или календарь недоступен)."""
    status_ = "cancelled" if it["cancelled"] else "live" if it["live"] else "scheduled"
    return {"id": f"pg:{it['id']}", "source": "peregovorka", "title": it["name"], "start": it["start"], "end": it["end"] or it["start"], "all_day": False,
            "kind": "series" if it["recurring"] else "meeting", "status": status_, "recurring": it["recurring"], "location": None, "etag": None, "room_id": it["id"],
            "mine": False, "join_url": f"{base}{it['url']}", "guest_url": None, "editable": False}


# ------------------------------------------------------------------------------------------------ список событий
async def events(db: AsyncSession, su: SessionUser, cfg: Any, redis, start: datetime, end: datetime, *, base: str, refresh: bool = False, transport=None) -> dict:
    link = await _link_for(db, su, cfg)
    if end <= start or end - start > MAX_RANGE:
        raise CalendarDenied("bad_range", "Период запроса: от одного дня до 45 дней.", 422)
    await _limit(redis, f"cal:list:{su.user_id}", LIST_LIMIT, LIST_WINDOW_S)
    now = utcnow()
    key = _cache_key(su.user_id, start, end)
    cached = None if refresh else await redis.get(key)
    base_host = (urlsplit(base).netloc or "").lower()
    error: str | None = None
    items: list[CalEvent] = []
    stale = False
    if cached:
        items = [CalEvent(**{**d, "start": datetime.fromisoformat(d["start"]), "end": datetime.fromisoformat(d["end"])}) for d in json.loads(cached)]
    else:
        try:
            client = make_calendar_client(cfg, transport)
            async with _SLOT:
                items = await asyncio.wait_for(client.list_events(link.email, start, end), 45)
            link.last_ok_at, link.last_error = now, None
            blob = json.dumps([{**e.__dict__, "start": e.start.isoformat(), "end": e.end.isoformat()} for e in items], ensure_ascii=False)
            await redis.set(key, blob, ex=CACHE_TTL_S)
            await redis.set(f"cal:last:{su.user_id}:{int(start.timestamp())}:{int(end.timestamp())}", blob, ex=STALE_TTL_S)
        except (CalendarError, asyncio.TimeoutError) as exc:
            error = "Exchange не ответил вовремя." if isinstance(exc, asyncio.TimeoutError) else str(exc)
            link.last_error = error[:300]
            last = await redis.get(f"cal:last:{su.user_id}:{int(start.timestamp())}:{int(end.timestamp())}")
            if last:
                items = [CalEvent(**{**d, "start": datetime.fromisoformat(d["start"]), "end": datetime.fromisoformat(d["end"])}) for d in json.loads(last)]
                stale = True
    by_id, by_slug = await _rooms_for(db, items, base_host)
    related = list({r.id: r for r in [*by_id.values(), *by_slug.values()]}.values())
    live = set((await db.execute(select(Meeting.room_id).where(Meeting.room_id.in_([r.id for r in related]), Meeting.ended_at.is_(None)))).scalars().all()) if related else set()
    out = [_view(e, by_id, by_slug, live, su, base, now, cfg) for e in items]
    matched = {v["room_id"] for v in out if v["room_id"]}
    for it in await up.upcoming(db, su, now):                      # встречи Peregovorka без события Exchange — не дублируются, если уже показаны
        if it["kind"] != "scheduled" or not it["mine"] or it["id"] in matched or not (start <= it["start"] <= end):
            continue
        out.append(_local_view(it, su, base))
    out.sort(key=lambda v: (v["start"], v["title"]))
    await db.commit()
    return {"events": out, "from": start, "to": end, "server_time": now, "stale": stale, "error": error, "mode": cfg.sync_mode}


# ------------------------------------------------------------------------------------------------ создание
def _pattern(raw: Any, start: datetime, end: datetime, tz: str) -> dict | None:
    """Повторение от интерфейса → шаблон Peregovorka (recurrence.validate): ежедневное, еженедельное по дням, ежемесячное по числу."""
    if not isinstance(raw, dict) or raw.get("type") in (None, "", "none"):
        return None
    t = raw["type"]
    if t not in ("daily", "weekly", "monthly"):
        raise CalendarDenied("bad_recurrence", "Повторение: каждый день, каждую неделю или каждый месяц.", 422)
    local = start.astimezone(ZoneInfo(tz))
    p: dict[str, Any] = {"type": t, "interval": raw.get("interval") or 1, "start": local.date().isoformat(), "end": raw.get("end") or None, "count": raw.get("count") or None,
                         "time": local.strftime("%H:%M"), "minutes": max(1, int((end - start).total_seconds() // 60)), "tz": tz}
    if t == "weekly":
        p["days"] = raw.get("days") or [local.weekday()]
    if t == "monthly":
        p["day"] = raw.get("day") or local.day
    pat = rc.validate(p)
    if pat is None:
        raise CalendarDenied("bad_recurrence", "Повторение указано неверно: проверьте дни, интервал и дату окончания.", 422)
    return pat


def _attendees(raw: Any, own_email: str) -> list[tuple[str, str | None, bool]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ATTENDEES:
        raise CalendarDenied("bad_attendees", f"Участников не больше {MAX_ATTENDEES}.", 422)
    out = ol.parse_attendees(raw) or []
    return [a for a in out if a[0] != own_email]


async def create(db: AsyncSession, su: SessionUser, cfg: Any, redis, body: dict, *, base: str, settings: Any, encrypt_storage: bool, transport=None) -> dict:
    if not cfg.calendar_write:
        raise CalendarDenied("write_disabled", "Создание встреч из Peregovorka не включено администратором.")
    if not ol.allowed(cfg, su):
        raise CalendarDenied("forbidden", "Создавать встречи вам не разрешено.")
    link = await _link_for(db, su, cfg)
    await _limit(redis, f"cal:write:{su.user_id}", WRITE_LIMIT, 60)
    key = str(body.get("key") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,70}", key):
        raise CalendarDenied("bad_key", "Некорректный ключ запроса: обновите страницу.", 422)
    subject = " ".join(str(body.get("subject") or "").split())[:200]
    if not subject:
        raise CalendarDenied("bad_subject", "Укажите название встречи.", 422)
    tz = _tz(str(body.get("tz") or "UTC"))
    try:
        start = datetime.fromisoformat(str(body.get("start")).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(body.get("end")).replace("Z", "+00:00"))
    except ValueError:
        raise CalendarDenied("bad_time", "Время начала и окончания — в формате ISO 8601.", 422) from None
    pattern = _pattern(body.get("recurrence"), ol.aware(start), ol.aware(end), tz)
    now = utcnow()
    try:
        start, end = ol.validate_times(start, end, pattern is not None, now)
    except TempRoomError as exc:
        raise CalendarDenied(exc.code, exc.message, exc.status) from None
    attendees = _attendees(body.get("attendees"), link.email)
    conference = bool(body.get("conference", True))
    want_guest = bool(body.get("guest_link")) and conference and bool(cfg.guest_links)
    done_key = f"cal:created:{su.user_id}:{key}"
    prior = await redis.get(done_key)
    if prior and prior != "pending":
        return {**json.loads(prior), "duplicate": True}                       # повтор того же запроса (двойной щелчок, обрыв ответа): новое событие не создаётся
    if prior == "pending" or not await redis.set(done_key, "pending", nx=True, ex=70):
        raise CalendarDenied("busy", "Встреча уже создаётся. Подождите несколько секунд.", 409)       # тот же запрос ещё выполняется: параллельно второй раз не создаём
    room = None
    created_room = False
    spec = EventSpec(subject, start, end, tz, attendees, "", "", pattern, key)
    try:
        if conference:
            room, created_room = await ol.ensure(db, su, cfg, key="pgcal" + key, subject=subject, start=start, end=end, recurring=pattern is not None,
                                                 default_text_days=settings.default_text_retention_days, default_audio_days=settings.default_audio_retention_days, encrypt_storage=encrypt_storage)
            if created_room and not want_guest and room.guest_token:
                room.guest_token, room.guest_access_enabled = None, False       # гостевая ссылка — только если организатор её попросил
                await db.commit()
            guest = ol.guest_link(base, room, cfg) if want_guest else None
            spec.body_html, _ = ol.invite_texts(room, base, cfg, guest_url=guest)
            spec.location = ol.join_link(base, room)
        if room is not None and (room.outlook_rest_id or room.outlook_ews_id):      # комната этого запроса уже связана с событием: второе не создаётся
            created = None
        else:
            client = make_calendar_client(cfg, transport)
            async with _SLOT:
                created = await asyncio.wait_for(client.create_event(link.email, spec), 60)
    except (CalendarError, asyncio.TimeoutError) as exc:
        await redis.delete(done_key)
        if room is not None and created_room:
            await ol.cancel(db, room)                                              # события нет — пустая комната не остаётся
        msg = "Exchange не ответил вовремя: событие могло быть создано. Обновите календарь и проверьте, прежде чем создавать снова." if isinstance(exc, asyncio.TimeoutError) else str(exc)
        raise CalendarDenied("exchange", f"Встреча не создана: {msg}", 502) from None
    except TempRoomError as exc:
        await redis.delete(done_key)
        raise CalendarDenied(exc.code, exc.message, exc.status) from None
    if room is not None and created is not None:
        room.outlook_organizer = link.email
        if cfg.sync_mode == "graph":
            room.outlook_rest_id = created.id
        else:
            room.outlook_ews_id = created.id
        room.outlook_tz = tz
        room.outlook_synced_at = None
        if pattern is not None:
            room.outlook_recurrence, room.outlook_exceptions = pattern, None
        await ol.set_invitees(db, room, attendees, "calendar")
    await db.commit()
    await forget(redis, su.user_id)
    out = {"id": created.id if created else (room.outlook_rest_id or room.outlook_ews_id if room else None), "room_id": str(room.id) if room else None, "title": subject,
           "start": start.isoformat(), "end": end.isoformat(), "recurring": pattern is not None, "join_url": ol.join_link(base, room) if room else None,
           "guest_url": ol.guest_link(base, room, cfg) if room else None, "duplicate": False}
    await redis.set(done_key, json.dumps(out), ex=24 * 3600)
    return out


# ------------------------------------------------------------------------------------------------ перенос и отмена
async def _organizer_room(db: AsyncSession, su: SessionUser, room_id: uuid.UUID) -> Room:
    room = await db.get(Room, room_id)
    if room is None or room.scheduled_start is None or room.created_by_user_id != su.user_id:
        raise CalendarDenied("not_found", "Встреча не найдена или создана не вами.", 404)
    if not (room.outlook_rest_id or room.outlook_ews_id):
        raise CalendarDenied("not_linked", "Эта встреча не связана с событием в вашем календаре: измените её в Outlook.", 409)
    return room


async def reschedule(db: AsyncSession, su: SessionUser, cfg: Any, redis, room_id: uuid.UUID, body: dict, *, transport=None) -> dict:
    if not cfg.calendar_write:
        raise CalendarDenied("write_disabled", "Изменение встреч из Peregovorka не включено администратором.")
    link = await _link_for(db, su, cfg)
    await _limit(redis, f"cal:write:{su.user_id}", WRITE_LIMIT, 60)
    room = await _organizer_room(db, su, room_id)
    if room.recurring:
        raise CalendarDenied("series", "Серию встреч меняйте в Outlook: из Peregovorka изменяются только разовые встречи.", 409)
    subject = " ".join(str(body["subject"]).split())[:200] if body.get("subject") is not None else None
    if subject is not None and not subject:
        raise CalendarDenied("bad_subject", "Название не может быть пустым.", 422)
    start = end = None
    if body.get("start") is not None or body.get("end") is not None:
        try:
            start = datetime.fromisoformat(str(body.get("start")).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(body.get("end")).replace("Z", "+00:00"))
            start, end = ol.validate_times(start, end, False, utcnow())
        except ValueError:
            raise CalendarDenied("bad_time", "Время начала и окончания — в формате ISO 8601.", 422) from None
        except TempRoomError as exc:
            raise CalendarDenied(exc.code, exc.message, exc.status) from None
    if subject is None and start is None:
        raise CalendarDenied("nothing", "Нечего менять.", 422)
    eid = room.outlook_rest_id if cfg.sync_mode == "graph" else room.outlook_ews_id
    try:
        client = make_calendar_client(cfg, transport)
        async with _SLOT:
            res = await asyncio.wait_for(client.update_event(link.email, eid or "", body.get("etag") or None, subject=subject, start=start, end=end, tz=room.outlook_tz or "UTC"), 60)
    except CalendarError as exc:
        if str(exc) == "conflict":
            raise CalendarDenied("conflict", "Событие в календаре уже изменено (в Outlook или кем-то ещё). Обновите календарь и повторите изменение.", 409) from None
        raise CalendarDenied("exchange", f"Встреча не изменена: {exc}", 502) from None
    except asyncio.TimeoutError:
        raise CalendarDenied("exchange", "Exchange не ответил вовремя. Обновите календарь и проверьте событие.", 502) from None
    cfg_rs = await ol.reschedule(db, room, cfg, subject=subject, start=start or room.scheduled_start, end=end or room.scheduled_end, recurring=False)
    if res.id and res.id != eid:
        if cfg.sync_mode == "graph":
            room.outlook_rest_id = res.id
        else:
            room.outlook_ews_id = res.id
    room.outlook_synced_at = None
    await db.commit()
    await forget(redis, su.user_id)
    return {"room_id": str(room.id), "changed": bool(cfg_rs), "etag": res.change}


async def cancel(db: AsyncSession, su: SessionUser, cfg: Any, redis, room_id: uuid.UUID, *, transport=None) -> dict:
    if not cfg.calendar_write:
        raise CalendarDenied("write_disabled", "Отмена встреч из Peregovorka не включена администратором.")
    link = await _link_for(db, su, cfg)
    await _limit(redis, f"cal:write:{su.user_id}", WRITE_LIMIT, 60)
    room = await _organizer_room(db, su, room_id)
    eid = room.outlook_rest_id if cfg.sync_mode == "graph" else room.outlook_ews_id
    try:
        client = make_calendar_client(cfg, transport)
        async with _SLOT:
            await asyncio.wait_for(client.cancel_event(link.email, eid or "", "Встреча отменена организатором."), 60)
    except CalendarError as exc:
        raise CalendarDenied("exchange", f"Встреча не отменена: {exc}", 502) from None
    except asyncio.TimeoutError:
        raise CalendarDenied("exchange", "Exchange не ответил вовремя. Обновите календарь и проверьте событие.", 502) from None
    await ol.cancel(db, room)
    await db.commit()
    await forget(redis, su.user_id)
    return {"room_id": str(room.id), "cancelled": True}
