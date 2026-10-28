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
            "empty": self.detail or "Модель вернула пустой ответ.",
            "limit_rejected": self.detail or "Провайдер отклонил запрошенную длину ответа.",
            "empty_length": self.detail or "Модель израсходовала весь лимит ответа на «рассуждения» и не написала текст.",
            "empty_refusal": self.detail or "Модель отказалась отвечать.",
            "empty_filtered": self.detail or "Ответ модели скрыт фильтром провайдера.",
            "provider_error": self.detail or "Провайдер вернул ошибку в теле ответа.",
            "unavailable": self.detail or "Выбранная языковая модель недоступна.",
            "invalid_response": self.detail or "Неожиданный формат ответа провайдера.",
            "timeout": "Таймаут при обращении к LLM.",
        }
        if c in table:
            return table[c]
        if c.startswith("http_"):
            return f"Провайдер ответил HTTP {c[5:]}."
        if c.startswith("transport_"):
            return f"Сетевая ошибка: {c[10:]}."
        return f"Ошибка LLM: {c}."


LIMIT_LADDER = (16384, 8192, 4096)      # ступени, если провайдер отклонил длину ответа и не назвал свой предел
MAX_RETRY_TOKENS = 16000     # потолок единственного повтора, если окно контекста модели неизвестно

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_thinking(text: str) -> str:
    """Убирает «размышления» моделей вида Qwen3/DeepSeek (<think>…</think>), в том числе оборванный незакрытый блок."""
    text = _THINK.sub("", text or "")
    i = text.lower().find("<think>")
    if i != -1:
        text = text[:i]
    return text.replace("</think>", "").strip()


def _parse_answer(data: dict, *, anthropic: bool) -> dict:
    """Ответ провайдера → текст и признаки. Учтены ответы шлюзов: content строкой или списком частей, reasoning_content/reasoning,
    refusal, блоки thinking у Anthropic. Неожиданная структура — KeyError/TypeError (вызывающий превращает в invalid_response)."""
    if anthropic:
        blocks = data["content"]
        usage = data.get("usage") or {}
        finish = str(data.get("stop_reason") or "")
        return {"text": "".join(b.get("text", "") for b in blocks if b.get("type") == "text"), "pt": usage.get("input_tokens"), "ct": usage.get("output_tokens"),
                "finish": finish, "truncated": finish == "max_tokens", "refusal": "", "reasoning": any(b.get("type") in ("thinking", "redacted_thinking") for b in blocks)}
    choice = data["choices"][0]
    msg = choice["message"]
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type", "text") in ("text", "output_text"))
    if not isinstance(content, str):
        raise TypeError("content")
    usage = data.get("usage") or {}
    finish = str(choice.get("finish_reason") or "")
    details = usage.get("completion_tokens_details") or {}
    reasoning = bool(msg.get("reasoning_content") or msg.get("reasoning") or details.get("reasoning_tokens"))
    return {"text": content, "pt": usage.get("prompt_tokens"), "ct": usage.get("completion_tokens"), "finish": finish, "truncated": finish == "length",
            "refusal": str(msg.get("refusal") or ""), "reasoning": reasoning}


_CAP_PATTERNS = (
    re.compile(r"supports at most (\d+)", re.I),                                    # OpenAI: «This model supports at most 16384 completion tokens»
    re.compile(r">\s*(\d+),\s*which is the maximum", re.I),                        # Anthropic: «max_tokens: 100000 > 64000, which is the maximum allowed…»
    re.compile(r"range of max_tokens is \[\s*\d+\s*,\s*(\d+)\s*\]", re.I),           # DeepSeek: «the valid range of max_tokens is [1, 8192]»
    re.compile(r"(?:max_tokens|max_completion_tokens)[^.\n]{0,80}?(?:less than or equal to|at most|no more than|<=|not exceed|maximum (?:of|is|value is))\s*(\d+)", re.I),
)
_CTX_PATTERN = re.compile(r"maximum context length is (\d+) tokens.{0,80}?\((\d+) in the messages", re.I | re.S)


def provider_token_cap(text: str, requested: int) -> int | None:
    """Предел длины ответа, который назвал сам провайдер в отказе «слишком большой max_tokens» (разные API формулируют по-разному). None — не названо или не меньше запрошенного."""
    for pat in _CAP_PATTERNS:
        m = pat.search(text or "")
        if m and 64 <= int(m.group(1)) < requested:
            return int(m.group(1))
    m = _CTX_PATTERN.search(text or "")
    if m:
        room = int(m.group(1)) - int(m.group(2)) - 32            # окно контекста минус вход (с небольшим запасом)
        if 64 <= room < requested:
            return room
    return None


