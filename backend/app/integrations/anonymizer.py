"""Клиент API обезличивания (портирован из legacy/php/src/Llm/Anonymization/AnonymizationGateway.php).

ЕДИНСТВЕННОЕ место, откуда уходят данные к сервису обезличивания; любой код, отправляющий
текст во внешнюю LLM, обязан сначала получить результат отсюда. Fail closed: при любой
неопределённости (не настроено, таймаут, не-2xx, не JSON, нет поля результата, пустой результат
для непустого входа, verification.clean != true) выбрасывается AnonymizerError, данные наружу не идут.
В журнал пишутся только метаданные (длины, число фрагментов), текст и ответ не логируются.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ..services.settings import AnonymizerSettings

log = logging.getLogger("app.anonymizer")


class AnonymizerError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail

    def describe(self) -> str:
        c = self.code
        if c == "not_configured":
            return "Сервис обезличивания не настроен или выключен."
        if c == "invalid_json":
            return "Сервис вернул не JSON (проверьте адрес — возможно, это страница-заглушка)."
        if c == "missing_field":
            return "В ответе нет поля с обезличенным текстом."
        if c == "status_not_ok":
            return "Сервис сообщил о неуспешной обработке."
        if c == "empty_result":
            return "Сервис вернул пустой результат для непустого текста."
        if c == "not_clean":
            return "Сервис вернул verification.clean = false — такой текст наружу не отправляется."
        if c == "not_docclean":
            return "По указанному адресу не отвечает API DocClean (нет спецификации openapi)."
        if c == "api_unauthorized":
            return "DocClean отклонил токен (UNAUTHORIZED)."
        if c == "api_api_disabled":
            return "API в DocClean выключено (API_DISABLED)."
        if c.startswith("api_"):
            return f"DocClean вернул ошибку {c[4:].upper()}."
        if c.startswith("http_"):
            return f"Сервис ответил HTTP {c[5:]}."
        if c.startswith("transport_"):
            return f"Сетевая ошибка: {c[10:]}."
        return f"Ошибка обезличивания: {c}."


@dataclass
class Anonymized:
    text: str
    chunks: int
    replaced: int | None
    duration_ms: int


def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Делит по границам строк, чтобы не рвать сущности (ФИО, адреса) посередине."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    cur = ""
    for line in re.split(r"(?<=\n)", text):
        while len(line) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            piece = line[:max_chars]
            cut = max(piece.rfind(" "), int(max_chars * 0.5))
            chunks.append(line[:cut])
            line = line[cut:]
        if len(cur) + len(line) > max_chars:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks


def _get_path(data, path: str):
    node = data
    for seg in path.split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def _set_path(data: dict, path: str, value) -> None:
    node = data
    parts = path.split(".")
    for seg in parts[:-1]:
        if not isinstance(node.get(seg), dict):
            node[seg] = {}
        node = node[seg]
    node[parts[-1]] = value


class AnonymizerClient:
    def __init__(self, cfg: AnonymizerSettings, *, ca_file: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self._c = cfg
        self._verify: str | bool = ca_file if (cfg.use_corporate_ca and ca_file) else True
        self._transport = transport

    # ------------------------------------------------------------------ служебное
    def _client(self, timeout: int | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout or self._c.timeout, connect=self._c.connect_timeout),
            verify=self._verify, transport=self._transport, follow_redirects=False)

    def _headers(self) -> dict[str, str]:
        token = self._c.token
        kind = "bearer" if self._c.profile == "docclean" else self._c.auth_type
        if kind == "none" or not token:
            return {}
        if kind == "bearer":
            return {"Authorization": f"Bearer {token}"}
        if kind == "header":
            return {self._c.auth_header_name: token}
        if kind == "basic":
            return {"Authorization": "Basic " + base64.b64encode(f"{self._c.auth_username}:{token}".encode()).decode()}
        return {}

    def _docclean_url(self, action: str, query: dict | None = None) -> str:
        base = self._c.base_url.rstrip("/")
        if not base.lower().endswith(".php"):
            base += "/index.php"
        return f"{base}?{urlencode({'action': action, **(query or {})})}"

    async def _send(self, client: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
        try:
            return await client.request(method, url, **kw)
        except httpx.ConnectTimeout:
            raise AnonymizerError("transport_timeout") from None
        except httpx.TimeoutException:
            raise AnonymizerError("transport_timeout") from None
        except httpx.HTTPError as exc:
            raise AnonymizerError(f"transport_{type(exc).__name__}") from None

    @staticmethod
    def _json(resp: httpx.Response):
        try:
            return resp.json()
        except ValueError:
            return None

    # ------------------------------------------------------------------- основное
    async def anonymize(self, text: str, purpose: str = "protocol") -> Anonymized:
        c = self._c
        if not c.enabled or not c.base_url:
            raise AnonymizerError("not_configured")
        if not text.strip():
            return Anonymized("", 0, 0, 0)
        started = time.monotonic()
        chunks = split_into_chunks(text, c.max_chunk_chars)
        out: list[str] = []
        replaced = 0
        try:
            async with self._client() as client:
                for ch in chunks:
                    if c.profile == "docclean":
                        piece, n = await self._docclean_chunk(client, ch)
                        replaced += n
                    else:
                        piece = await self._generic_chunk(client, ch)
                    out.append(piece)
        except AnonymizerError as exc:
            log.error("Обезличивание не удалось", extra={"purpose": purpose, "code": exc.code, "chunks": len(chunks),
                                                         "input_chars": len(text)})
            raise
        dur = int((time.monotonic() - started) * 1000)
        log.info("Текст обезличен", extra={"purpose": purpose, "chunks": len(chunks), "input_chars": len(text),
                                           "output_chars": sum(len(x) for x in out), "duration_ms": dur})
        return Anonymized("".join(out), len(chunks), replaced if c.profile == "docclean" else None, dur)

    async def _docclean_chunk(self, client: httpx.AsyncClient, chunk: str) -> tuple[str, int]:
        payload: dict = {"text": chunk, "mode": self._c.docclean_mode}
        groups = [g for g in self._c.docclean_groups.split(",") if g]
        if groups:
            payload["groups"] = groups
        resp = await self._send(client, "POST", self._docclean_url("api_text"), json=payload,
                                headers={**self._headers(), "Accept": "application/json"})
        data = self._docclean_ok(resp)
        if not isinstance(data.get("text"), str):
            raise AnonymizerError("missing_field")
        verification = data.get("verification") if isinstance(data.get("verification"), dict) else {}
        if verification.get("clean") is not True:
            raise AnonymizerError("not_clean", f"leaked={verification.get('leaked_values_count', -1)}")
        if chunk.strip() and not data["text"].strip():
            raise AnonymizerError("empty_result")
        report = data.get("report") if isinstance(data.get("report"), dict) else {}
        return data["text"], int(report.get("total_replaced", 0) or 0)

    def _docclean_ok(self, resp: httpx.Response) -> dict:
        data = self._json(resp)
        if not resp.is_success:
            code = data.get("code") if isinstance(data, dict) else None
            if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{2,60}", code):
                raise AnonymizerError("api_" + code.lower())
            raise AnonymizerError(f"http_{resp.status_code}")
        if not isinstance(data, dict):
            raise AnonymizerError("invalid_json")
        if data.get("ok") is not True:
            raise AnonymizerError("status_not_ok")
        return data

    async def _generic_chunk(self, client: httpx.AsyncClient, chunk: str) -> str:
        c = self._c
        payload: dict = {}
        if c.extra_body.strip():
            payload = json.loads(c.extra_body)
        _set_path(payload, c.request_field, chunk)
        url = c.base_url.rstrip("/") + "/" + c.endpoint.lstrip("/")
        resp = await self._send(client, "POST", url, json=payload, headers={**self._headers(), "Accept": "application/json"})
        if not resp.is_success:
            raise AnonymizerError(f"http_{resp.status_code}")
        data = self._json(resp)
        if not isinstance(data, dict):
            raise AnonymizerError("invalid_json")
        if c.status_field:
            status = _get_path(data, c.status_field)
            shown = str(status).lower() if isinstance(status, bool) else str(status)  # JSON true → "true"
            if not isinstance(status, (str, int, float, bool)) or \
                    (c.status_ok_value and shown != c.status_ok_value) or (not c.status_ok_value and not status):
                raise AnonymizerError("status_not_ok")
        value = _get_path(data, c.response_field)
        if not isinstance(value, str):
            raise AnonymizerError("missing_field")
        if chunk.strip() and not value.strip():
            raise AnonymizerError("empty_result")
        return value

    async def test(self) -> tuple[bool, str, int]:
        """Проверка из админки: синтетическая строка без реальных данных проходит полный путь."""
        started = time.monotonic()
        try:
            if self._c.profile == "docclean":
                async with self._client(self._c.timeout) as client:
                    resp = await self._send(client, "GET", self._docclean_url("openapi"), headers={"Accept": "application/json"})
                spec = self._json(resp)
                if not resp.is_success or not isinstance(spec, dict) or "paths" not in spec or \
                        "/index.php?action=api_text" not in spec.get("paths", {}):
                    raise AnonymizerError("not_docclean")
            res = await self.anonymize("Проверка связи: Иванов Иван Иванович, тел. +7 900 000-00-00.", "check")
            msg = f"Сервис отвечает, результат обезличивания получен ({res.chunks} фр.)."
            ok = True
        except AnonymizerError as exc:
            msg, ok = exc.describe(), False
        return ok, msg, int((time.monotonic() - started) * 1000)
