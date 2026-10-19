"""Устройство пользователя: подпись для списка «Активные устройства», вид (телефон/планшет/компьютер) и метка подключения к встрече.

Сессия входа и участие во встрече — разные вещи: у одной сессии может быть несколько вкладок/устройств во встрече (метка = хэш сессии и идентификатора вкладки), а у одного
пользователя — несколько сессий (до `max_sessions`). Метка устойчива: то же устройство после обрыва сети получает тот же идентификатор участника и не создаёт лишних подключений.
"""
from __future__ import annotations

import hashlib
import re

from ..services.journal import parse_client

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def device_kind(ua: str | None) -> str:
    u = (ua or "")[:400]
    if re.search(r"iPad|Tablet|Android(?!.*Mobile)", u):
        return "планшет"
    if re.search(r"iPhone|iPod|Android.*Mobile|Mobi", u):
        return "телефон"
    return "компьютер"


def device_label(ua: str | None) -> str:
    """«Chrome 130 · Windows 10/11 (компьютер)» — для списка устройств."""
    base = parse_client(ua) or "неизвестное устройство"
    return f"{base} ({device_kind(ua)})"


def device_tag(session_id: str, client_id: str | None) -> str:
    """Метка устройства/вкладки (8 hex) по идентификатору вкладки, который клиент присылает в заголовке X-Client-Id и хранит в sessionStorage: та же вкладка (в том числе после обрыва сети и даже
    после повторного входа) получает тот же identity, другая вкладка или устройство — другой. Без заголовка (API-клиенты) метки нет: поведение прежнее — один identity на пользователя."""
    if not client_id or not _CLIENT_ID_RE.match(client_id):
        return ""
    return hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:8]
