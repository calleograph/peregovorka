"""Конфигурация backend. Источник — только переменные окружения (compose/.env).

Ничего специфичного для организации (домены, адреса, порты) в коде нет.
"""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # --- экземпляр
    app_version: str = "0.0.0"
    app_git_commit: str = "unknown"
    app_public_url: str = "http://localhost:8080"
    log_level: str = "INFO"
    log_format: str = "json"  # json | console
    docs_enabled: bool = True
    data_dir: str = "/data"
    trusted_proxy_hops: int = Field(default=1, ge=1, le=10)

    # --- PostgreSQL (database_url имеет приоритет — удобно для тестов)
    database_url: str | None = None
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "peregovorka"
    postgres_user: str = "peregovorka"
    postgres_password: str = ""

    # --- Redis (redis_url имеет приоритет)
    redis_url: str | None = None
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = ""

    # --- LiveKit
    livekit_internal_url: str = "ws://livekit:7880"
    livekit_public_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_token_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    # --- Active Directory
    ldap_uris: str = ""
    ldap_base_dn: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_ca_file: str = ""
    ldap_login_attribute: str = "sAMAccountName"
    ldap_display_name_attribute: str = "displayName"
    ldap_admin_group_dn: str = ""
    ldap_access_group_dn: str = ""
    ldap_timeout_seconds: int = Field(default=5, ge=1, le=60)

    # --- сессии и защита входа
    cookie_secure: bool = True
    cookie_name: str = "vm_session"
    session_idle_timeout_seconds: int = Field(default=28800, ge=60)
    session_absolute_timeout_seconds: int = Field(default=86400, ge=60)
    login_max_failures_per_user: int = Field(default=3, ge=1)
    login_max_failures_per_ip: int = Field(default=20, ge=1)
    login_failure_window_seconds: int = Field(default=900, ge=10)
    login_lockout_seconds: int = Field(default=900, ge=10)

    # --- секреты приложения
    app_master_key: str = ""
    internal_api_token: str = ""

    # --- встречи и хранение
    meeting_end_grace_seconds: int = Field(default=30, ge=0)
    default_text_retention_days: int | None = 365
    default_audio_retention_days: int | None = 30
    segment_consumer_block_ms: int = Field(default=2000, ge=10, le=10000)
    room_password_max_failures: int = 5
    room_password_failure_window_seconds: int = 300

    @field_validator("ldap_uris")
    @classmethod
    def _only_ldaps(cls, value: str) -> str:
        for uri in [u.strip() for u in value.split(",") if u.strip()]:
            if not uri.lower().startswith("ldaps://"):
                raise ValueError("LDAP_URIS: разрешены только адреса ldaps:// (проверка сертификата обязательна)")
        return value

    @field_validator("default_text_retention_days", "default_audio_retention_days", mode="before")
    @classmethod
    def _empty_is_none(cls, value):  # '' в .env = бессрочно
        return None if value in ("", None) else value

    # --- производные значения
    @property
    def sqlalchemy_url(self) -> str | URL:
        if self.database_url:
            return self.database_url
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def recordings_path(self) -> str:
        return f"{self.data_dir.rstrip('/')}/recordings"

    @property
    def effective_redis_url(self) -> str:
        if self.redis_url:
            return self.redis_url
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/0"

    @property
    def ldap_uri_list(self) -> list[str]:
        return [u.strip() for u in self.ldap_uris.split(",") if u.strip()]

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.app_public_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def livekit_http_url(self) -> str:
        url = self.livekit_internal_url
        if url.startswith("wss://"):
            return "https://" + url[len("wss://"):]
        if url.startswith("ws://"):
            return "http://" + url[len("ws://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
