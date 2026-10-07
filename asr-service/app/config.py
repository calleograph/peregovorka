"""Конфигурация ASR-сервиса (переменные окружения из compose/.env)."""
from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_GGUF_ARGS = "-m {model} -f {wav} -t {threads}"


class AsrSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_version: str = "0.0.0"
    app_git_commit: str = "unknown"
    app_built_at: str = "unknown"
    log_level: str = "INFO"
    log_format: str = "json"
    health_port: int = 8090

    # провайдер распознавания
    asr_provider: str = "gigaam"
    asr_model_name: str = "v3_e2e_rnnt"
    asr_model_id: str = "gigaam-v3-e2e-rnnt-full"  # модель по умолчанию, пока администратор не выбрал другую (хранится в админке)
    asr_gguf_bin: str = "transcribe"  # ЗАПАСНОЙ вариант: внешний исполняемый файл, если Python-привязки transcribe-cpp недоступны (обычно не нужен)
    asr_gguf_args: str = "-m {model} -f {wav} -t {threads}"  # шаблон аргументов запасного варианта
    internal_api_token: str = ""  # защита изменяющих эндпоинтов (активация, тест, сравнение); пусто — только для разработки
    asr_model_dir: str = "/models/gigaam"
    asr_device: str = "cpu"  # cpu | cuda
    asr_cpu_threads: int = Field(default=0, ge=0)  # intra-op потоки torch; 0 = по умолчанию библиотеки (все ядра хоста)
    asr_interop_threads: int = Field(default=0, ge=0)  # inter-op потоки torch; 0 = по умолчанию
    asr_language: str = "ru"

    # очередь и параллелизм инференса (одна общая копия модели на процесс)
    asr_max_concurrent_inference: int = Field(default=1, ge=1, le=16)
    asr_queue_size: int = Field(default=64, ge=1, le=10000)

    # VAD / сегментация
    asr_vad_threshold: float = Field(default=0.5, gt=0, lt=1)
    asr_vad_end_silence_ms: int = Field(default=700, ge=100, le=5000)
    asr_vad_min_speech_ms: int = Field(default=250, ge=32, le=5000)
    asr_vad_pad_ms: int = Field(default=200, ge=0, le=1000)
    asr_max_segment_seconds: float = Field(default=20.0, ge=3.0, le=25.0)  # GigaAM: не длиннее 25 с

    # инфраструктура
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = ""
    redis_url: str | None = None
    livekit_internal_url: str = "ws://livekit:7880"
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    recordings_dir: str = "/data/recordings"

    @field_validator("asr_gguf_args", mode="before")
    @classmethod
    def _gguf_args(cls, v):
        return v if v else DEFAULT_GGUF_ARGS  # пустое значение (compose передаёт пустую строку) — шаблон по умолчанию

    @field_validator("asr_gguf_bin", mode="before")
    @classmethod
    def _gguf_bin(cls, v):
        return v if v else "transcribe"

    @property
    def effective_redis_url(self) -> str:
        if self.redis_url:
            return self.redis_url
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/0"
