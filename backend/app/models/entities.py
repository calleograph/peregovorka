"""ORM-модели. Схема БД меняется ТОЛЬКО через Alembic (backend/migrations)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, BigIntPK, JSONType, UTCDateTime, utcnow


class User(Base):
    """Локальное представление доменного пользователя. Пароли не хранятся."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ad_guid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # objectGUID, стабильный
    sam_account_name: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    upn: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # ad — доменный пользователь (пароль не хранится); local — локальный (аварийный) администратор, независимый от LDAP
    auth_source: Mapped[str] = mapped_column(String(8), default="ad", server_default="ad", nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)            # только для local (argon2id)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Профиль из каталога (только разрешённые атрибуты) и собственная аватарка. Обновляется при каждом входе и кнопкой «Обновить данные из AD».
    title: Mapped[str | None] = mapped_column(String(300))            # должность
    department: Mapped[str | None] = mapped_column(String(300))       # подразделение
    phone: Mapped[str | None] = mapped_column(String(64))
    profile_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    avatar_mime: Mapped[str | None] = mapped_column(String(20))       # есть аватарка — файл в DATA_DIR/avatars (см. services/avatars.py)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Откуда аватарка: manual — загрузил сам пользователь; bitrix — скачана с портала (обновляется по сроку); пусто с файлом — прежняя ручная.
    avatar_source: Mapped[str | None] = mapped_column(String(10))
    # Значения профиля по источникам {"ad": {...}, "bitrix": {..., "fetched_at": iso}} и внешние идентификаторы {"bitrix": "123"} — для слияния по приоритетам
    profile_sources: Mapped[dict | None] = mapped_column(JSONType)
    external_ids: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    @property
    def livekit_identity(self) -> str:
        return f"u-{self.id.hex}"


