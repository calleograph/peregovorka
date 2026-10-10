"""Короткоживущие ссылки на скачивание аудиозаписей.

Ключ API не передаётся тому, кто скачивает (например, плееру или стороннему сервису): интеграция запрашивает ссылку (`recordings:download`), получает подписанный адрес
на несколько минут и отдаёт его дальше. Ссылка:
* подписана HMAC (ключ выводится из мастер-ключа приложения), содержит интеграцию, запись и срок; подделка и продление срока невозможны;
* действует `download_url_ttl_s` секунд (по умолчанию 300); после истечения — 410;
* при использовании заново проверяется, что интеграция включена, право `recordings:download` и область комнат прежние (отзыв ключа/права действует сразу);
* адрес не содержит ключа API и в журнале обращений записывается шаблоном пути (без самого токена).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _key(master: str, internal: str) -> bytes:
    return hashlib.sha256(b"pubapi-download|" + (master or internal or "").encode()).digest()


def issue(master: str, internal: str, *, client_id: str, recording_id: str, ttl_s: int, now: float | None = None) -> tuple[str, int]:
    exp = int((time.time() if now is None else now) + ttl_s)
    payload = base64.urlsafe_b64encode(json.dumps({"c": client_id, "r": recording_id, "e": exp}, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_key(master, internal), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}", exp


def verify(master: str, internal: str, token: str, *, now: float | None = None) -> tuple[dict | None, str]:
    """(данные, причина). Причина: ok | bad | expired. Подпись проверяется ДО чтения содержимого."""
    try:
        payload, sig = token.rsplit(".", 1)
    except ValueError:
        return None, "bad"
    good = hmac.new(_key(master, internal), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return None, "bad"
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = int(data["e"])
    except (ValueError, KeyError, TypeError):
        return None, "bad"
    if (time.time() if now is None else now) > exp:
        return None, "expired"
    return data, "ok"
