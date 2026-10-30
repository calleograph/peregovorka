"""Страница-рекордер (шаблон Egress): состояние общей сцены встречи по подписанному токену. Без сеанса и cookie; только чтение."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_db
from ..models import Meeting, MeetingWhiteboard, Room
from ..services import rec_scene, roles
from .collab import _tail_key

router = APIRouter(prefix="/rec", tags=["recorder"])


@router.get("/state")
async def recorder_state(request: Request, rt: str = Query(..., max_length=200), db: AsyncSession = Depends(get_db)):
    settings = request.app.state.settings
    mid = rec_scene.check_token(settings, rt)
    if mid is None:
        raise HTTPException(status_code=404, detail="Не найдено")            # не подсказываем, что именно не так
    meeting = await db.get(Meeting, mid)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    redis = request.app.state.redis
    ended = meeting.ended_at is not None
    row = await db.get(MeetingWhiteboard, mid)
    base = row.seq if row else 0
    patches: list[dict] = []
    if not ended:
        for raw in await redis.lrange(_tail_key(mid), 0, -1):
            p = json.loads(raw)
            if p.get("leaders_only") is None and p["seq"] > base:      # доска «только для руководителей» в запись не попадает
                patches.append(p)
        patches.sort(key=lambda p: p["seq"])
    room = await db.get(Room, meeting.room_id)
    private = room is not None and roles.board_level(room) == "private"
    stage = await request.app.state.stage.get(mid)
    if private:                                         # закрытая доска: ни схема, ни её активность в общую запись не идут
        stage = {**stage, "items": [i for i in stage.get("items", []) if i.get("type") != "board"]}
    payload = rec_scene.scene_payload(stage=stage, board_age=None if private else await rec_scene.board_age_s(redis, mid),
                                      board={"seq": base, "xml": None if private else (row.xml if row else None), "patches": [] if private else patches}, ended=ended)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})