class Room(Base):
    """Постоянная переговорка (настройки). Конкретные сеансы — Meeting."""

    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # технический идентификатор
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_participants: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    transcription_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    record_audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    camera_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    screen_share_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    text_retention_days: Mapped[int | None] = mapped_column(Integer)   # None = бессрочно
    audio_retention_days: Mapped[int | None] = mapped_column(Integer)  # None = бессрочно
    protocol_instructions: Mapped[str | None] = mapped_column(Text)
    # Кто видит завершённую встречу после выхода: 'admin' — только администраторы (и участники, пока они на странице встречи);
    # 'participants' — участники встречи; явные разрешения (meeting_grants) действуют всегда.
    history_access: Mapped[str] = mapped_column(String(20), default="admin", server_default="admin", nullable=False)
    # Обезличивание текста перед LLM для этой комнаты: inherit — как в общих настройках, on — всегда, off — выключено (текст идёт в LLM как есть).
    anonymize_mode: Mapped[str] = mapped_column(String(10), default="inherit", server_default="inherit", nullable=False)
    # Какой API использовать: пусто — общий по умолчанию. Ссылка мягкая (без FK): удалённый профиль = «по умолчанию».
    llm_profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    anonymizer_profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Участники входят с выключенным микрофоном (удобно для больших встреч); текст приветствия показывается при входе.
    mute_on_join: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    # Гостевой доступ по ссылке (без AD): включается отдельно; ссылку можно отозвать (перевыпустить токен) без удаления комнаты.
    guest_access_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    guest_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Тип комнаты: regular — обычная (все публикуют звук/видео по правам комнаты); presentation — участники входят слушателями, публиковать
    # (микрофон, камера, экран) и править доску могут руководители и те, кому руководитель «дал слово» на время встречи.
    room_type: Mapped[str] = mapped_column(String(16), default="regular", server_default="regular", nullable=False)
    # Начинать запись аудио автоматически при старте встречи (иначе — вручную). Транскрибация идёт всегда (её можно остановить во встрече).
    auto_record: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    # Могут ли обычные участники (не руководители) править общую доску; руководители — всегда.
    board_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    # Кто работает с общей доской: auto — по типу комнаты (обычная: все, если board_allowed; презентация: только руководитель); everyone — все правят;
    # speakers — руководители и те, кому дали слово; leaders — правят только руководители, остальные смотрят; private — доска только у руководителей
    # (остальные её не видят и не получают её изменений).
    board_access: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto", nullable=False)
    # доставка материалов встречи по почте (настраивает руководитель): {enabled, materials[], recipients{leaders,participants,users[],emails[]}}
    mail_delivery: Mapped[dict | None] = mapped_column(JSONType)
    # Языковая модель комнаты: inherit — системная по умолчанию; profile — внешний профиль (llm_profile_id); local — локальная модель (llm_local_model); off — отключена.
    # Для комнат старых версий: llm_profile_id задан → profile. Выбранный профиль удалён/модель недоступна → политика llm.on_missing (системная или «недоступна»).
    llm_mode: Mapped[str] = mapped_column(String(10), default="inherit", server_default="inherit", nullable=False)
    llm_local_model: Mapped[str | None] = mapped_column(String(80))
    # То же для КРАТКОГО РЕЗЮМЕ (протокол и резюме — разные задачи, модель выбирается отдельно): inherit — системная модель для резюме.
    llm_summary_mode: Mapped[str] = mapped_column(String(10), default="inherit", server_default="inherit", nullable=False)
    llm_summary_profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    llm_summary_local_model: Mapped[str | None] = mapped_column(String(80))
    # Телефония (SIP через LiveKit SIP): off — отключена; default — профиль по умолчанию; profile — конкретный (sip_profile_id).
    sip_mode: Mapped[str] = mapped_column(String(10), default="off", server_default="off", nullable=False)
    sip_profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    sip_extension: Mapped[str | None] = mapped_column(String(32), unique=True)      # внутренний номер комнаты для входящих звонков
    sip_allow_inbound: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    sip_allow_outbound: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    sip_dispatch_rule_id: Mapped[str | None] = mapped_column(String(100))          # правило LiveKit для входящих (постоянное, переиспользуется)
    sip_contacts: Mapped[list | None] = mapped_column(JSONType)                     # сохранённые номера для исходящих: [{name, number}]
    welcome_message: Mapped[str | None] = mapped_column(Text)
    # Автоматически формировать «Карту разговора» после встречи: inherit — как в системных настройках («Протоколы»), on / off — явно для комнаты.
    auto_map_mode: Mapped[str] = mapped_column(String(10), default="inherit", server_default="inherit", nullable=False)
    # Прежние технические идентификаторы (адреса) комнаты: по ним старая ссылка перенаправляет на нынешнюю. Список строк.
    slug_history: Mapped[list | None] = mapped_column(JSONType)
    # Срок жизни: permanent — обычная переговорка; temporary — временная, создаётся пользователем на одну встречу и по её окончании закрывается.
    # (Не путать с room_type: обычная / презентационная.) Материалы закрытой временной комнаты остаются в истории: строка не удаляется.
    lifetime: Mapped[str] = mapped_column(String(12), default="permanent", server_default="permanent", nullable=False)
    # Состояние временной комнаты: active → grace_period (все вышли, ждём возврата) → closed. У постоянной всегда active.
    lifecycle: Mapped[str] = mapped_column(String(12), default="active", server_default="active", nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    auto_close_at: Mapped[datetime | None] = mapped_column(UTCDateTime)      # когда комната закроется сама, если никто не вернётся
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by_name: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    acl: Mapped[list["RoomAcl"]] = relationship(back_populates="room", cascade="all, delete-orphan", lazy="selectin")
    moderators: Mapped[list["RoomModerator"]] = relationship(back_populates="room", cascade="all, delete-orphan", lazy="selectin")


class RoomAcl(Base):
    """Кому разрешён вход: AD-группа (DN) или конкретный пользователь (objectGUID)."""

    __tablename__ = "room_acl"
    __table_args__ = (UniqueConstraint("room_id", "subject_type", "subject_ref", name="uq_room_acl_subject"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 'group' | 'user'
    subject_ref: Mapped[str] = mapped_column(String(512), nullable=False)  # group DN (lower) | ad_guid
    display_name: Mapped[str | None] = mapped_column(String(300))

    room: Mapped[Room] = relationship(back_populates="acl")


class RoomModerator(Base):
    """Руководитель (модератор) комнаты: AD-группа или пользователь. Может выключать микрофоны участников; вход в комнату ему разрешён всегда."""

    __tablename__ = "room_moderators"
    __table_args__ = (UniqueConstraint("room_id", "subject_type", "subject_ref", name="uq_room_moderator_subject"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 'group' | 'user'
    subject_ref: Mapped[str] = mapped_column(String(512), nullable=False)  # group DN (lower) | ad_guid (lower)
    display_name: Mapped[str | None] = mapped_column(String(300))

    room: Mapped[Room] = relationship(back_populates="moderators")


class Meeting(Base):
    """Конкретный сеанс встречи в комнате."""

    __tablename__ = "meetings"
    __table_args__ = (
        # не более одной активной встречи на комнату
        Index("uq_meetings_one_active_per_room", "room_id", unique=True,
              postgresql_where=text("ended_at IS NULL"), sqlite_where=text("ended_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    livekit_room: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)  # m-<uuid hex>
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    end_reason: Mapped[str | None] = mapped_column(String(40))
    started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    empty_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Настройки ЭТОЙ встречи поверх настроек комнаты (задаёт руководитель): рассылка материалов и языковая модель; None — как в комнате
    delivery_override: Mapped[dict | None] = mapped_column(JSONType)
    llm_override: Mapped[dict | None] = mapped_column(JSONType)
    llm_summary_override: Mapped[dict | None] = mapped_column(JSONType)
    transcription_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    record_audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    room: Mapped[Room] = relationship(lazy="joined")
    participants: Mapped[list["MeetingParticipant"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan", lazy="selectin"
    )


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    __table_args__ = (Index("ix_meeting_participants_meeting_user", "meeting_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)  # токен выдан
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # подтверждено LiveKit
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Снимок данных сотрудника на момент встречи (ФИО, должность, подразделение, e-mail, телефон): не меняется после сохранения, протоколы показывают данные того времени
    snapshot: Mapped[dict | None] = mapped_column(JSONType)

    meeting: Mapped[Meeting] = relationship(back_populates="participants")
    user: Mapped[User] = relationship(lazy="joined")


class TranscriptSegment(Base):
    """Реплика одного участника. Каждый микрофон — отдельный трек → независимые записи."""

    __tablename__ = "transcript_segments"
    __table_args__ = (Index("ix_segments_meeting_started", "meeting_id", "started_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    segment_uid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False)  # идемпотентность приёма
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    guest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("guest_participants.id", ondelete="SET NULL"), index=True)
    participant_identity: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[dict | None] = mapped_column(JSONType)  # провайдер/модель/устройство
    metrics: Mapped[dict | None] = mapped_column(JSONType)  # длительности, очередь
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    user: Mapped[User | None] = relationship(lazy="joined")
    guest: Mapped["GuestParticipant | None"] = relationship(lazy="joined")


class GuestParticipant(Base):
    """Гость встречи (вход по ссылке без AD). Это отдельный тип участника, а НЕ фиктивный пользователь AD: у него нет прав и учётной записи."""

    __tablename__ = "guest_participants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True, nullable=False)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    participant_type: Mapped[str] = mapped_column(String(10), default="guest", server_default="guest", nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    client: Mapped[str | None] = mapped_column(String(160))
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Идентификатор участника в LiveKit, если он выдан не нами (входящий телефонный звонок: sip_<номер>…); иначе выводится из id
    lk_identity: Mapped[str | None] = mapped_column(String(160), index=True)

    @property
    def is_phone(self) -> bool:
        return self.participant_type == "phone"

    @property
    def label(self) -> str:
        """Как показывать участника: гость — «Имя (гость)», телефонный абонент — «Телефон: +7…»."""
        return self.display_name if self.is_phone else f"{self.display_name} (гость)"

    @property
    def livekit_identity(self) -> str:
        if self.lk_identity:
            return self.lk_identity
        return f"{'p' if self.is_phone else 'g'}-{self.id.hex}"


class MeetingChatMessage(Base):
    """Сообщение чата встречи. Привязано к конкретной встрече (в одной комнате встреч много), хранится вместе с материалами встречи."""

    __tablename__ = "meeting_chat_messages"
    __table_args__ = (Index("ix_chat_meeting_id", "meeting_id", "id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    author_type: Mapped[str] = mapped_column(String(10), default="user", nullable=False)  # user | guest | system
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    guest_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("guest_participants.id", ondelete="SET NULL"))
    author_name: Mapped[str] = mapped_column(String(300), nullable=False)  # имя на момент отправки
    text: Mapped[str] = mapped_column(Text, nullable=False)


class ChatAttachment(Base):
    """Вложение чата: в базе — только метаданные, сам файл лежит в файловом хранилище (профиль хранилища или локальный диск приложения).
    До отправки сообщения (`message_id` пуст) вложение «ожидает» и удаляется, если сообщение так и не отправлено."""

    __tablename__ = "meeting_chat_attachments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True, nullable=False)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("meeting_chat_messages.id", ondelete="CASCADE"), index=True)
    uploader_type: Mapped[str] = mapped_column(String(10), nullable=False)       # user | guest
    uploader_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)               # очищенное имя для показа и скачивания
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)                 # image | file
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)        # путь внутри хранилища (формирует сервер, не клиент)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)                   # профиль хранилища на момент загрузки (None — локальный диск)
    file_state: Mapped[str] = mapped_column(String(12), default="ok", server_default="ok", nullable=False)   # ok | missing
    file_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)


class StorageProfile(Base):
    """Профиль файлового хранилища: создаётся администратором один раз (локальный каталог или SMB-ресурс с учётной записью), а функции
    (записи, протоколы, стенограммы, вложения чата, доски) лишь выбирают профиль. Пароль хранится зашифрованным."""

    __tablename__ = "storage_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)                 # local | smb
    config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)  # local_path | smb_server, smb_share, smb_base_path, smb_username, smb_domain
    secret_enc: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class MeetingWhiteboard(Base):
    """Общая доска встречи: схема в формате draw.io (XML) — её можно открыть и продолжить редактировать; одна на встречу."""

    __tablename__ = "meeting_whiteboards"

    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True)
    xml: Mapped[str] = mapped_column(Text, default="", nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)       # номер последнего учтённого в снимке патча
    shapes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)    # блоков и связей (для списка встреч и протокола)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(300))


class AuditLog(Base):
    """Аудит административных действий (отдельно от технического лога)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str] = mapped_column(String(300), nullable=False)
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONType)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))


class AppSetting(Base):
    """Глобальные настройки; секретные значения хранятся зашифрованными (SecretBox)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(300))


class Recording(Base):
    """Файл записи аудио одного участника (хранение отдельно от текста, свой срок)."""

    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True, nullable=False)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    participant_identity: Mapped[str] = mapped_column(String(80), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)  # относительно каталога записей
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    # local — только локальный том; pending/failed — выгрузка во внешнее хранилище не удалась (будет повтор); exported — выгружена
    export_status: Mapped[str] = mapped_column(String(20), default="local", server_default="local", nullable=False)
    export_location: Mapped[str | None] = mapped_column(String(1000))
    export_error: Mapped[str | None] = mapped_column(String(500))
    exported_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # ok — файл есть (по последней сверке); missing — удалён из хранилища/с диска вне приложения: ссылка не отдаётся
    file_state: Mapped[str] = mapped_column(String(12), default="ok", server_default="ok", nullable=False)
    file_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)


class Protocol(Base):
    """Протокол встречи: 'transcript' — полный текст по времени; 'summary' — краткий протокол от LLM."""

    __tablename__ = "protocols"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # transcript | summary
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending | ready | failed
    content: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    meta: Mapped[dict | None] = mapped_column(JSONType)  # модель, метрики обезличивания, путь экспорта
    created_by: Mapped[str | None] = mapped_column(String(300))
    instruction: Mapped[str | None] = mapped_column(Text)  # инструкция, с которой сформирован текст
    title: Mapped[str | None] = mapped_column(String(300))
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    edited_by: Mapped[str | None] = mapped_column(String(300))
    # состояние ВЫГРУЖЕННОГО файла (сам текст — в базе): ok | missing — файл удалён из хранилища
    file_state: Mapped[str] = mapped_column(String(12), default="ok", server_default="ok", nullable=False)
    file_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ConversationMap(Base):
    """Карта разговора одной встречи (тема → отрезки времени → участники → источники). Первичны ДАННЫЕ (`data`, проверенный JSON), а не HTML:
    страница и HTML-выгрузка строятся из них. Пользовательские правки (`edits`) хранятся отдельно и переживают пересоздание карты."""

    __tablename__ = "conversation_maps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)   # pending | running | ready | failed
    data: Mapped[dict | None] = mapped_column(JSONType)       # карта: темы, отрезки, участники, источники (см. services/conv_map.py)
    edits: Mapped[dict | None] = mapped_column(JSONType)      # правки пользователя: {topic_id: {title, category, note}} — отдельно от результата модели
    meta: Mapped[dict | None] = mapped_column(JSONType)       # модель, время этапов, чанки, повторы, токены, предупреждения
    error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class MailTemplate(Base):
    """Шаблон письма с материалами встречи (тема, текст, подпись, материалы по умолчанию); переменные {{meeting_title}} и др. подставляются при отправке."""

    __tablename__ = "mail_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str | None] = mapped_column(Text)
    materials: Mapped[list | None] = mapped_column(JSONType)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ProtocolTemplate(Base):
    """Сохранённая инструкция для протокола: общая (admin) или личная."""

    __tablename__ = "protocol_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="any", nullable=False)  # summary | protocol | any
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)  # global | user
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class MeetingGrant(Base):
    """Явное разрешение пользователю смотреть завершённую встречу (выдаёт администратор)."""

    __tablename__ = "meeting_grants"
    __table_args__ = (UniqueConstraint("meeting_id", "user_id", name="uq_meeting_grant"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    granted_by: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    user: Mapped[User] = relationship(lazy="joined")


class ApiProfile(Base):
    """Именованный API внешнего сервиса: языковой модели (kind=llm) или обезличивания (kind=anonymizer). Несколько на тип;
    один — по умолчанию, комната может выбрать свой. Секрет (токен/ключ) хранится зашифрованным."""

    __tablename__ = "api_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)  # поля настроек без секрета
    secret_enc: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("kind", "name", name="uq_api_profile_name"),)


class EventLog(Base):
    """Журнал событий для диагностики: входы, подключения, ошибки устройств, сеть, действия. Хранится N дней (по умолчанию 30),
    очищается автоматически; опционально дублируется во внешнее хранилище. Содержимое разговоров и секреты сюда не попадают."""

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(8), index=True, default="info", nullable=False)  # debug|info|warn|error
    category: Mapped[str] = mapped_column(String(24), index=True, nullable=False)  # auth|room|client|device|network|admin|llm|storage|asr|system
    event: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(300), index=True)
    room: Mapped[str | None] = mapped_column(String(200), index=True)
    meeting_id: Mapped[str | None] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(String(64))
    client: Mapped[str | None] = mapped_column(String(160))   # «Chrome 130 · Windows 11» — браузер и ОС
    message: Mapped[str | None] = mapped_column(String(600))
    data: Mapped[dict | None] = mapped_column(JSONType)
    request_id: Mapped[str | None] = mapped_column(String(64))


class LdapProfile(Base):
    """Подключение к каталогу (LDAPS), настраиваемое из веб-интерфейса. Пароль сервисной учётной записи — зашифрован."""

    __tablename__ = "ldap_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=636, nullable=False)
    protocol: Mapped[str] = mapped_column(String(10), default="ldaps", server_default="ldaps", nullable=False)   # ldaps | starttls
    base_dn: Mapped[str] = mapped_column(String(500), nullable=False)
    upn_suffix: Mapped[str] = mapped_column(String(253), default="", server_default="", nullable=False)
    netbios_domain: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    bind_dn: Mapped[str] = mapped_column(String(500), nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    login_attribute: Mapped[str] = mapped_column(String(64), default="sAMAccountName", server_default="sAMAccountName", nullable=False)
    display_name_attribute: Mapped[str] = mapped_column(String(64), default="displayName", server_default="displayName", nullable=False)
    email_attribute: Mapped[str] = mapped_column(String(64), default="mail", server_default="mail", nullable=False)
    use_for_users: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    use_for_admins: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class CaCertificate(Base):
    """Корневой/промежуточный сертификат удостоверяющего центра для проверки LDAPS (и, по желанию, SMTP). Закрытых ключей здесь нет."""

    __tablename__ = "ca_certificates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(1000), nullable=False)
    issuer: Mapped[str] = mapped_column(String(1000), nullable=False)
    serial: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    not_before: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    not_after: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    is_ca: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    self_signed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pem: Mapped[str] = mapped_column(Text, nullable=False)
    added_by: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class MailProfile(Base):
    """Профиль исходящей почты (SMTP). Пароль — зашифрован. Активен один профиль (`is_active`)."""

    __tablename__ = "mail_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    security: Mapped[str] = mapped_column(String(10), default="starttls", server_default="starttls", nullable=False)   # none | starttls | ssl
    auth_type: Mapped[str] = mapped_column(String(10), default="none", server_default="none", nullable=False)         # none | login
    username: Mapped[str] = mapped_column(String(320), default="", server_default="", nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str] = mapped_column(String(200), default="", server_default="", nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    verify_cert: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class MailMessage(Base):
    """Письмо в очереди. Тела писем и документы в таблице НЕ хранятся — собираются из данных встречи в момент отправки."""

    __tablename__ = "mail_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"), index=True)
    room_name: Mapped[str | None] = mapped_column(String(200))
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    kinds: Mapped[list | None] = mapped_column(JSONType)        # какие материалы: protocol | summary | transcript …
    options: Mapped[dict | None] = mapped_column(JSONType)      # {"archive": true} — одним архивом (zip)
    trigger: Mapped[str] = mapped_column(String(10), default="auto", nullable=False)   # auto | manual | test
    requested_by: Mapped[str | None] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(10), default="queued", index=True, nullable=False)   # queued | sending | sent | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(600))
    delivery: Mapped[str | None] = mapped_column(String(10))     # attachment | link — как доставлены материалы
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    sending_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class StorageSyncRun(Base):
    """Запуск сверки метаданных базы с реальным содержимым хранилищ (отчёт для администратора)."""

    __tablename__ = "storage_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trigger: Mapped[str] = mapped_column(String(10), nullable=False)    # manual | auto
    actor: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(12), default="running", nullable=False)   # running | ok | partial | unavailable | failed
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing: Mapped[int] = mapped_column(Integer, default=0, nullable=False)       # впервые обнаружены отсутствующими
    restored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)      # снова на месте
    orphans: Mapped[int] = mapped_column(Integer, default=0, nullable=False)       # неизвестные файлы (не импортируются)
    unavailable: Mapped[int] = mapped_column(Integer, default=0, nullable=False)   # не удалось проверить (хранилище недоступно)
    details: Mapped[dict | None] = mapped_column(JSONType)