class _Reply:
    """Ответ провайдера в виде, общем для обычного и потокового режимов: status_code, text (тело при ошибке) и json() / is_success."""

    def __init__(self, status_code: int, text: str, data: dict | None):
        self.status_code, self.text, self._data = status_code, text, data

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        if self._data is not None:
            return self._data
        import json as _json  # noqa: PLC0415

        return _json.loads(self.text)


class _StreamAcc:
    """Сборка потокового ответа (SSE) в структуру обычного ответа. Оборванный поток без признака завершения не выдаётся за готовый ответ."""

    def __init__(self, *, anthropic: bool):
        self.an, self.text, self.reasoning, self.refusal = anthropic, [], [], []
        self.finish, self.usage, self.error, self.done, self.thinking_blocks = "", {}, None, False, False

    def feed(self, line: str) -> bool:
        import json as _json  # noqa: PLC0415

        line = line.strip()
        if not line.startswith("data:"):
            return False
        payload = line[5:].strip()
        if payload == "[DONE]":
            self.done = True
            return True
        try:
            ev = _json.loads(payload)
        except ValueError:
            return False
        if not isinstance(ev, dict):
            return False
        if ev.get("error"):
            self.error = ev["error"]
            return True
        if self.an:
            t = ev.get("type")
            if t == "message_start":
                self.usage.update((ev.get("message") or {}).get("usage") or {})
            elif t == "content_block_start" and (ev.get("content_block") or {}).get("type") in ("thinking", "redacted_thinking"):
                self.thinking_blocks = True
            elif t == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta":
                    self.text.append(d.get("text", ""))
                elif d.get("type") == "thinking_delta":
                    self.thinking_blocks = True
            elif t == "message_delta":
                self.finish = (ev.get("delta") or {}).get("stop_reason") or self.finish
                self.usage.update(ev.get("usage") or {})
            elif t == "message_stop":
                self.done = True
                return True
            elif t == "error":
                self.error = ev.get("error") or ev
                return True
            return False
        if ev.get("usage"):
            self.usage = ev["usage"]
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            c = d.get("content")
            if isinstance(c, str):
                self.text.append(c)
            elif isinstance(c, list):
                self.text.extend(p.get("text", "") for p in c if isinstance(p, dict))
            r = d.get("reasoning_content") or d.get("reasoning")
            if r:
                self.reasoning.append(str(r))
            if d.get("refusal"):
                self.refusal.append(str(d["refusal"]))
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]
        return False

    def result(self) -> dict:
        if self.error:
            msg = self.error.get("message") if isinstance(self.error, dict) else str(self.error)
            raise LlmError("provider_error", f"Провайдер прервал ответ ошибкой: «{' '.join(str(msg).split())[:240]}».")
        if not self.done and not self.finish:
            raise LlmError("transport_StreamInterrupted", "Поток ответа оборван до завершения: документ не выдаётся за полный.")
        if self.an:
            blocks = ([{"type": "thinking"}] if self.thinking_blocks else []) + [{"type": "text", "text": "".join(self.text)}]
            return {"content": blocks, "stop_reason": self.finish, "usage": self.usage}
        msg = {"content": "".join(self.text), "reasoning_content": "".join(self.reasoning), "refusal": "".join(self.refusal)}
        return {"choices": [{"message": msg, "finish_reason": self.finish}], "usage": self.usage}


@dataclass
class LlmResult:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int
    # Ответ оборван по лимиту длины (finish_reason=length / stop_reason=max_tokens): текст неполный, вызывающий код обязан сообщить об этом пользователю
    truncated: bool = False
    finish_reason: str = ""         # причина остановки как её назвал провайдер (stop / length / end_turn / max_tokens …)
    max_tokens: int | None = None   # лимит ответа, который был отправлен в запросе (фактически применённый)
    requested: int | None = None    # лимит, запрошенный настройками задачи; отличается от max_tokens, если провайдер назвал меньший предел


