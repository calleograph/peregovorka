"""Интеграция с календарём Outlook: запланированные временные комнаты, создаваемые надстройкой (Office.js).

Надстройка (`frontend/public/outlook/`) по кнопке «Добавить конференцию Peregovorka» обращается к `/api/v1/outlook/*` с одноразово выданным токеном (вход — в окне
Office dialog на странице нашего же сервера, поэтому работают пароль, 2FA и CAPTCHA). Здесь — токены, идемпотентное создание комнаты по ключу события, перенос,
отмена, окно предварительного входа и сведения для страницы обратного отсчёта.

Ключ события — случайная строка, которую надстройка сохраняет в пользовательских свойствах письма-встречи: повторное нажатие кнопки, несохранённый черновик и
удалённая из приглашения ссылка дают ту же комнату (вернётся существующая связь, дубля нет). Все моменты времени хранятся в UTC.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser
from ..models import Room, RoomAcl, RoomInvitee, RoomModerator, User, utcnow
from .rooms import slug_taken
from .settings import GeneralSettings, OutlookSettings
from .temp_rooms import TempRoomError, close as close_room

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PREFIX = "ol-"
CODE_LEN = 20
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
TOKEN_PREFIX = "pgo_"
MAX_TOKEN_LIFETIME_S = 30 * 24 * 3600         # продление входа надстройки не делает его бессрочным: через 30 дней от входа — снова пароль
MAX_SINGLE = timedelta(hours=24)
MAX_AHEAD = timedelta(days=730)
MAX_INVITEES = 500
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,120}@[A-Za-z0-9.-]{1,120}\.[A-Za-z0-9-]{2,63}$")


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


# ------------------------------------------------------------------------------------------------ токены надстройки
def _tok_key(token: str) -> str:
    return "outlook:tok:" + hashlib.sha256(token.encode()).hexdigest()


async def issue_token(redis, su: SessionUser, ttl_hours: int, *, born: float | None = None) -> tuple[str, int]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    payload = {"uid": str(su.user_id), "ad": su.ad_guid, "sam": su.sam_account_name, "name": su.display_name, "admin": su.is_admin, "groups": sorted(su.groups), "local": su.local,
               "born": born or utcnow().timestamp()}
    ttl = ttl_hours * 3600
    if born:                                                              # продление: не дольше предела от первоначального входа
        ttl = min(ttl, int(born + MAX_TOKEN_LIFETIME_S - utcnow().timestamp()))
    await redis.set(_tok_key(token), json.dumps(payload), ex=max(ttl, 1))
    return token, max(ttl, 1)


async def refresh_token(redis, token: str, ttl_hours: int) -> tuple[str, int] | None:
    """Продление входа надстройки: новый токен с тем же пользователем, прежний отзывается сразу. None — токен недействителен или исчерпан предел от первоначального входа."""
    su = await principal(redis, token)
    if su is None:
        return None
    born = float(json.loads(await redis.get(_tok_key(token)) or "{}").get("born") or utcnow().timestamp())
    if born + MAX_TOKEN_LIFETIME_S - utcnow().timestamp() < 3600:
        return None
    new = await issue_token(redis, su, ttl_hours, born=born)
    await revoke(redis, token)
    return new


async def principal(redis, token: str) -> SessionUser | None:
    """Пользователь по токену надстройки (только для `/outlook/*`; общий доступ к API токен не даёт)."""
    if not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        return None
    raw = await redis.get(_tok_key(token))
    if not raw:
        return None
    d = json.loads(raw)
    return SessionUser(session_id="outlook", user_id=uuid.UUID(d["uid"]), ad_guid=d["ad"], sam_account_name=d["sam"], display_name=d["name"], is_admin=bool(d["admin"]),
                       groups=frozenset(d["groups"]), csrf="", local=bool(d["local"]))


async def revoke(redis, token: str) -> None:
    await redis.delete(_tok_key(token))


# ------------------------------------------------------------------------------------------------ политика и время
def allowed(cfg: OutlookSettings, su: SessionUser) -> bool:
    """Кто может создавать конференции из Outlook: пустой список групп — любой сотрудник; иначе администратор или член группы."""
    wanted = {g for g in cfg.allowed_groups.split(";") if g}
    return su.is_admin or not wanted or bool(wanted & {g.lower() for g in su.groups})


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def opens_at(start: datetime, early_minutes: int) -> datetime:
    return aware(start) - timedelta(minutes=early_minutes)


def validate_times(start: datetime, end: datetime, recurring: bool, now: datetime) -> tuple[datetime, datetime]:
    start, end = aware(start).astimezone(timezone.utc), aware(end).astimezone(timezone.utc)
    if end <= start:
        raise TempRoomError("bad_time", "Окончание встречи должно быть позже начала.", 422)
    if not recurring and end - start > MAX_SINGLE:
        raise TempRoomError("too_long", "Встреча дольше суток: для длинных событий создайте серию.", 422)
    if end < now - timedelta(hours=1):
        raise TempRoomError("in_past", "Встреча уже закончилась.", 422)
    if start > now + MAX_AHEAD:
        raise TempRoomError("too_far", "Встречу нельзя запланировать больше чем на два года вперёд.", 422)
    return start, end


def _auto_close(end: datetime, recurring: bool, cfg: OutlookSettings, now: datetime) -> datetime:
    # серия живёт, пока её не закроют вручную (до двух лет): отдельные встречи серии открываются без календарных ограничений
    return now + MAX_AHEAD if recurring else end + timedelta(minutes=cfg.after_end_minutes)


# ------------------------------------------------------------------------------------------------ комнаты
async def active_count(db: AsyncSession, user_id) -> int:
    return (await db.execute(select(func.count()).select_from(Room).where(Room.created_by_user_id == user_id, Room.scheduled_start.is_not(None), Room.lifecycle != "closed"))).scalar_one()


async def find_by_key(db: AsyncSession, user_id, key: str) -> Room | None:
    return (await db.execute(select(Room).where(Room.created_by_user_id == user_id, Room.outlook_key == key))).scalars().first()


async def owned(db: AsyncSession, su: SessionUser, room_id: uuid.UUID) -> Room:
    room = await db.get(Room, room_id)
    if room is None or room.scheduled_start is None or not (su.is_admin or room.created_by_user_id == su.user_id):
        raise TempRoomError("not_found", "Встреча не найдена.", 404)
    return room


async def ensure(db: AsyncSession, su: SessionUser, cfg: OutlookSettings, *, key: str, subject: str, start: datetime, end: datetime, recurring: bool,
                 default_text_days: int | None = None, default_audio_days: int | None = None, encrypt_storage: bool = False, instance: bool = False) -> tuple[Room, bool]:
    """Создаёт запланированную комнату или возвращает уже связанную с этим событием (идемпотентно). Отменённую или закрытую по ошибке комнату,
    у которой встреча ещё не закончилась, повторное нажатие кнопки возвращает в работу.

    instance — изменено одно занятие серии (Office.js `seriesId`): комнату серии оно не переносит (время, тема, связь с событием остаются от серии;
    перенос и отмену занятия сообщает календарь при сверке). Новая комната для одного занятия — разовая встреча."""
    if not cfg.enabled:
        raise TempRoomError("disabled", "Интеграция с Outlook отключена администратором.", 403)
    if not allowed(cfg, su):
        raise TempRoomError("forbidden", "Создавать конференции из Outlook вам не разрешено.", 403)
    if not KEY_RE.match(key or ""):
        raise TempRoomError("bad_key", "Некорректный ключ события.", 422)
    now = utcnow()
    room = await find_by_key(db, su.user_id, key)
    if room is not None and instance and room.recurring:
        return room, False
    start, end = validate_times(start, end, recurring, now)
    subject = " ".join((subject or "").split())[:200] or "Встреча"
    if room is not None:
        await reschedule(db, room, cfg, subject=subject, start=start, end=end, recurring=recurring, now=now)
        return room, False
    if await active_count(db, su.user_id) >= cfg.max_active_per_user:
        raise TempRoomError("limit_user", f"У вас уже {cfg.max_active_per_user} запланированных конференций — закройте ненужные.", 409)
    for _ in range(8):
        code = new_code()
        if await slug_taken(db, PREFIX + code):
            continue
        room = Room(slug=PREFIX + code, name=subject, lifetime="temporary", lifecycle="active", history_access="participants", created_by_user_id=su.user_id,
                    created_by_name=su.display_name, text_retention_days=default_text_days, audio_retention_days=default_audio_days, peer_access="link",
                    scheduled_start=start, scheduled_end=end, join_opens_at=opens_at(start, cfg.early_minutes), outlook_key=key, recurring=recurring,
                    auto_close_at=_auto_close(end, recurring, cfg, now), camera_allowed=True, screen_share_allowed=True, encrypt_storage=encrypt_storage)
        if cfg.guest_links:
            room.guest_access_enabled = True
            room.guest_token = secrets.token_urlsafe(24)
        if su.ad_guid and not su.local:
            ref = su.ad_guid.lower()
            room.acl = [RoomAcl(subject_type="user", subject_ref=ref, display_name=su.display_name)]
            room.moderators = [RoomModerator(subject_type="user", subject_ref=ref, display_name=su.display_name)]
        db.add(room)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await find_by_key(db, su.user_id, key)            # гонка двух одинаковых запросов: вернём победителя
            if existing is not None:
                return existing, False
            continue
        await db.commit()
        await db.refresh(room)
        return room, True
    raise TempRoomError("no_code", "Не удалось подобрать адрес комнаты, повторите.", 503)


async def reschedule(db: AsyncSession, room: Room, cfg: OutlookSettings, *, subject: str | None, start: datetime, end: datetime, recurring: bool, now: datetime | None = None) -> bool:
    """Перенос/изменение события: новое время, название, окно входа и срок закрытия. Возвращает, изменилось ли что-то."""
    now = now or utcnow()
    start, end = validate_times(start, end, recurring, now)
    before = (room.name, room.scheduled_start, room.scheduled_end, room.recurring, room.cancelled_at, room.lifecycle)
    if subject:
        room.name = subject
    room.scheduled_start, room.scheduled_end, room.recurring = start, end, recurring
    room.join_opens_at = opens_at(start, cfg.early_minutes)
    if room.lifecycle == "closed" or room.cancelled_at is not None:
        if room.cancelled_at is not None or room.closed_at is not None:     # встречу перенесли в будущее или кнопку нажали снова — возвращаем в работу
            room.lifecycle, room.closed_at, room.cancelled_at = "active", None, None
    room.auto_close_at = _auto_close(end, recurring, cfg, now)
    after = (room.name, room.scheduled_start, room.scheduled_end, room.recurring, room.cancelled_at, room.lifecycle)
    if after != before:
        await db.commit()
        await db.refresh(room)
        return True
    return False


# ------------------------------------------------------------------------------------------------ приглашённые
def parse_attendees(raw) -> list[tuple[str, str | None, bool]] | None:
    """Приглашённые из надстройки или календаря: [(адрес строчными, имя, необязательный)] без повторов и неверных адресов. None — сведений нет:
    список тогда не трогаем (старая надстройка, не удалось прочитать)."""
    if not isinstance(raw, list):
        return None
    out: list[tuple[str, str | None, bool]] = []
    seen: set[str] = set()
    for a in raw[:MAX_INVITEES]:
        if not isinstance(a, dict):
            continue
        email = str(a.get("email") or "").strip().lower()
        if not EMAIL_RE.match(email) or email in seen:
            continue
        seen.add(email)
        name = " ".join(str(a.get("name") or "").split())[:300] or None
        out.append((email, None if name and name.lower() == email else name, bool(a.get("optional"))))
    return out


async def set_invitees(db: AsyncSession, room: Room, attendees: list[tuple[str, str | None, bool]] | None, source: str, *, prune: bool = True) -> bool:
    """Список приглашённых — последний подтверждённый (кнопка или отправка приглашения в Outlook, серверная сверка с календарём). Организатор в список
    не входит. prune=False — только добавить и обновить (календарь вернул не все адреса). Коммит — у вызывающего. Возвращает, изменилось ли что-то."""
    if attendees is None:
        return False
    org = (room.outlook_organizer or "").lower()
    want = {e: (n, o) for e, n, o in attendees if e != org}
    have = {i.email: i for i in (await db.execute(select(RoomInvitee).where(RoomInvitee.room_id == room.id))).scalars()}
    users: dict[str, uuid.UUID] = {}
    if want:
        for uid, mail in (await db.execute(select(User.id, User.email).where(func.lower(User.email).in_(list(want))))).all():
            users[mail.lower()] = uid
    changed = False
    for email, inv in have.items():
        if (prune and email not in want) or (inv.user_id is not None and inv.user_id == room.created_by_user_id):
            await db.delete(inv)
            changed = True
    for email, (name, optional) in want.items():
        uid = users.get(email)
        if uid is not None and uid == room.created_by_user_id:          # организатор под другим адресом
            continue
        inv = have.get(email)
        if inv is None:
            db.add(RoomInvitee(room_id=room.id, email=email, name=name, user_id=uid, optional=optional, source=source))
            changed = True
        elif (inv.name, inv.user_id, inv.optional) != (name or inv.name, uid or inv.user_id, optional):
            inv.name, inv.user_id, inv.optional, inv.source = name or inv.name, uid or inv.user_id, optional, source
            changed = True
    return changed


def apply_recurrence(room: Room, raw) -> bool:
    """Шаблон серии от надстройки (Office.js): у разовой встречи сбрасывается; неверный или отсутствующий — прежний не трогаем.
    Новый шаблон — прежние исключения серии недействительны (Outlook при изменении шаблона их сбрасывает): календарь пришлёт свежие при ближайшей сверке."""
    from . import recurrence as rc  # noqa: PLC0415

    if not room.recurring:
        changed = room.outlook_recurrence is not None or room.outlook_exceptions is not None
        room.outlook_recurrence = room.outlook_exceptions = None
        return changed
    pat = rc.from_office(raw)
    if pat is None or pat == room.outlook_recurrence:
        return False
    if room.outlook_recurrence is not None:
        room.outlook_exceptions, room.outlook_synced_at = None, None
    room.outlook_recurrence = pat
    return True


async def cancel(db: AsyncSession, room: Room) -> bool:
    if room.cancelled_at is not None:
        return False
    room.cancelled_at = utcnow()
    close_room(room, "cancelled")
    await db.commit()
    return True


# ------------------------------------------------------------------------------------------------ состояние
def state(room: Room, now: datetime | None = None, *, meeting_active: bool = False, cfg: OutlookSettings | None = None) -> str:
    """cancelled | waiting | open | live | ended — для страницы обратного отсчёта и ограничения входа."""
    now = aware(now or utcnow())
    if room.cancelled_at is not None:
        return "cancelled"
    if meeting_active:
        return "live"
    if room.lifecycle == "closed":
        return "ended"
    if room.recurring or room.scheduled_start is None:
        return "open"
    if room.join_opens_at is not None and now < aware(room.join_opens_at):
        return "waiting"
    return "open"


def gate_message(room: Room) -> str:
    opens = aware(room.join_opens_at).strftime("%d.%m.%Y %H:%M UTC") if room.join_opens_at else ""
    return f"Конференция ещё не открыта: подключиться можно с {opens}. Откройте ссылку позже — страница покажет обратный отсчёт."


def is_too_early(room: Room, now: datetime | None = None) -> bool:
    return bool(room.scheduled_start is not None and not room.recurring and room.cancelled_at is None and room.join_opens_at is not None
                and aware(now or utcnow()) < aware(room.join_opens_at))


def public_url(app_public_url: str, cfg: OutlookSettings) -> str:
    return (cfg.public_url or app_public_url or "").rstrip("/")


def join_link(base: str, room: Room) -> str:
    return f"{base}/m/{room.slug.removeprefix(PREFIX)}"


def guest_link(base: str, room: Room, cfg: OutlookSettings) -> str | None:
    """Гостевая ссылка, если организация разрешила гостей во встречах Outlook и у комнаты она выпущена (не отозвана). Иначе None."""
    if not (cfg.guest_links and room.guest_access_enabled and room.guest_token):
        return None
    return f"{base}/guest/{room.guest_token}"


def invite_texts(room: Room, base: str, cfg: OutlookSettings, *, guest_url: str | None = None) -> tuple[str, str]:
    """Блок для письма-приглашения: HTML (в тело встречи) и обычный текст (для запасных клиентов). Две ссылки: для сотрудников (вход учётной записью организации)
    и, если разрешены гости, отдельная гостевая — с собственным секретом, поэтому сотрудник не раздаёт ссылку, по которой входят по доменной учётной записи."""
    url = join_link(base, room)
    early = cfg.early_minutes
    timing = f"Вход открывается за {early} мин до начала. Перед входом проверьте камеру и микрофон." if not room.recurring else "Вход открыт в любое время; встреча начинается в назначенное время."
    lines = ["Подключение к встрече Peregovorka", "", "Для сотрудников организации (вход учётной записью):", url]
    html = ['<br><p><b>Подключение к встрече Peregovorka</b></p>',
            f'<p><b>Для сотрудников организации</b> (вход учётной записью):<br><a href="{escape(url, {chr(34): "&quot;"})}" target="_blank">{escape(url)}</a></p>']
    if guest_url:
        note = "Если у участника нет корпоративной учётной записи, передайте ему гостевую ссылку."
        lines += ["", "Для внешних участников (без учётной записи):", guest_url, note]
        html.append(f'<p><b>Для внешних участников</b> (без учётной записи):<br><a href="{escape(guest_url, {chr(34): "&quot;"})}" target="_blank">{escape(guest_url)}</a><br><i>{escape(note)}</i></p>')
    lines += ["", timing]
    html.append(f"<p>{escape(timing)}</p><br>")
    return "".join(html), "\n".join(lines)


def public_view(room: Room, st: str, *, now: datetime, cfg: OutlookSettings) -> dict:
    """Сведения для страницы обратного отсчёта (без входа в систему): название, время, состояние; гостевой токен — только когда вход уже открыт."""
    guest_ok = bool(room.guest_access_enabled and room.guest_token and cfg.guest_links)
    return {"name": room.name, "state": st, "start": room.scheduled_start, "end": room.scheduled_end, "opens_at": room.join_opens_at, "recurring": room.recurring,
            "early_minutes": cfg.early_minutes, "server_time": aware(now), "slug": room.slug, "guests": guest_ok,
            "guest_token": room.guest_token if guest_ok and st in ("open", "live") else None}


# ------------------------------------------------------------------------------------------------ манифест
def addin_id(base: str) -> str:
    """Постоянный идентификатор надстройки для адреса (не меняется между версиями и скачиваниями)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "peregovorka-outlook:" + base))


