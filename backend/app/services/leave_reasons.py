"""Причина отключения участника от встречи — для «Журнала событий» (категория room, событие participant_left).

Источники по старшинству: метка сервера, поставленная перед его собственным действием (вышел кнопкой «Выйти», удалил руководитель, отозваны права);
завершение встречи (в том числе автозавершение забытой встречи по бездействию); причина, которую сообщает сервер звонков (DisconnectReason LiveKit).
Молчание участника само по себе причиной отключения не бывает: отключение по бездействию — только завершение забытой встречи, где человек остался один.
"""
from __future__ import annotations

import uuid

LABELS = {
    "left": "вышел сам (кнопка «Выйти»)",
    "closed": "закрыл страницу или приложение",
    "removed": "удалён руководителем комнаты",
    "rights": "отключён сервером: права на публикацию отозваны",
    "idle": "встреча завершена автоматически: участник был в комнате один и ничего не делал",
    "meeting_ended": "встреча завершена",
    "connection_lost": "потеря соединения",
    "replaced": "вошёл заново с этого же подключения",
    "server": "перезапуск или переезд сервера звонков",
    "other": "другая причина",
}
END_LABELS = {"manual": "кнопкой «Завершить для всех»", "admin": "администратором", "api": "через внешний API", "empty": "все участники вышли",
              "temp_ttl": "истёк предельный срок временной комнаты"}

# livekit.protocol.models.DisconnectReason (числа — из протокола; сам модуль тут не нужен)
_LK = {1: "closed", 2: "replaced", 3: "server", 4: "removed", 5: "meeting_ended", 6: "connection_lost", 7: "connection_lost", 8: "server",
       9: "connection_lost", 10: "meeting_ended", 14: "connection_lost", 15: "connection_lost"}
_LK_NAMES = {0: "UNKNOWN_REASON", 1: "CLIENT_INITIATED", 2: "DUPLICATE_IDENTITY", 3: "SERVER_SHUTDOWN", 4: "PARTICIPANT_REMOVED", 5: "ROOM_DELETED",
             6: "STATE_MISMATCH", 7: "JOIN_FAILURE", 8: "MIGRATION", 9: "SIGNAL_CLOSE", 10: "ROOM_CLOSED", 11: "USER_UNAVAILABLE", 12: "USER_REJECTED",
             13: "SIP_TRUNK_FAILURE", 14: "CONNECTION_TIMEOUT", 15: "MEDIA_FAILURE", 16: "AGENT_ERROR"}
MARK_TTL = 300


def _key(meeting_id: uuid.UUID | str, identity: str) -> str:
    return f"leftwhy:{meeting_id}:{identity}"


async def mark(redis, meeting_id: uuid.UUID | str, identity: str, why: str) -> None:
    """Сервер сам отключает участника (или участник нажал «Выйти»): причина известна до того, как сервер звонков пришлёт событие ухода."""
    await redis.set(_key(meeting_id, identity), why, ex=MARK_TTL)


async def mark_idle(redis, meeting_id: uuid.UUID | str, quiet_s: int | None) -> None:
    await redis.set(f"idlequiet:{meeting_id}", str(int(quiet_s or 0)), ex=MARK_TTL)


async def resolve(redis, meeting_id: uuid.UUID | str, identity: str, lk_reason: int, end_reason: str | None) -> tuple[str, dict]:
    """Код причины и подробности для журнала (lk — причина сервера звонков, end — причина завершения встречи, quiet_s — сколько секунд без действий)."""
    data: dict = {"lk": _LK_NAMES.get(lk_reason, str(lk_reason))}
    marked = await redis.getdel(_key(meeting_id, identity))
    if end_reason:
        data["end"] = end_reason
    if marked:
        return (marked.decode() if isinstance(marked, bytes) else marked), data
    if end_reason == "idle":
        q = await redis.get(f"idlequiet:{meeting_id}")
        if q:
            data["quiet_s"] = int(q)
        return "idle", data
    if end_reason:
        return "meeting_ended", data
    return _LK.get(lk_reason, "other"), data


def message(name: str, code: str, data: dict, in_meeting_s: int | None) -> str:
    text = f"{name or 'Участник'}: {LABELS.get(code, LABELS['other'])}"
    if code == "idle" and data.get("quiet_s"):
        text += f", без действий {max(1, data['quiet_s'] // 60)} мин"
    if code == "meeting_ended" and data.get("end") in END_LABELS:
        text += f" ({END_LABELS[data['end']]})"
    if in_meeting_s is not None and in_meeting_s >= 0:
        m, s = divmod(in_meeting_s, 60)
        text += f"; во встрече {m} мин {s} с" if m else f"; во встрече {s} с"
    return text
