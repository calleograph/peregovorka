"""Подключения к языковым моделям. Модель — внешний или внутренний сервис организации, к которому сервер обращается по API (адрес, модель, токен);
встроенной модели нет. Все обращения идут через `LlmClient.complete()`, где перед отправкой обязательно маскируются секреты."""
from __future__ import annotations

import httpx

from ..config import Settings
from ..integrations.llm import LlmClient
from .settings import LlmSettings


class LlmGateway:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._s = settings
        self._transport = transport

    @staticmethod
    def effective(cfg: LlmSettings) -> LlmSettings:
        """Настройки для вызова: «отключена» — enabled=False, «подключена» — enabled=True."""
        if cfg.effective_provider == "off":
            return cfg.model_copy(update={"enabled": False})
        return cfg.model_copy(update={"enabled": True}) if cfg.provider == "external" else cfg

    def client(self, cfg: LlmSettings, *, ca_file: str | None = None, transport: httpx.AsyncBaseTransport | None = None, purpose: str = "protocol") -> LlmClient:
        return LlmClient(self.effective(cfg), ca_file=ca_file, transport=transport or self._transport, purpose=purpose)
