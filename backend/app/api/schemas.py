from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ROOM_PARTICIPANTS = 2000   # верхняя граница настройки комнаты; это не гарантия 2000 одновременных видеоподключений (нагрузку определяют ресурсы сервера и LiveKit)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=512, repr=False)

    # Пароль не фильтруется по символам: допустим любой набор (в LDAP он передаётся как значение bind, а не как часть фильтра).


class UserOut(BaseModel):
    id: uuid.UUID
    sam_account_name: str
    display_name: str
    is_admin: bool


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str
    local: bool = False                 # локальный (аварийный) администратор
    must_change_password: bool = False  # первичный/сброшенный пароль нужно сменить до работы
    must_enroll_totp: bool = False      # политика требует двухфакторную защиту: сначала её нужно настроить


class ActiveMeetingOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    participants: int


class RoomIconOut(BaseModel):
    kind: str                       # library | image (стандартного значка в ответе нет: icon = null)
    key: str | None = None          # ключ значка библиотеки
    url: str | None = None          # адрес загруженного изображения (с версией: кэш обновляется при замене)


class RoomOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    max_participants: int
    has_password: bool
    transcription_enabled: bool
    record_audio: bool
    camera_allowed: bool
    screen_share_allowed: bool
    board_allowed: bool = True
    board_access: str = "auto"
    room_type: str = "regular"
    auto_record: bool = False
    encrypt_storage: bool = False      # чат и стенограммы этой комнаты шифруются при хранении
    e2ee_mode: str = "off"             # сквозное шифрование звука и видео: off | full | trusted
    lifetime: str = "permanent"        # permanent | temporary
    lifecycle: str = "active"          # active | grace_period | closed
    auto_close_at: datetime | None = None
    created_by_name: str | None = None
    can_manage: bool = False                  # вошедший — руководитель комнаты или администратор (только список комнат)
    mute_on_join: bool = False                # участники входят с выключенным микрофоном (экран входа показывает это руководителю презентации)
    guest_token: str | None = None            # секрет гостевой ссылки: отдаётся ТОЛЬКО руководителям и администраторам, и только если гостевой вход включён
    active_meeting: ActiveMeetingOut | None = None
    icon: RoomIconOut | None = None


class JoinIn(BaseModel):
    password: str | None = Field(default=None, max_length=256, repr=False)


class ClientConfig(BaseModel):
    screen_profile: str = "sharp"
    screen_eco_kbps: int = 800      # цель битрейта профиля «Экономный 720p» (500–900 кбит/с)
    screen_share_audio: bool = False
    one_sharer_at_a_time: bool = False
    can_moderate: bool = False      # руководитель комнаты / администратор: может выключать микрофоны участников
    is_guest: bool = False          # гость: без административных функций, без демонстрации экрана
    mute_on_join: bool = False      # участники входят с выключенным микрофоном
    welcome_message: str | None = None
    can_manage: bool = False        # руководитель комнаты или администратор: «Настройки комнаты», участники встречи, слово, запись
    can_control: bool = False       # может переключать запись и транскрибацию (руководитель; в комнате без руководителей — любой участник)
    presentation: bool = False      # презентационная комната: участники — слушатели, пока им не «дали слово»
    sources: list[str] = Field(default_factory=lambda: ["microphone", "camera", "screen_share", "screen_share_audio"])  # что можно публиковать сейчас
    floor: bool = False             # вам дано слово
    can_edit_board: bool = True     # можно ли править общую доску
    can_view_board: bool = True     # видна ли доска (при уровне «только руководители» остальные её не видят)
    board_access: str = "everyone"  # действующий уровень: everyone | speakers | leaders | private
    recording_allowed: bool = False  # комната допускает запись аудио (кнопка «Начать запись»)
    attachments: bool = True        # вложения в чат включены


class E2EEOut(BaseModel):
    key: str = Field(repr=False)
    mode: str


class JoinOut(BaseModel):
    meeting_id: uuid.UUID
    room: RoomOut
    livekit_url: str
    livekit_room: str
    token: str = Field(repr=False)
    identity: str
    recording: bool = False  # идёт запись аудио
    transcription: bool = True  # идёт транскрибация
    asr_ready: bool = False  # транскрибация готова; вход в комнату от этого НЕ зависит
    client: ClientConfig = Field(default_factory=ClientConfig)
    e2ee: E2EEOut | None = None          # ключ сквозного шифрования этой встречи (если у комнаты включено E2EE)


class ParticipantOut(BaseModel):
    user_id: uuid.UUID | None = None
    guest_id: uuid.UUID | None = None
    participant_type: str = "user"   # user | guest
    role: str | None = None          # organizer — начал встречу; leader — руководитель комнаты (поимённо); иначе None
    display_name: str
    joined_at: datetime
    left_at: datetime | None
    online: bool


