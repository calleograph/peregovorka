"""Сцена ведущего (Spotlight): что руководитель комнаты показывает крупно ВСЕМ участникам встречи.

Хранится в Redis на время встречи (сбрасывается при её завершении) и рассылается событием `stage_changed`. Элементы задаются УСТОЙЧИВЫМИ ключами —
тип и участник (`camera` / `screen` + identity, `board`), а не идентификаторами дорожек LiveKit: после переподключения участника дорожка получает
новый SID, а сцена остаётся верной. Личная сцена каждого зрителя живёт только в его браузере; сервер её не знает и не меняет.

Права проверяются здесь (вызывающий маршрут — только руководитель/администратор), а не скрытием кнопки в интерфейсе. Каждый участник,
названный в сцене, должен быть участником ЭТОЙ встречи (защита от подстановки чужих идентичностей).
"""
from __future__ import annotations

import json
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meeting
from . import events

STAGE_MAX = 4                       # больше четырёх крупных элементов «для всех» на обычном экране не читается
TYPES = ("camera", "screen", "board")
STAGE_TTL = 3 * 24 * 3600


def key_of(item: dict) -> str:
    return "board" if item["type"] == "board" else f"{item['type']}:{item['identity']}"


class StageError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class StageService:
    def __init__(self, redis, meetings_svc):
        self._r, self._m = redis, meetings_svc

    @staticmethod
    def _key(meeting_id: uuid.UUID | str) -> str:
        return f"stage:{meeting_id}"

    async def get(self, meeting_id: uuid.UUID) -> dict:
        raw = await self._r.get(self._key(meeting_id))
        if not raw:
            return {"items": [], "by": None, "at": None}
        try:
            data = json.loads(raw)
            return {"items": list(data.get("items") or []), "by": data.get("by"), "at": data.get("at")}
        except (ValueError, TypeError):
            return {"items": [], "by": None, "at": None}

    async def normalize(self, db: AsyncSession, meeting: Meeting, raw: object) -> list[dict]:
        """Проверка и приведение элементов: тип из белого списка, участник этой встречи, без повторов, не больше STAGE_MAX."""
        if not isinstance(raw, list):
            raise StageError("bad_items", "items: список элементов сцены")
        out: list[dict] = []
        seen: set[str] = set()
        for it in raw:
            if not isinstance(it, dict) or it.get("type") not in TYPES:
                raise StageError("bad_item", "Элемент сцены: type — camera, screen или board")
            if it["type"] == "board":
                item = {"type": "board"}
            else:
                identity = it.get("identity")
                if not isinstance(identity, str) or not identity or len(identity) > 120:
                    raise StageError("bad_item", "Элемент сцены: identity участника")
                try:
                    await self._m._member_check(db, meeting, identity)            # noqa: SLF001 — участник ЭТОЙ встречи (не чужой identity)
                except Exception as exc:                                           # JoinError: не участник
                    raise StageError("not_member", "На сцену можно поставить только участника этой встречи", 404) from exc
                item = {"type": it["type"], "identity": identity}
            k = key_of(item)
            if k in seen:
                continue
            seen.add(k)
            out.append(item)
        if len(out) > STAGE_MAX:
            raise StageError("too_many", f"На общей сцене — не больше {STAGE_MAX} элементов")
        return out

    async def set(self, db: AsyncSession, meeting: Meeting, raw: object, *, by: str) -> dict:
        if meeting.ended_at is not None:
            raise StageError("meeting_ended", "Встреча уже завершена", 409)
        items = await self.normalize(db, meeting, raw)
        state = {"items": items, "by": by if items else None, "at": time.time()}
        if items:
            await self._r.set(self._key(meeting.id), json.dumps(state, ensure_ascii=False), ex=STAGE_TTL)
        else:
            await self._r.delete(self._key(meeting.id))
        await events.publish(self._r, meeting.id, {"type": "stage_changed", **state})
        return state

    async def forget(self, meeting_id: uuid.UUID, identity: str) -> None:
        """Участник ушёл: его камера и экран больше не могут быть на общей сцене."""
        st = await self.get(meeting_id)
        left = [i for i in st["items"] if i.get("identity") != identity]
        if len(left) != len(st["items"]):
            st["items"] = left
            if left:
                await self._r.set(self._key(meeting_id), json.dumps(st, ensure_ascii=False), ex=STAGE_TTL)
            else:
                await self._r.delete(self._key(meeting_id))
            await events.publish(self._r, meeting_id, {"type": "stage_changed", **st})