class SipProfile(Base):
    """Профиль SIP-телефонии (транк к АТС или провайдеру): общий для LiveKit SIP; Asterisk/PJSIP — первый проверенный вариант, но не единственный.
    Пароль зашифрован (AES-GCM, ключ APP_MASTER_KEY) и обратно не отдаётся. Идентификаторы транков LiveKit хранятся для обновления и удаления."""

    __tablename__ = "sip_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="both", server_default="both", nullable=False)   # outbound | inbound | both
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=5060, nullable=False)
    transport: Mapped[str] = mapped_column(String(8), default="udp", server_default="udp", nullable=False)       # udp | tcp | tls
    username: Mapped[str] = mapped_column(String(200), default="", server_default="", nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    realm: Mapped[str] = mapped_column(String(253), default="", server_default="", nullable=False)             # домен/realm для входящей авторизации
    caller_id: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)           # номер, с которого звоним
    allowed_numbers: Mapped[list | None] = mapped_column(JSONType)       # шаблоны допустимых номеров назначения (префиксы)
    inbound_numbers: Mapped[list | None] = mapped_column(JSONType)       # номера, принимаемые входящим транком (пусто — любые)
    allowed_addresses: Mapped[list | None] = mapped_column(JSONType)     # адреса/подсети АТС, с которых принимаются входящие (IP allowlist)
    codecs: Mapped[list | None] = mapped_column(JSONType)                # допустимые кодеки; пусто — по умолчанию LiveKit
    media_encryption: Mapped[str] = mapped_column(String(10), default="disable", server_default="disable", nullable=False)   # disable | allow | require
    ring_timeout_s: Mapped[int] = mapped_column(Integer, default=45, server_default="45", nullable=False)
    lk_outbound_trunk_id: Mapped[str | None] = mapped_column(String(100))
    lk_inbound_trunk_id: Mapped[str | None] = mapped_column(String(100))
    last_check: Mapped[dict | None] = mapped_column(JSONType)            # итог последней проверки настроек/тестового вызова (без секретов)
    last_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ApiClient(Base):
    """Сервисная учётная запись публичного API (интеграция): права (scopes), область комнат и сетевые ограничения. Ключи — отдельно (ApiKey)."""

    __tablename__ = "api_clients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    rooms: Mapped[list | None] = mapped_column(JSONType)         # None — все комнаты; иначе список id комнат (UUID строкой): переименование адреса комнаты доступ не меняет
    ip_allowlist: Mapped[list | None] = mapped_column(JSONType)  # None/пусто — без ограничения; иначе адреса и сети CIDR
    created_by: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    keys: Mapped[list["ApiKey"]] = relationship(back_populates="client", cascade="all, delete-orphan", lazy="selectin")


