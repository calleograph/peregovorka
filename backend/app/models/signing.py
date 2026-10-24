"""Подпись клиента Windows и корпоративные сборки. Закрытых ключей здесь нет и быть не может: сервер хранит только сведения о сертификатах
(и их открытую часть), задания для подписывающих агентов и готовые подписанные пакеты. Ключи живут в хранилище Windows на узле агента.
Схема — миграция 0042."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONType, UTCDateTime, utcnow


class SigningAgent(Base):
    """Подписывающий агент на доверенном узле организации. Авторизация — собственный токен (хранится только SHA-256), полномочия — только
    задания подписи из фиксированного списка; удалённого выполнения команд нет."""

    __tablename__ = "signing_agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    host: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    info: Mapped[dict | None] = mapped_column(JSONType)                      # ОС, PowerShell, signtool, источник выпусков (без секретов)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_ip: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SigningCert(Base):
    """Сертификат подписи кода на узле агента: собственный (самоподписанный, создан агентом) или организации (выпущен УЦ, зарегистрирован по отпечатку).
    `cer` — только открытая часть (DER) для распространения доверия. Активный — тот, которым подписываются новые сборки."""

    __tablename__ = "signing_certs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signing_agents.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)             # self | corporate
    thumbprint: Mapped[str] = mapped_column(String(40), nullable=False)       # SHA-1 (так его показывает Windows)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(400), nullable=False)
    issuer: Mapped[str] = mapped_column(String(400), nullable=False)
    serial: Mapped[str] = mapped_column(String(80), nullable=False)
    not_before: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    not_after: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    key_alg: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    sig_alg: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    check: Mapped[dict | None] = mapped_column(JSONType)                      # последняя проверка: назначение, цепочка, ключ, пробная подпись
    cer: Mapped[bytes | None] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SigningJob(Base):
    """Задание агенту: create_cert | register_cert | check_cert | build. Параметры — только данные (адрес сервера, название, флаги), никаких команд.
    Результат сборки — файлы в каталоге данных сервера (`signing/<id>/`) и их SHA-256/подписи в `result`."""

    __tablename__ = "signing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")    # queued | running | done | failed | cancelled
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signing_agents.id", ondelete="CASCADE"), index=True, nullable=False)
    cert_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("signing_certs.id", ondelete="SET NULL"))
    params: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONType)
    progress: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
