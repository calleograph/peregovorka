"""Диагностика Outlook: где завершается HTTPS, доступен ли API (с разбором причины) и что считать ошибкой, а что — особенностью маршрутизации.

Главное правило: ошибка обращения СЕРВЕРА к собственному публичному адресу (DNS, hairpin NAT, чужой CA) не равна недоступности надстройки для клиентов.
Достоверно клиентскую доступность показывает только проверка из браузера клиента (публичная страница /outlook и кнопка «Проверить с этого компьютера»).
"""
from __future__ import annotations

import socket
import ssl
from typing import Any, Mapping

import httpx

TOPOLOGY_LABELS = {
    "local": "HTTPS на самом сервере Peregovorka (сертификат установщика)",
    "proxy": "HTTPS на внешнем reverse proxy",
    "gateway": "HTTPS на WAF, балансировщике или другом внешнем шлюзе",
    "external": "HTTPS на внешнем узле (reverse proxy, WAF или балансировщик)",
    "unknown": "Требуется уточнение",
}


def detect_topology(headers: Mapping[str, str], tls_local_configured: bool, manual: str = "auto") -> dict:
    """Режим — по достоверным признакам установки и доверенным proxy-заголовкам, а не по исходу TLS-соединения (оно не говорит, где лежит сертификат)."""
    h = {k.lower(): v for k, v in headers.items()}
    manual = {"direct": "local"}.get(manual, manual)
    evidence: list[str] = []
    xff = [x for x in h.get("x-forwarded-for", "").split(",") if x.strip()]
    https_forwarded = h.get("x-forwarded-proto", "").lower() == "https"
    via = bool(h.get("via") or h.get("forwarded") or h.get("x-real-ip"))
    if tls_local_configured:
        evidence.append("в установке задан собственный TLS-сертификат (NGINX_TLS_CERT): HTTPS принимает nginx установщика")
    if https_forwarded:
        evidence.append("запрос пришёл с заголовком X-Forwarded-Proto: https (соединение до приложения — не HTTPS, схему сообщил узел перед ним)")
    if len(xff) >= 2 or via:
        evidence.append("в запросе есть цепочка proxy-заголовков (X-Forwarded-For / Via / Forwarded): перед приложением больше одного узла")
    if manual in ("local", "proxy", "gateway"):
        return {"mode": manual, "auto": False, "label": TOPOLOGY_LABELS[manual], "evidence": evidence, "needs_choice": False}
    if tls_local_configured and not (len(xff) >= 2 or via):
        mode = "local"
    elif https_forwarded or len(xff) >= 2 or via:
        mode = "external"
    else:
        mode = "unknown"
    return {"mode": mode, "auto": True, "label": TOPOLOGY_LABELS[mode], "evidence": evidence, "needs_choice": mode == "unknown",
            "hint": "Выберите вручную, где завершается HTTPS, если определение неоднозначно." if mode in ("unknown", "external") or (tls_local_configured and mode != "local") else ""}


def http_verdict(status: int) -> tuple[bool | None, str, str]:
    """(ok, код, пояснение) по коду ответа API надстройки."""
    if status == 200:
        return True, "ok", "API отвечает."
    if status == 401:
        return True, "auth", "Маршрут работает, требуется вход (ответ 401 без авторизации — это нормально)."
    if status == 403:
        return None, "forbidden", "Доступ ограничен политикой (403): проверьте правила WAF/прокси для /api/v1/outlook/."
    if status == 404:
        return False, "not_found", "Маршрут не найден (404): проверьте, что прокси передаёт путь /api/ приложению и интеграция включена."
    if status in (502, 503, 504):
        return False, "gateway", f"Ошибка прокси или сервиса ({status}): приложение за прокси не отвечает."
    return None, "other", f"Неожиданный ответ HTTP {status}."