class MeetingOut(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    room_name: str
    room_icon: RoomIconOut | None = None
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    transcription_enabled: bool
    participants: list[ParticipantOut]
    segments: int = 0
    recordings: int = 0
    protocols: int = 0
    chat_messages: int = 0
    whiteboard_shapes: int = 0       # 0 — доска не использовалась
    guests: int = 0
    can_generate: bool = False         # формировать протокол, резюме и карту: администратор, руководитель комнаты, организатор встречи
    can_manage_access: bool = False    # руководитель комнаты / администратор: «Управление доступом», журнал комнаты
    can_send_materials: bool = False   # руководитель комнаты / администратор: «Отправить материалы» по почте


class SegmentOut(BaseModel):
    id: int
    uid: uuid.UUID
    meeting_id: uuid.UUID
    user_id: uuid.UUID | None
    guest_id: uuid.UUID | None = None
    display_name: str
    identity: str
    started_at: datetime
    ended_at: datetime
    text: str
    language: str | None


class TranscriptOut(BaseModel):
    meeting_id: uuid.UUID
    segments: list[SegmentOut]
    has_more: bool


# ---- админка
class AclEntryIn(BaseModel):
    subject_type: str = Field(pattern="^(group|user)$")
    subject_ref: str = Field(min_length=1, max_length=512)
    display_name: str | None = Field(default=None, max_length=300)


class AclEntryOut(AclEntryIn):
    model_config = ConfigDict(from_attributes=True)


class RoomAdminOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    icon: RoomIconOut | None = None
    is_enabled: bool
    max_participants: int
    has_password: bool
    transcription_enabled: bool
    record_audio: bool
    camera_allowed: bool
    screen_share_allowed: bool
    text_retention_days: int | None
    audio_retention_days: int | None
    protocol_instructions: str | None
    history_access: str = "admin"
    anonymize_mode: str = "inherit"
    llm_profile_id: uuid.UUID | None = None
    anonymizer_profile_id: uuid.UUID | None = None
    mute_on_join: bool = False
    welcome_message: str | None = None
    room_type: str = "regular"
    auto_record: bool = False
    board_allowed: bool = True
    board_access: str = "auto"
    encrypt_storage: bool = False
    e2ee_mode: str = "off"
    guest_access_enabled: bool = False
    slug_history: list[str] = Field(default_factory=list)      # прежние адреса комнаты (старые ссылки перенаправляют на нынешний)
    lifetime: str = "permanent"
    lifecycle: str = "active"
    closed_at: datetime | None = None
    created_by_name: str | None = None
    guest_token: str | None = None     # секрет гостевой ссылки (виден только администраторам); None — ссылка не выпущена/отозвана
    acl: list[AclEntryOut]
    moderators: list[AclEntryOut] = Field(default_factory=list)
    active_meeting_id: uuid.UUID | None = None


class RoomCreateIn(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_enabled: bool = True
    max_participants: int = Field(default=20, ge=1, le=MAX_ROOM_PARTICIPANTS)
    password: str | None = Field(default=None, max_length=256, repr=False)
    transcription_enabled: bool = True
    record_audio: bool = False
    camera_allowed: bool = True
    screen_share_allowed: bool = True
    text_retention_days: int | None = Field(default=None, ge=0, le=36500)
    audio_retention_days: int | None = Field(default=None, ge=0, le=36500)
    protocol_instructions: str | None = Field(default=None, max_length=20000)
    history_access: str = Field(default="admin", pattern="^(admin|participants)$")
    anonymize_mode: str = Field(default="inherit", pattern="^(inherit|on|off)$")
    llm_profile_id: uuid.UUID | None = None
    anonymizer_profile_id: uuid.UUID | None = None
    mute_on_join: bool = False
    welcome_message: str | None = Field(default=None, max_length=2000)
    room_type: str = Field(default="regular", pattern="^(regular|presentation)$")
    auto_record: bool = False
    board_allowed: bool = True
    board_access: str = Field(default="auto", pattern="^(auto|everyone|speakers|leaders|private)$")
    encrypt_storage: bool = False
    e2ee_mode: str = Field(default="off", pattern="^(off|full|trusted)$")
    guest_access_enabled: bool = False
    acl: list[AclEntryIn] = Field(default_factory=list)
    moderators: list[AclEntryIn] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().lower()           # регистр не важен: адрес хранится строчным
        if not SLUG_RE.match(v):
            raise ValueError("Адрес комнаты: латиница, цифры, «-» и «_», 2–63 символа, начинается с буквы или цифры")
        return v


class RoomPatchIn(BaseModel):
    """Частичное обновление. Пароль: строка — задать, пустая строка — снять, поле не передано — не менять."""

    slug: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    is_enabled: bool | None = None
    max_participants: int | None = Field(default=None, ge=1, le=MAX_ROOM_PARTICIPANTS)
    password: str | None = Field(default=None, max_length=256, repr=False)
    transcription_enabled: bool | None = None
    record_audio: bool | None = None
    camera_allowed: bool | None = None
    screen_share_allowed: bool | None = None
    text_retention_days: int | None = Field(default=None, ge=0, le=36500)
    audio_retention_days: int | None = Field(default=None, ge=0, le=36500)
    protocol_instructions: str | None = Field(default=None, max_length=20000)
    history_access: str | None = Field(default=None, pattern="^(admin|participants)$")
    anonymize_mode: str | None = Field(default=None, pattern="^(inherit|on|off)$")
    llm_profile_id: uuid.UUID | None = None
    anonymizer_profile_id: uuid.UUID | None = None
    mute_on_join: bool | None = None
    welcome_message: str | None = Field(default=None, max_length=2000)
    room_type: str | None = Field(default=None, pattern="^(regular|presentation)$")
    auto_record: bool | None = None
    board_allowed: bool | None = None
    board_access: str | None = Field(default=None, pattern="^(auto|everyone|speakers|leaders|private)$")
    encrypt_storage: bool | None = None
    e2ee_mode: str | None = Field(default=None, pattern="^(off|full|trusted)$")
    guest_access_enabled: bool | None = None
    acl: list[AclEntryIn] | None = None
    moderators: list[AclEntryIn] | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError("Адрес комнаты: латиница, цифры, «-» и «_», 2–63 символа, начинается с буквы или цифры")
        return v