class LlmClient:
    def __init__(self, cfg: LlmSettings, *, ca_file: str | None = None, transport: httpx.AsyncBaseTransport | None = None, local: bool = False,
                 purpose: str = "protocol"):
        self._c = cfg
        self.purpose = purpose      # summary | protocol — от задачи зависит максимальная длина ответа
        # счётчики за время жизни клиента (один документ): вызовы, обрывы по длине, причины остановки, время ответов — для журнала и статистики моделей
        self.stats: dict = {"calls": 0, "length": 0, "finish": {}, "ms": 0, "max_tokens": 0, "requested": 0, "clamps": [], "provider_cap": 0}
        self.local = local          # встроенная локальная модель (Qwen3): отключаем «размышления» и чистим служебные блоки из ответа
        self._verify: str | bool = ca_file if (cfg.use_corporate_ca and ca_file) else True
        self._transport = transport

    def _base(self) -> str:
        return (self._c.base_url or _DEFAULT_BASE.get(self._c.type, "")).rstrip("/")

    async def complete(self, system: str, user: str, *, max_tokens: int | None = None, json_schema: dict | None = None,
                       temperature: float | None = None, _retried: bool = False) -> LlmResult:
        c = self._c
        temp = c.temperature if temperature is None else temperature
        cap, _note = c.output_limit(self.purpose)
        limit = min(cap, max_tokens) if max_tokens else cap
        requested = limit
        if _retried and max_tokens:
            limit = max_tokens                  # повтор после «пустого» ответа: лимит заведомо выше обычного потолка задачи
        if c.context_window and not self.local and not _retried:
            # Окно контекста известно: место под ответ не отнимается у стенограммы. Русский текст ≈ 2 знака на токен (берём с запасом), остальное — под ответ.
            room = c.context_window - int((len(system) + len(user)) / 2.0) - 256
            if 1024 <= room < limit:
                self.stats["clamps"].append({"requested": limit, "applied": room, "reason": "context", "model": c.model})
                limit = room
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
        stream = bool(getattr(c, "stream_response", True)) and not self.local
        if stream:
            body["stream"] = True
            if c.type != "anthropic":
                body["stream_options"] = {"include_usage": True}      # без него потоковый ответ не содержит число токенов
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(c.timeout, connect=10), verify=self._verify,
                                         transport=self._transport, follow_redirects=False) as client:
                resp = await self._post(client, url, headers, body)
                for _ in range(6):                       # повторы с исправленным запросом: каждый вызван конкретным ответом провайдера, а не «на всякий случай»
                    low = (resp.text or "").lower()
                    if resp.status_code not in (400, 422):
                        break
                    if "stream_options" in body and "stream_options" in low:
                        log.warning("Провайдер не принимает stream_options — повтор без него", extra={"model": c.model})
                        body.pop("stream_options")
                    elif body.get("stream") and "stream" in low and "max_tokens" not in low:
                        log.warning("Провайдер отклонил потоковый режим — повтор обычным запросом", extra={"model": c.model})
                        body.pop("stream", None)
                        body.pop("stream_options", None)
                    elif "temperature" in body and "temperature" in low:
                        # часть API не принимает temperature (или ограничивает диапазон): повторяем без него; в настройках стоит снять «Передавать temperature»
                        log.warning("Провайдер отклонил temperature — повтор без параметра", extra={"model": c.model})
                        body.pop("temperature")
                    elif "max_tokens" in body and "max_completion_tokens" in low:
                        # модели с «рассуждениями» (o-серия, gpt-5…) принимают только max_completion_tokens
                        log.warning("Провайдер требует max_completion_tokens — повтор с ним", extra={"model": c.model})
                        body["max_completion_tokens"] = body.pop("max_tokens")
                    else:
                        # Модель не поддерживает запрошенный объём ответа. Называет предел сам провайдер — повтор с ним; не называет — одна ступенька вниз (16384 → 8192 → 4096).
                        # Это НЕ молчаливое уменьшение: запрошенное и применённое значения и причина записываются в диагностику задания.
                        key = "max_completion_tokens" if "max_completion_tokens" in body else "max_tokens"
                        pcap = provider_token_cap(resp.text, limit)
                        step = next((v for v in LIMIT_LADDER if v < limit), None) if re.search(r"token|context|length", low) else None
                        new_limit, reason = (pcap, "provider") if pcap else ((step, "rejected") if step else (None, ""))
                        if not new_limit:
                            break
                        log.warning("Провайдер отклонил длину ответа — повтор с меньшей", extra={"model": c.model, "requested": limit, "applied": new_limit, "reason": reason})
                        self.stats["clamps"].append({"requested": limit, "applied": new_limit, "reason": reason, "model": c.model})
                        body[key] = limit = new_limit
                    resp = await self._post(client, url, headers, body)
        except httpx.TimeoutException:
            raise LlmError("timeout") from None
        except httpx.HTTPError as exc:
            raise LlmError(f"transport_{type(exc).__name__}") from None
        if not resp.is_success:
            code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited",
                    400: "bad_request", 413: "bad_request", 422: "bad_request"}.get(resp.status_code, f"http_{resp.status_code}")
            if code == "bad_request" and re.search(r"token|context", resp.text or "", re.I):
                said = " ".join((resp.text or "").split())[:200]
                raise LlmError("limit_rejected", f"Провайдер отклонил запрос из-за длины (запрошено токенов ответа: {requested}{f', последняя попытка — {limit}' if limit != requested else ''}, модель {c.model}): «{said}». "
                                                  "Уменьшите «Максимальную длину ответа» в настройках подключения или укажите окно контекста модели.")
            raise LlmError(code)
        try:
            data = resp.json()
        except ValueError:
            raise LlmError("invalid_response", "Провайдер вернул не JSON" + (f": «{' '.join((resp.text or '').split())[:160]}»" if (resp.text or "").strip() else " (пустое тело ответа)") + ".") from None
        if isinstance(data, dict) and data.get("error") and not data.get("choices") and not data.get("content"):
            err = data["error"]
            said = (err.get("message") or err.get("code") or str(err)) if isinstance(err, dict) else str(err)
            raise LlmError("provider_error", f"Провайдер вернул ошибку вместо ответа (HTTP 200): «{' '.join(str(said).split())[:240]}» (модель {c.model}).")
        try:
            parsed = _parse_answer(data, anthropic=c.type == "anthropic")
        except (KeyError, IndexError, TypeError, AttributeError):
            keys = ", ".join(sorted(data)[:8]) if isinstance(data, dict) else type(data).__name__
            raise LlmError("invalid_response", f"Неожиданный формат ответа провайдера (поля верхнего уровня: {keys}; модель {c.model}).") from None
        text, pt, ct, finish, truncated = parsed["text"], parsed["pt"], parsed["ct"], parsed["finish"], parsed["truncated"]
        if "<think" in text.lower():
            parsed["reasoning"] = True
        text = strip_thinking(text)
        dur = int((time.monotonic() - started) * 1000)
        st = self.stats
        st["calls"] += 1
        st["length"] += 1 if truncated else 0
        st["ms"] += dur
        st["max_tokens"] = max(st["max_tokens"], limit)
        st["requested"] = max(st["requested"], requested)
        if truncated and ct and limit >= 4000 and ct < limit // 4:
            st["provider_cap"] = max(st["provider_cap"], ct)         # оборван намного раньше лимита запроса: предел задаёт провайдер/шлюз, а не настройки
        st["finish"][finish or "?"] = st["finish"].get(finish or "?", 0) + 1
        if not text.strip():
            return await self._empty(system, user, parsed, limit, dur, _retried, max_tokens=max_tokens, json_schema=json_schema, temperature=temperature)
        log.info("LLM ответила", extra={"model": c.model, "prompt_tokens": pt, "completion_tokens": ct, "duration_ms": dur})
        if truncated:
            log.warning("Ответ LLM оборван по лимиту длины", extra={"model": c.model, "completion_tokens": ct, "max_tokens": limit})
        return LlmResult(text.strip(), pt, ct, dur, truncated, finish, limit, requested)

    async def _post(self, client: httpx.AsyncClient, url: str, headers: dict, body: dict) -> "_Reply":
        """Один запрос к провайдеру. Потоковый режим: куски ответа собираются в ту же структуру, что и у обычного ответа, так что дальше разбор один.
        Время ожидания (`timeout`) — пауза между кусками, а не общая длительность: длинный документ не обрывается, пока модель пишет."""
        if not body.get("stream"):
            r = await client.post(url, headers=headers, json=body)
            return _Reply(r.status_code, r.text, None)
        async with client.stream("POST", url, headers=headers, json=body) as r:
            ctype = (r.headers.get("content-type") or "").lower()
            if r.status_code >= 400 or "event-stream" not in ctype:
                raw = (await r.aread()).decode("utf-8", "replace")               # ошибка или провайдер проигнорировал stream: обычное тело
                return _Reply(r.status_code, raw, None)
            acc = _StreamAcc(anthropic=self._c.type == "anthropic")
            async for line in r.aiter_lines():
                if acc.feed(line):
                    break
            return _Reply(r.status_code, "", acc.result())

    async def _empty(self, system: str, user: str, parsed: dict, limit: int, dur: int, retried: bool, **kw) -> LlmResult:
        """Пустой текст ответа: причина берётся из ответа провайдера, а не угадывается. Единственный случай, где повтор оправдан, — лимит ответа
        целиком ушёл на «рассуждения» модели (finish=length, текста нет): один повтор с лимитом вдвое больше (в пределах окна контекста)."""
        c, finish = self._c, parsed["finish"]
        ct = parsed["ct"]
        ctx = f"модель {c.model}, finish_reason={finish or 'нет'}, токенов ответа {ct if ct is not None else '?'} из {limit}"
        if parsed["refusal"] or finish == "refusal":
            code, why = "empty_refusal", "Модель отказалась отвечать на этот запрос" + (f": «{parsed['refusal'][:160]}»" if parsed["refusal"] else "") + "."
        elif finish in ("content_filter", "safety", "blocked"):
            code, why = "empty_filtered", "Ответ скрыт фильтром безопасности провайдера. Выберите другую модель или измените материалы."
        elif parsed["truncated"] and ct and limit >= 4000 and ct < limit // 4:
            code = "empty_length"
            self.stats["provider_cap"] = max(self.stats["provider_cap"], ct)
            why = (f"Провайдер оборвал ответ на {ct} токенах, хотя запрошено {limit}: фактический предел задаёт сама модель или шлюз, а не настройки Peregovorka. "
                   "Проверьте ограничения модели и шлюза (максимум токенов ответа) или выберите другую модель.")
        elif parsed["truncated"]:
            code = "empty_length"
            why = (f"Модель израсходовала весь лимит ответа ({limit} токенов) на «рассуждения» и не успела написать текст. "
                   "Увеличьте лимит ответа в настройках подключения, отключите режим рассуждений у модели или выберите другую модель.")
            hard = max(256, c.context_window - 2048) if c.context_window > 4096 else (c.context_window // 2 if c.context_window else MAX_RETRY_TOKENS)
            bigger = min(limit * 2, hard)
            if not retried and bigger > limit:
                self.stats["empty_retries"] = self.stats.get("empty_retries", 0) + 1
                log.warning("Пустой ответ: лимит ушёл на рассуждения — один повтор с большим лимитом", extra={"model": c.model, "max_tokens": limit, "retry_max_tokens": bigger})
                kw["max_tokens"] = bigger
                return await self.complete(system, user, _retried=True, **kw)
        elif not retried and not parsed["reasoning"] and ct in (None, 0) and finish in ("", "stop", "end_turn"):
            # Провайдер завершил ответ «нормально», но ничего не написал (перегруженный шлюз, сбой на его стороне): один повтор того же запроса. Лимит не повышается, потому что дело не в нём.
            self.stats["empty_retries"] = self.stats.get("empty_retries", 0) + 1
            log.warning("Пустой ответ при нормальном завершении — один повтор", extra={"model": c.model, "finish": finish})
            return await self.complete(system, user, _retried=True, **kw)
        else:
            code, why = "empty", "Модель вернула пустой ответ" + (" (в ответе были только «рассуждения»)" if parsed["reasoning"] else "") + "."
        self.stats.setdefault("empty", []).append({"code": code, "finish": finish, "completion_tokens": ct, "max_tokens": limit, "reasoning": parsed["reasoning"], "retried": retried})
        log.warning("Пустой ответ LLM", extra={"model": c.model, "code": code, "finish": finish, "completion_tokens": ct, "max_tokens": limit, "duration_ms": dur})
        raise LlmError(code, f"{why} ({ctx})")

    async def test(self) -> tuple[bool, str, int]:
        started = time.monotonic()
        try:
            res = await self.complete("Отвечай одним словом.", "Скажи: готово")
            return True, f"LLM отвечает (модель {self._c.model}): «{res.text[:60]}»", int((time.monotonic() - started) * 1000)
        except LlmError as exc:
            return False, exc.describe(), int((time.monotonic() - started) * 1000)
