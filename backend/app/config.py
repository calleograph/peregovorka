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
    app_built_at: str = "unknown"
    app_public_url: str = "http://localhost:8080"
    log_level: str = "INFO"
    log_format: str = "json"  # json | console
    docs_enabled: bool = False   # документация и схема внутреннего API по умолчанию закрыты (в compose было false, в коде — true: «открыто по умолчанию» не должно зависеть от окружения)
    data_dir: str = "/data"
    trusted_proxy_hops: int = Field(default=1, ge=1, le=10)
    # Адреса прокси, которые в X-Forwarded-For пропускаются справа налево (через запятую, CIDR или IP): локальный TLS-терминатор/host-nginx и т. п.
    trusted_proxy_cidrs: str = "127.0.0.0/8,::1/128"

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
    # сведения о развёртывании LiveKit для диагностики (compose передаёт из .env)
    livekit_server_version: str = ""
    asr_internal_url: str = "http://asr:8090"  # управление моделями ASR (статус, тест, сравнение) — только внутри сети проекта
    # --- локальная языковая модель (контейнер llm-local, llama.cpp; compose передаёт значения из .env). Модель хранится вне образа.
    local_llm_url: str = "http://llm-local:8080"
    local_llm_models_dir: str = "/models/llm"
    local_llm_enabled: str = "yes"
    local_llm_model_file: str = "Qwen3-1.7B-Q4_K_M.gguf"
    local_llm_model_alias: str = "qwen3-1.7b-q4_k_m"
    local_llm_model_bytes: int = 1_282_439_584
    local_llm_model_sha256: str = "72c5c3cb38fa32d5256e2fe30d03e7a64c6c79e668ad84057e3bd66e250b24fb"
    # --- SIP-телефония (контейнер livekit-sip, профиль compose `sip`; по умолчанию выключена и порты не открыты)
    sip_enabled: str = "no"
    sip_signaling_port: int = 5060
    sip_rtp_start: int = 20000
    sip_rtp_end: int = 20100
    sip_media_ip: str = ""
    sip_allowed_cidrs: str = ""
    sip_health_url: str = "http://livekit-sip:8081"
    livekit_node_ip: str = ""
    livekit_rtc_tcp_port: int = 0
    livekit_rtc_udp_port: int = 0

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
    login_max_failures_per_user: int = Field(default=20, ge=1)   # неверных вводов на логин до блокировки входа (в нашей системе, не в AD)
    login_max_failures_per_ip: int = Field(default=60, ge=1)
    login_failure_window_seconds: int = Field(default=900, ge=10)
    login_lockout_seconds: int = Field(default=300, ge=10)

    # --- секреты приложения
    app_master_key: str = ""
    internal_api_token: str = ""

    # --- встречи и хранение
    meeting_end_grace_seconds: int = Field(default=60, ge=0)
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
