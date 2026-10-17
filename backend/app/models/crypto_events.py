"""Прозрачное шифрование чата и стенограмм на уровне ORM (см. security/storage_crypto.py).

Запись: перед вставкой/обновлением, если у комнаты встречи включено `encrypt_storage`, текст заменяется шифртекстом, а после записи в объект возвращается
исходный (приложение дальше работает с обычным текстом). Чтение: при загрузке строки шифртекст расшифровывается и кладётся в объект как «неизменённое» значение,
поэтому обновление других полей не перезапишет текст. Если расшифровать не удалось (нет ключа, подмена), вместо текста показывается пометка — страница не падает.
Решение «шифровать ли» принимается по комнате встречи и кэшируется на 30 секунд (`invalidate`).
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import event, select
from sqlalchemy.orm import Session, attributes

from ..security.storage_crypto import StorageCryptoError, chat_aad, is_sealed, keyring, segment_aad
from .entities import Meeting, MeetingChatMessage, Room, TranscriptSegment

log = logging.getLogger("app.crypto")
UNREADABLE = "🔒 [запись зашифрована, ключ недоступен]"
_TTL_S = 30.0
_cache: dict[str, tuple[bool, float]] = {}


def invalidate() -> None:
    _cache.clear()


def _wants(connection, meeting_id) -> bool:
    key = str(meeting_id)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[1] < _TTL_S:
        return hit[0]
    row = connection.execute(select(Room.encrypt_storage).join(Meeting, Meeting.room_id == Room.id).where(Meeting.id == meeting_id)).first()
    val = bool(row[0]) if row else False
    _cache[key] = (val, now)
    return val


def _aad(target) -> bytes:
    return chat_aad(target.meeting_id) if isinstance(target, MeetingChatMessage) else segment_aad(target.meeting_id, target.segment_uid)


def _seal(mapper, connection, target):
    text = target.__dict__.get("text")
    if not text or is_sealed(text) or not _wants(connection, target.meeting_id):
        return
    target.__dict__["_pg_plain"] = text
    target.text = keyring.seal(text, _aad(target))           # без ключа StorageCryptoError: незашифрованное в зашифрованную комнату не пишем


def _restore(mapper, connection, target):
    plain = target.__dict__.pop("_pg_plain", None)
    if plain is not None:
        attributes.set_committed_value(target, "text", plain)


def _open(target):
    v = target.__dict__.get("text")
    if not is_sealed(v):
        return
    try:
        attributes.set_committed_value(target, "text", keyring.open_(v, _aad(target)))
    except StorageCryptoError as exc:
        log.error("Не удалось расшифровать запись", extra={"table": target.__tablename__, "error": str(exc)})
        attributes.set_committed_value(target, "text", UNREADABLE)


def _on_load(target, context):
    _open(target)


def _on_refresh(target, context, attrs):
    if attrs is None or "text" in attrs:
        _open(target)


for _model in (MeetingChatMessage, TranscriptSegment):
    event.listen(_model, "before_insert", _seal)
    event.listen(_model, "after_insert", _restore)
    event.listen(_model, "before_update", _seal)
    event.listen(_model, "after_update", _restore)
    event.listen(_model, "load", _on_load)
    event.listen(_model, "refresh", _on_refresh)


# Режим комнаты изменили: кэш решений сбрасывается ПОСЛЕ фиксации транзакции (до неё другой запрос мог бы снова закэшировать старое значение)
def _room_changed(mapper, connection, target):
    sess = attributes.instance_state(target).session
    if sess is not None and attributes.get_history(target, "encrypt_storage").has_changes():
        sess.info["crypto_dirty"] = True


def _after_commit(session):
    if session.info.pop("crypto_dirty", False):
        invalidate()


event.listen(Room, "after_update", _room_changed)
event.listen(Room, "after_insert", _room_changed)
event.listen(Session, "after_commit", _after_commit)
