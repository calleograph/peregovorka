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
            "unavailable": self.detail or "Выбранная языковая модель недоступна.",
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
    # Ответ оборван по лимиту длины (finish_reason=length / stop_reason=max_tokens): текст неполный, вызывающий код обязан сообщить об этом пользователю
    truncated: bool = False
    finish_reason: str = ""         # причина остановки как её назвал провайдер (stop / length / end_turn / max_tokens …)
    max_tokens: int | None = None   # лимит ответа, который был отправлен в запросе


class LlmClient:
    def __init__(self, cfg: LlmSettings, *, ca_file: str | None = None, transport: httpx.AsyncBaseTransport | None = None, local: bool = False,
                 purpose: str = "protocol"):
        self._c = cfg
        self.purpose = purpose      # summary | protocol — от задачи зависит максимальная длина ответа
        # счётчики за время жизни клиента (один документ): вызовы, обрывы по длине, причины остановки, время ответов — для журнала и статистики моделей
        self.stats: dict = {"calls": 0, "length": 0, "finish": {}, "ms": 0, "max_tokens": 0}
        self.local = local          # встроенная локальная модель (Qwen3): отключаем «размышления» и чистим служебные блоки из ответа
        self._verify: str | bool = ca_file if (cfg.use_corporate_ca and ca_file) else True
        self._transport = transport

    def _base(self) -> str:
        return (self._c.base_url or _DEFAULT_BASE.get(self._c.type, "")).rstrip("/")

    async def complete(self, system: str, user: str, *, max_tokens: int | None = None, json_schema: dict | None = None,
                       temperature: float | None = None) -> LlmResult:
        c = self._c
        temp = c.temperature if temperature is None else temperature
        cap, _note = c.output_limit(self.purpose)
        limit = min(cap, max_tokens) if max_tokens else cap
        if not c.enabled or not c.model:
            raise LlmError("not_configured")
        started = time.monotonic()
        use_temp = c.send_temperature or self.local
        sep_system = c.supports_system or self.local
        if c.type == "anthropic":
            url = f"{self._base()}/messages"
            headers = {"x-api-key": c.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            body = {"model": c.model, "max_tokens": limit, "messages": [{"role": "user", "content": user if sep_system else f"{system}\n\n{user}"}]}
            if sep_system:
                body["system"] = system
            if use_temp:
                body["temperature"] = temp
        else:
            url = f"{self._base()}/chat/completions"
            headers = {"Authorization": f"Bearer {c.api_key}", "content-type": "application/json"} if c.api_key \
                else {"content-type": "application/json"}
            body = {"model": c.model, "max_tokens": limit,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}] if sep_system
                    else [{"role": "user", "content": f"{system}\n\n{user}"}]}
            if use_temp:
                body["temperature"] = temp
            if json_schema is not None and (c.supports_json or self.local):
                # ответ строго по схеме (llama.cpp строит грамматику): вместо «текста, похожего на JSON» приходит разбираемый JSON
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "extract", "strict": True, "schema": json_schema}}
            if self.local and sep_system:
                # Qwen3 по умолчанию «думает» вслух (блок <think>) — для резюме это лишние минуты на CPU: выключаем и мягкой командой /no_think в запросе
                body["chat_template_kwargs"] = {"enable_thinking": False}
                body["messages"][0]["content"] = system + "\n/no_think"
            if c.type == "openai_compatible" and c.routing_provider:
                body["provider"] = {"only": [c.routing_provider]}
        extra = c.all_headers()
        if extra:
            headers = {**extra, **headers}          # служебные заголовки провайдера (ключ, content-type) не перекрываются
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(c.timeout, connect=10), verify=self._verify,
                                         transport=self._transport, follow_redirects=False) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code in (400, 422) and "temperature" in body and "temperature" in resp.text.lower():
                    # часть API не принимает temperature (или ограничивает диапазон): повторяем без него; в настройках стоит снять «Передавать temperature»
                    log.warning("Провайдер отклонил temperature — повтор без параметра", extra={"model": c.model})
                    body.pop("temperature")
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
                finish = str(data.get("stop_reason") or "")
                truncated = finish == "max_tokens"
            else:
                text = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage") or {}
                pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
                finish = str(data["choices"][0].get("finish_reason") or "")
                truncated = finish == "length"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LlmError("invalid_response") from None
        text = strip_thinking(text)
        if not text.strip():
            raise LlmError("empty")
        dur = int((time.monotonic() - started) * 1000)
        st = self.stats
        st["calls"] += 1
        st["length"] += 1 if truncated else 0
        st["ms"] += dur
        st["max_tokens"] = max(st["max_tokens"], limit)
        st["finish"][finish or "?"] = st["finish"].get(finish or "?", 0) + 1
        log.info("LLM ответила", extra={"model": c.model, "prompt_tokens": pt, "completion_tokens": ct, "duration_ms": dur})
        if truncated:
            log.warning("Ответ LLM оборван по лимиту длины", extra={"model": c.model, "completion_tokens": ct, "max_tokens": limit})
        return LlmResult(text.strip(), pt, ct, dur, truncated, finish, limit)

    async def test(self) -> tuple[bool, str, int]:
        started = time.monotonic()
        try:
            res = await self.complete("Отвечай одним словом.", "Скажи: готово")
            return True, f"LLM отвечает (модель {self._c.model}): «{res.text[:60]}»", int((time.monotonic() - started) * 1000)
        except LlmError as exc:
            return False, exc.describe(), int((time.monotonic() - started) * 1000)
