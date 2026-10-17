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
from ..models import Room, RoomAcl, RoomModerator, utcnow
from .rooms import slug_taken
from .settings import GeneralSettings, OutlookSettings
from .temp_rooms import TempRoomError, close as close_room

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PREFIX = "ol-"
CODE_LEN = 20
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
TOKEN_PREFIX = "pgo_"
MAX_SINGLE = timedelta(hours=24)
MAX_AHEAD = timedelta(days=730)


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


# ------------------------------------------------------------------------------------------------ токены надстройки
def _tok_key(token: str) -> str:
    return "outlook:tok:" + hashlib.sha256(token.encode()).hexdigest()


async def issue_token(redis, su: SessionUser, ttl_hours: int) -> tuple[str, int]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    payload = {"uid": str(su.user_id), "ad": su.ad_guid, "sam": su.sam_account_name, "name": su.display_name, "admin": su.is_admin, "groups": sorted(su.groups), "local": su.local}
    await redis.set(_tok_key(token), json.dumps(payload), ex=ttl_hours * 3600)
    return token, ttl_hours * 3600


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
                 default_text_days: int | None = None, default_audio_days: int | None = None) -> tuple[Room, bool]:
    """Создаёт запланированную комнату или возвращает уже связанную с этим событием (идемпотентно). Отменённую или закрытую по ошибке комнату,
    у которой встреча ещё не закончилась, повторное нажатие кнопки возвращает в работу."""
    if not cfg.enabled:
        raise TempRoomError("disabled", "Интеграция с Outlook отключена администратором.", 403)
    if not allowed(cfg, su):
        raise TempRoomError("forbidden", "Создавать конференции из Outlook вам не разрешено.", 403)
    if not KEY_RE.match(key or ""):
        raise TempRoomError("bad_key", "Некорректный ключ события.", 422)
    now = utcnow()
    start, end = validate_times(start, end, recurring, now)
    subject = " ".join((subject or "").split())[:200] or "Встреча"
    room = await find_by_key(db, su.user_id, key)
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
                    auto_close_at=_auto_close(end, recurring, cfg, now), camera_allowed=True, screen_share_allowed=True)
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


def invite_texts(room: Room, base: str, cfg: OutlookSettings, *, guest: bool) -> tuple[str, str]:
    """Блок для письма-приглашения: HTML (в тело встречи) и обычный текст (для запасных клиентов)."""
    url = join_link(base, room)
    early = cfg.early_minutes
    lines = [f"Подключиться к конференции: {url}",
             f"Вход открывается за {early} мин до начала. Перед входом проверьте камеру и микрофон." if not room.recurring else "Вход открыт в любое время; встреча начинается в назначенное время.",
             "Нужна учётная запись организации." + (" Гостям без учётной записи вход доступен по той же ссылке." if guest else "")]
    html = ('<br><p><b>Видеоконференция Peregovorka</b></p>'
            f'<p><a href="{escape(url, {chr(34): "&quot;"})}" target="_blank">Подключиться к конференции</a></p>'
            f'<p>{escape(url)}</p><p>{escape(lines[1])}</p><p>{escape(lines[2])}</p><br>')
    return html, "\n".join(lines)


def public_view(room: Room, st: str, *, now: datetime, cfg: OutlookSettings) -> dict:
    """Сведения для страницы обратного отсчёта (без входа в систему): название, время, состояние; гостевой токен — только когда вход уже открыт."""
    guest_ok = bool(room.guest_access_enabled and room.guest_token and cfg.guest_links)
    return {"name": room.name, "state": st, "start": room.scheduled_start, "end": room.scheduled_end, "opens_at": room.join_opens_at, "recurring": room.recurring,
            "early_minutes": cfg.early_minutes, "server_time": aware(now), "slug": room.slug, "guests": guest_ok,
            "guest_token": room.guest_token if guest_ok and st in ("open", "live") else None}


# ------------------------------------------------------------------------------------------------ манифест
def manifest_xml(base: str, version: str) -> str:
    """Манифест надстройки (add-in only manifest, поставщик онлайн-встреч). Идентификатор стабилен для адреса, поэтому обновление манифеста заменяет надстройку."""
    gid = str(uuid.uuid5(uuid.NAMESPACE_URL, "peregovorka-outlook:" + base))
    n = [int(x) for x in re.findall(r"\d+", version)[:3]] + [0, 0, 0]
    v = f"{n[0] + 1}.{n[1]}.{n[2]}.0"                 # Office требует версию манифеста не ниже 1.0: старшая часть увеличена на единицу (растёт вместе с версией приложения)
    b = escape(base)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0" xmlns:mailappor="http://schemas.microsoft.com/office/mailappversionoverrides/1.0" xsi:type="MailApp">
  <Id>{gid}</Id>
  <Version>{v}</Version>
  <ProviderName>Peregovorka</ProviderName>
  <DefaultLocale>ru-RU</DefaultLocale>
  <DisplayName DefaultValue="Peregovorka"/>
  <Description DefaultValue="Видеоконференция Peregovorka в приглашении Outlook"/>
  <IconUrl DefaultValue="{b}/outlook/icon-64.png"/>
  <HighResolutionIconUrl DefaultValue="{b}/outlook/icon-80.png"/>
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
        <SourceLocation DefaultValue="{b}/outlook/commands.html"/>
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
          <bt:Set Name="Mailbox" MinVersion="1.3"/>
        </bt:Sets>
      </Requirements>
      <Hosts>
        <Host xsi:type="MailHost">
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
                      <bt:Image size="32" resid="icon-32"/>
                      <bt:Image size="64" resid="icon-64"/>
                      <bt:Image size="80" resid="icon-80"/>
                    </Icon>
                    <Action xsi:type="ExecuteFunction">
                      <FunctionName>insertPeregovorkaMeeting</FunctionName>
                    </Action>
                  </Control>
                </Group>
              </OfficeTab>
            </ExtensionPoint>
          </DesktopFormFactor>
        </Host>
      </Hosts>
      <Resources>
        <bt:Images>
          <bt:Image id="icon-16" DefaultValue="{b}/outlook/icon-16.png"/>
          <bt:Image id="icon-32" DefaultValue="{b}/outlook/icon-32.png"/>
          <bt:Image id="icon-64" DefaultValue="{b}/outlook/icon-64.png"/>
          <bt:Image id="icon-80" DefaultValue="{b}/outlook/icon-80.png"/>
        </bt:Images>
        <bt:Urls>
          <bt:Url id="residFunctionFile" DefaultValue="{b}/outlook/commands.html"/>
        </bt:Urls>
        <bt:ShortStrings>
          <bt:String id="residDescription" DefaultValue="Peregovorka"/>
          <bt:String id="residLabel" DefaultValue="Добавить конференцию"/>
        </bt:ShortStrings>
        <bt:LongStrings>
          <bt:String id="residTooltip" DefaultValue="Добавить в приглашение видеоконференцию Peregovorka и ссылку для участников."/>
        </bt:LongStrings>
      </Resources>
    </VersionOverrides>
  </VersionOverrides>
</OfficeApp>
"""
