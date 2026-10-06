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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    acl: Mapped[list["RoomAcl"]] = relationship(back_populates="room", cascade="all, delete-orphan", lazy="selectin")


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
    participant_identity: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[dict | None] = mapped_column(JSONType)  # провайдер/модель/устройство
    metrics: Mapped[dict | None] = mapped_column(JSONType)  # длительности, очередь
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    user: Mapped[User | None] = relationship(lazy="joined")


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
