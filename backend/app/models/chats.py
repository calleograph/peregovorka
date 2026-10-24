"""Корпоративные чаты: личные и групповые переписки, участники с личными настройками (папки, закрепление, архив, прочитанность),
сообщения, вложения и папки пользователя. Схема — только через Alembic (миграция 0039)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigIntPK, JSONType, UTCDateTime, utcnow


class Chat(Base):
    """Переписка. Личная (`direct`) — ровно два участника, одна на пару (`direct_key` — пара идентификаторов по порядку); групповая — название,
    изображение, администраторы. `last_message_*` — для сортировки списка без подзапросов."""

    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)                         # direct | group
    title: Mapped[str | None] = mapped_column(String(200))
    direct_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    avatar_mime: Mapped[str | None] = mapped_column(String(20))
    avatar_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)


class ChatMember(Base):
    """Участник переписки и его ЛИЧНЫЕ настройки: прочитано до, история очищена до, скрыта из списка, без уведомлений, закреплена, в архиве, папка.
    Чужие настройки на участника не влияют."""

    __tablename__ = "chat_members"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_chat_members"), Index("ix_chat_members_user", "user_id", "chat_id"))

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(8), default="member", server_default="member", nullable=False)      # member | admin
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_read_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    cleared_before_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)     # «очистить историю» — только у себя
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)    # «удалить из списка»; новое сообщение вернёт
    muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    pinned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_folders.id", ondelete="SET NULL"))


class ChatMessage(Base):
    """Сообщение. `client_id` — идентификатор, который присылает клиент: повторная отправка после сетевого сбоя возвращает уже записанное
    сообщение (без дубля). Удаление автором — пометка `deleted_at` (текст стирается), чужие сообщения физически не удаляются."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_chat", "chat_id", "id"), UniqueConstraint("chat_id", "author_id", "client_id", name="uq_chat_messages_client"))

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author_name: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), default="text", server_default="text", nullable=False)          # text | system
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    client_id: Mapped[str | None] = mapped_column(String(64))
    reply_to_id: Mapped[int | None] = mapped_column(BigInteger)
    forwarded: Mapped[dict | None] = mapped_column(JSONType)                  # {"name": автор исходного, "at": время исходного}
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ChatFile(Base):
    """Вложение сообщения: в базе — метаданные, файл — в хранилище вложений (те же правила, что у чата встречи). До отправки `message_id` пуст."""

    __tablename__ = "chat_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=False)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True)
    uploader_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)                 # image | file
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    file_state: Mapped[str] = mapped_column(String(12), default="ok", server_default="ok", nullable=False)
    file_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)


class ChatFolder(Base):
    """Личная папка пользователя («Работа», «Проекты» …). Удаление папки чаты не удаляет — они возвращаются в общий список."""

    __tablename__ = "chat_folders"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_chat_folders_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ChatReaction(Base):
    """Реакция участника на сообщение (как в мессенджерах): у одного участника на сообщении — одна реакция, повтор той же снимает её, другая — заменяет."""

    __tablename__ = "chat_reactions"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_chat_reactions"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class MeetingChatReaction(Base):
    """Реакция на сообщение чата встречи: от сотрудника или гостя (`actor_type`, `actor_id`), одна на участника; набор значков — общий с корпоративными чатами."""

    __tablename__ = "meeting_chat_reactions"
    __table_args__ = (UniqueConstraint("message_id", "actor_type", "actor_id", name="uq_meeting_chat_reactions"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("meeting_chat_messages.id", ondelete="CASCADE"), index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(8), nullable=False)          # user | guest
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_name: Mapped[str] = mapped_column(String(300), nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