def connect_failure(exc: Exception) -> tuple[str, str]:
    """(код, пояснение) для ошибки соединения — вместо общего «ConnectError»."""
    cause: BaseException | None = exc
    seen = 0
    while cause is not None and seen < 6:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return "tls_untrusted", f"Ошибка доверия TLS: {getattr(cause, "verify_message", None) or cause}. Сервер Peregovorka не доверяет сертификату своего публичного адреса (чаще всего — неполная цепочка на прокси или внутренний центр сертификации)."
        if isinstance(cause, socket.gaierror):
            return "dns", "Имя публичного адреса не разрешается из контейнера (DNS). Типично для split DNS: клиентам имя известно, контейнеру — нет."
        if isinstance(cause, ConnectionRefusedError):
            return "refused", "Соединение с публичным адресом отклонено из контейнера (порт закрыт или адрес ведёт не на тот узел)."
        cause = cause.__cause__ or cause.__context__
        seen += 1
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout)):
        return "timeout", "Нет ответа от публичного адреса из контейнера (таймаут): вероятна особенность маршрутизации (hairpin NAT, брандмауэр)."
    return "network", f"Сетевая ошибка при обращении к публичному адресу ({type(exc).__name__})."


async def probe_json(url: str, *, transport: Any = None, verify: Any = True, timeout: float = 8.0, headers: dict | None = None) -> dict:
    """Один запрос с понятным результатом: {ok, code, message, status?}."""
    try:
        async with httpx.AsyncClient(transport=transport, verify=verify, timeout=httpx.Timeout(timeout, connect=5.0), follow_redirects=False) as c:
            r = await c.get(url, headers=headers)
    except httpx.HTTPError as exc:
        code, text = connect_failure(exc)
        return {"ok": None, "code": code, "message": text}
    if 300 <= r.status_code < 400:
        return {"ok": False, "code": "redirect", "status": r.status_code, "message": f"Ответ-перенаправление HTTP {r.status_code} на {r.headers.get('location', '?')[:120]}: Exchange 2019 не следует перенаправлениям при загрузке манифеста."}
    ok, code, text = http_verdict(r.status_code)
    return {"ok": ok, "code": code, "status": r.status_code, "message": text}


# ------------------------------------------------------------------------------------------------ кэш сведений о публичном TLS
import asyncio  # noqa: E402
import time  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

from . import tls_chain, tls_check  # noqa: E402

TLS_TTL = 120.0
_lock = asyncio.Lock()


def public_tls_view(res: dict) -> dict:
    """Публичная часть результата (без служебных PEM): сведения о сертификате берутся из самой сети — любой клиент получает те же данные."""
    keys = ("host", "port", "state", "ok", "issuer", "subject", "not_after", "days_left", "chain", "chain_complete", "cause", "message", "fix")
    return {k: res.get(k) for k in keys if k in res}


async def tls_snapshot(app, base: str, *, fetch_aia: bool, force: bool = False, extra_ca: str = "") -> dict | None:
    """Результат проверки публичного TLS с кэшем на 2 минуты (публичная страница не должна становиться источником исходящих соединений по запросу посетителей)."""
    if not base.startswith("https://"):
        return None
    cache = getattr(app.state, "outlook_tls", None)
    if cache and cache["base"] == base and time.monotonic() - cache["at"] < TLS_TTL and not force and (cache["aia"] or not fetch_aia):
        return cache["res"]
    async with _lock:
        cache = getattr(app.state, "outlook_tls", None)
        if cache and cache["base"] == base and time.monotonic() - cache["at"] < TLS_TTL and not force and (cache["aia"] or not fetch_aia):
            return cache["res"]
        u = urlsplit(base)
        ctx = tls_chain.system_context(extra_ca or None)
        res = await asyncio.to_thread(tls_check.probe, u.hostname or "", u.port or 443, 6.0, ctx, fetch_aia)
        app.state.outlook_tls = {"base": base, "at": time.monotonic(), "res": res, "aia": fetch_aia}
        return res
