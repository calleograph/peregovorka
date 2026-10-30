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


LEGACY = "legacy"          # владелец основного подключения — клиент без идентификатора (старые приложения, сторонние API-клиенты)


def device_tag(session_id: str, client_id: str | None) -> str:
    """Метка подключения к встрече (8 hex) по идентификатору клиента `X-Client-Id`; пусто — клиент идентификатора не прислал.

    Разделены четыре идентификатора: пользователь (`u-<hex>`), устройство/экземпляр клиента (`X-Client-Id`: вкладка браузера — sessionStorage, приложение для Android и клиент
    Windows — своё значение), подключение (метка) и участник LiveKit (`u-<hex>` — основное подключение, `u-<hex>~<метка>` — остальные). Та же вкладка/установка после обрыва и
    повторного входа получает того же участника, другая — другого. См. `session_tag` — запасная метка для клиента без идентификатора."""
    if client_id and _CLIENT_ID_RE.match(client_id):
        return hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:8]
    return ""


def session_tag(session_id: str) -> str:
    """Запасная метка для клиента без идентификатора, когда основное подключение пользователя уже занято другим клиентом: по сеансу входа. Раньше такой клиент (приложение
    для Android до 0.1.3) получал ТОТ ЖЕ идентификатор участника, что и вкладка браузера, — LiveKit закрывал прежнее подключение (DUPLICATE_IDENTITY)."""
    return hashlib.sha256(b"session:" + (session_id or "").encode("utf-8")).hexdigest()[:8] if session_id else ""