def manifest_xml(base: str, version: str, *, sync_on_send: bool = False, officejs: str = "local") -> str:
    """Манифест надстройки (add-in only manifest). Кнопка «Добавить конференцию» открывает боковую панель (ShowTaskpane, Mailbox 1.3): видно, что происходит, есть вход,
    ошибки и повтор. Идентификатор стабилен для адреса, поэтому обновление манифеста заменяет надстройку. FunctionFile остаётся (обязателен для DesktopFormFactor,
    обработчик отправки и кнопка старых манифестов живут в commands.js)."""
    gid = addin_id(base)
    n = [int(x) for x in re.findall(r"\d+", version)[:3]] + [0, 0, 0]
    v = f"{n[0] + 1}.{n[1]}.{n[2]}.0"                 # Office требует версию манифеста не ниже 1.0: старшая часть увеличена на единицу (растёт вместе с версией приложения)
    b = escape(base)
    q = "" if officejs == "local" else f"?officejs={escape(officejs)}"      # источник Office.js страниц надстройки (frontend/public/outlook/ms-loader.js); по умолчанию — с сервера
    # Smart Alerts (OnAppointmentSend, Mailbox 1.12): при отправке приглашения или обновления время и тема уходят на сервер. Подпись обработчика никогда не блокирует отправку.
    rt = '''
        <Runtimes>
          <Runtime resid="residRuntime">
            <Override type="javascript" resid="residScript"/>
          </Runtime>
        </Runtimes>''' if sync_on_send else ""
    launch = '''
            <ExtensionPoint xsi:type="LaunchEvent">
              <LaunchEvents>
                <LaunchEvent Type="OnAppointmentSend" FunctionName="onAppointmentSendHandler" SendMode="SoftBlock"/>
              </LaunchEvents>
              <SourceLocation resid="residRuntime"/>
            </ExtensionPoint>''' if sync_on_send else ""
    urls = f'\n          <bt:Url id="residRuntime" DefaultValue="{b}/outlook/commands.html{q}"/>\n          <bt:Url id="residScript" DefaultValue="{b}/outlook/commands.js"/>' if sync_on_send else ""
    min_set = "1.12" if sync_on_send else "1.3"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0" xmlns:mailappor="http://schemas.microsoft.com/office/mailappversionoverrides/1.0" xsi:type="MailApp">
  <Id>{gid}</Id>
  <Version>{v}</Version>
  <ProviderName>calleograph</ProviderName>
  <DefaultLocale>ru-RU</DefaultLocale>
  <DisplayName DefaultValue="Peregovorka"/>
  <Description DefaultValue="Видеоконференция Peregovorka в приглашении Outlook"/>
  <IconUrl DefaultValue="{b}/outlook/icon-64.png"/>
  <HighResolutionIconUrl DefaultValue="{b}/outlook/icon-128.png"/>
  <SupportUrl DefaultValue="{b}"/>
  <AppDomains>
    <AppDomain>{b}</AppDomain>
  </AppDomains>
  <Hosts>
    <Host Name="Mailbox"/>
  </Hosts>
  <Requirements>
    <Sets>
      <Set Name="Mailbox" MinVersion="1.3"/>
    </Sets>
  </Requirements>
  <FormSettings>
    <Form xsi:type="ItemRead">
      <DesktopSettings>
        <SourceLocation DefaultValue="{b}/outlook/commands.html{q}"/>
        <RequestedHeight>250</RequestedHeight>
      </DesktopSettings>
    </Form>
  </FormSettings>
  <Permissions>ReadWriteItem</Permissions>
  <Rule xsi:type="RuleCollection" Mode="Or">
    <Rule xsi:type="ItemIs" ItemType="Appointment" FormType="Edit"/>
  </Rule>
  <DisableEntityHighlighting>false</DisableEntityHighlighting>
  <VersionOverrides xmlns="http://schemas.microsoft.com/office/mailappversionoverrides" xsi:type="VersionOverridesV1_0">
    <VersionOverrides xmlns="http://schemas.microsoft.com/office/mailappversionoverrides/1.1" xsi:type="VersionOverridesV1_1">
      <Description resid="residDescription"/>
      <Requirements>
        <bt:Sets>
          <bt:Set Name="Mailbox" MinVersion="{min_set}"/>
        </bt:Sets>
      </Requirements>
      <Hosts>
        <Host xsi:type="MailHost">{rt}
          <DesktopFormFactor>
            <FunctionFile resid="residFunctionFile"/>
            <ExtensionPoint xsi:type="AppointmentOrganizerCommandSurface">
              <OfficeTab id="TabDefault">
                <Group id="apptComposeGroup">
                  <Label resid="residDescription"/>
                  <Control xsi:type="Button" id="insertMeetingButton">
                    <Label resid="residLabel"/>
                    <Supertip>
                      <Title resid="residLabel"/>
                      <Description resid="residTooltip"/>
                    </Supertip>
                    <Icon>
                      <bt:Image size="16" resid="icon-16"/>
                      <bt:Image size="20" resid="icon-20"/>
                      <bt:Image size="24" resid="icon-24"/>
                      <bt:Image size="32" resid="icon-32"/>
                      <bt:Image size="40" resid="icon-40"/>
                      <bt:Image size="48" resid="icon-48"/>
                      <bt:Image size="64" resid="icon-64"/>
                      <bt:Image size="80" resid="icon-80"/>
                    </Icon>
                    <Action xsi:type="ShowTaskpane">
                      <SourceLocation resid="residTaskpane"/>
                    </Action>
                  </Control>
                </Group>
              </OfficeTab>
            </ExtensionPoint>{launch}
          </DesktopFormFactor>
        </Host>
      </Hosts>
      <Resources>
        <bt:Images>
          <bt:Image id="icon-16" DefaultValue="{b}/outlook/icon-16.png"/>
          <bt:Image id="icon-20" DefaultValue="{b}/outlook/icon-20.png"/>
          <bt:Image id="icon-24" DefaultValue="{b}/outlook/icon-24.png"/>
          <bt:Image id="icon-32" DefaultValue="{b}/outlook/icon-32.png"/>
          <bt:Image id="icon-40" DefaultValue="{b}/outlook/icon-40.png"/>
          <bt:Image id="icon-48" DefaultValue="{b}/outlook/icon-48.png"/>
          <bt:Image id="icon-64" DefaultValue="{b}/outlook/icon-64.png"/>
          <bt:Image id="icon-80" DefaultValue="{b}/outlook/icon-80.png"/>
        </bt:Images>
        <bt:Urls>
          <bt:Url id="residFunctionFile" DefaultValue="{b}/outlook/commands.html{q}"/>
          <bt:Url id="residTaskpane" DefaultValue="{b}/outlook/taskpane.html{q}"/>{urls}
        </bt:Urls>
        <bt:ShortStrings>
          <bt:String id="residDescription" DefaultValue="Peregovorka"/>
          <bt:String id="residLabel" DefaultValue="Добавить конференцию"/>
        </bt:ShortStrings>
        <bt:LongStrings>
          <bt:String id="residTooltip" DefaultValue="Открыть панель Peregovorka: создать видеоконференцию и добавить ссылку в приглашение."/>
        </bt:LongStrings>
      </Resources>
    </VersionOverrides>
  </VersionOverrides>
</OfficeApp>
"""