class ApiKey(Base):
    """Ключ публичного API. Секрет показывается один раз; в базе — только SHA-256 секрета и открытый идентификатор ключа."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_clients.id", ondelete="CASCADE"), index=True, nullable=False)
    key_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)      # открытая часть: pgk_<key_id>_<секрет>
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # None — бессрочно; при ротации у старого ключа — конец «окна совместимости»
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    client: Mapped[ApiClient] = relationship(back_populates="keys", lazy="joined")


class ApiRequestLog(Base):
    """Журнал обращений к публичному API (без заголовков и тел запросов). Пишется пачками в фоне, а не на каждый запрос."""

    __tablename__ = "api_request_log"
    __table_args__ = (Index("ix_api_request_log_client_at", "client_id", "at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    client_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    key_id: Mapped[str | None] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(200), nullable=False)    # шаблон маршрута, без идентификаторов
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(60))


class WebhookEndpoint(Base):
    """Подписка на события публичного API: адрес получателя, события, область комнат и секрет подписи (хранится зашифрованным)."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active", nullable=False)     # active | degraded | disabled
    events: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)       # пусто — все события
    rooms: Mapped[list | None] = mapped_column(JSONType)                                # None — все комнаты; иначе id комнат (UUID строкой)
    secret_enc: Mapped[str | None] = mapped_column(Text)
    previous_secret_enc: Mapped[str | None] = mapped_column(Text)                       # прежний секрет действует до previous_until (подписываем обоими)
    previous_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(300))
    disabled_reason: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class WebhookDelivery(Base):
    """Одна доставка события одному получателю: очередь с повторами (экспоненциальная задержка) и история попыток."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True, nullable=False)
    event_id: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)   # pending | delivered | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manual_retries: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(300))
    attempt_log: Mapped[list | None] = mapped_column(JSONType)       # последние попытки: [{at, status, ms, error}]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ApiJob(Base):
    """Долгая операция публичного API (формирование протокола, резюме, карты): queued → processing → completed | failed | cancelled."""

    __tablename__ = "api_jobs"
    __table_args__ = (Index("ix_api_jobs_client_created", "client_id", "created_at"), Index("ix_api_jobs_status_created", "status", "created_at"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)                  # protocol | summary | map
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="queued", nullable=False)
    params: Mapped[dict | None] = mapped_column(JSONType)
    result: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ApiIdempotency(Base):
    """Ключи идемпотентности (`Idempotency-Key`): повтор того же запроса возвращает прежний ответ, а не создаёт дубль."""

    __tablename__ = "api_idempotency"
    __table_args__ = (UniqueConstraint("client_id", "key", name="uq_api_idempotency_client_key"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_clients.id", ondelete="CASCADE"), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(12), default="in_progress", nullable=False)     # in_progress | done
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
