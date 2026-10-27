"""Модели защитного слоя: разрешённые подсети и журнал событий безопасности."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, BigIntPK, JSONType, UTCDateTime, utcnow


class AllowedNetwork(Base):
    """Разрешённый клиентский адрес или подсеть (ограничение доступа «Доверенные подсети»). Не путать с доверенными прокси."""

    __tablename__ = "allowed_networks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cidr: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    comment: Mapped[str] = mapped_column(String(300), default="", server_default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(200), default="", server_default="", nullable=False)


class SecurityEvent(Base):
    """Событие безопасности (раздел «События и блокировки»). Без паролей, токенов, гостевых ссылок и cookie: только адрес, запись (или её имя), тип, результат, правило и срок."""

    __tablename__ = "security_events"
    __table_args__ = (Index("ix_security_events_ip_at", "ip", "at"), Index("ix_security_events_kind_at", "kind", "at"))

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    account: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[str] = mapped_column(String(16), default="", server_default="", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    rule: Mapped[str | None] = mapped_column(String(80))
    until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    component: Mapped[str] = mapped_column(String(24), default="", server_default="", nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONType)
