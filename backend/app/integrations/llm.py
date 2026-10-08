"""Шлюз к LLM (портирован из legacy/php/src/Llm/Gateway: адаптеры OpenAI / Anthropic / OpenAI-compatible).

К LLM попадает ТОЛЬКО уже обезличенный текст: вызывающий код (services/protocols.py) обязан получить его
от AnonymizerClient; сам шлюз текст не проверяет. Ключ API передаётся из зашифрованных настроек и не логируется.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

from ..services.settings import LlmSettings

log = logging.getLogger("app.llm")

_DEFAULT_BASE = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1"}


class LlmError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail

    def describe(self) -> str:
        c = self.code
        table = {
            "not_configured": "LLM не настроена или выключена.",
            "unauthorized": "Провайдер отклонил ключ API.",
            "forbidden": "Доступ запрещён провайдером.",
            "rate_limited": "Превышен лимит запросов провайдера. Повторите позже.",
            "bad_request": "Провайдер отклонил запрос (возможно, превышен контекст модели).",
            "not_found": "Модель или адрес API не найдены.",
            "empty": "Модель вернула пустой ответ.",
            "invalid_response": "Неожиданный формат ответа провайдера.",
            "timeout": "Таймаут при обращении к LLM.",
        }
        if c in table:
            return table[c]
        if c.startswith("http_"):
            return f"Провайдер ответил HTTP {c[5:]}."
        if c.startswith("transport_"):
            return f"Сетевая ошибка: {c[10:]}."
        return f"Ошибка LLM: {c}."


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_thinking(text: str) -> str:
    """Убирает «размышления» моделей вида Qwen3/DeepSeek (<think>…</think>), в том числе оборванный незакрытый блок."""
    text = _THINK.sub("", text or "")
    i = text.lower().find("<think>")
    if i != -1:
        text = text[:i]
    return text.replace("</think>", "").strip()


@dataclass
class LlmResult:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int


class LlmClient:
    def __init__(self, cfg: LlmSettings, *, ca_file: str | None = None, transport: httpx.AsyncBaseTransport | None = None, local: bool = False):
        self._c = cfg
        self.local = local          # встроенная локальная модель (Qwen3): отключаем «размышления» и чистим служебные блоки из ответа
        self._verify: str | bool = ca_file if (cfg.use_corporate_ca and ca_file) else True
        self._transport = transport

    def _base(self) -> str:
        return (self._c.base_url or _DEFAULT_BASE.get(self._c.type, "")).rstrip("/")

    async def complete(self, system: str, user: str) -> LlmResult:
        c = self._c
        if not c.enabled or not c.model:
            raise LlmError("not_configured")
        started = time.monotonic()
        if c.type == "anthropic":
            url = f"{self._base()}/messages"
            headers = {"x-api-key": c.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            body = {"model": c.model, "max_tokens": c.max_tokens, "temperature": c.temperature, "system": system,
                    "messages": [{"role": "user", "content": user}]}
        else:
            url = f"{self._base()}/chat/completions"
            headers = {"Authorization": f"Bearer {c.api_key}", "content-type": "application/json"} if c.api_key \
                else {"content-type": "application/json"}
            body = {"model": c.model, "max_tokens": c.max_tokens, "temperature": c.temperature,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if self.local:
                # Qwen3 по умолчанию «думает» вслух (блок <think>) — для резюме это лишние минуты на CPU: выключаем и мягкой командой /no_think в запросе
                body["chat_template_kwargs"] = {"enable_thinking": False}
                body["messages"][0]["content"] = system + "\n/no_think"
            if c.type == "openai_compatible" and c.routing_provider:
                body["provider"] = {"only": [c.routing_provider]}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(c.timeout, connect=10), verify=self._verify,
                                         transport=self._transport, follow_redirects=False) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException:
            raise LlmError("timeout") from None
        except httpx.HTTPError as exc:
            raise LlmError(f"transport_{type(exc).__name__}") from None
        if not resp.is_success:
            code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited",
                    400: "bad_request", 413: "bad_request", 422: "bad_request"}.get(resp.status_code, f"http_{resp.status_code}")
            raise LlmError(code)
        try:
            data = resp.json()
            if c.type == "anthropic":
                text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
                usage = data.get("usage") or {}
                pt, ct = usage.get("input_tokens"), usage.get("output_tokens")
            else:
                text = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage") or {}
                pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LlmError("invalid_response") from None
        text = strip_thinking(text)
        if not text.strip():
            raise LlmError("empty")
        dur = int((time.monotonic() - started) * 1000)
        log.info("LLM ответила", extra={"model": c.model, "prompt_tokens": pt, "completion_tokens": ct, "duration_ms": dur})
        return LlmResult(text.strip(), pt, ct, dur)

    async def test(self) -> tuple[bool, str, int]:
        started = time.monotonic()
        try:
            res = await self.complete("Отвечай одним словом.", "Скажи: готово")
            return True, f"LLM отвечает (модель {self._c.model}): «{res.text[:60]}»", int((time.monotonic() - started) * 1000)
        except LlmError as exc:
            return False, exc.describe(), int((time.monotonic() - started) * 1000)
