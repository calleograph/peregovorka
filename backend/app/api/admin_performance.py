"""Админка → «Производительность»: нагрузка хоста и контейнера, комнаты и зрители, звонковый сервер, распознавание речи, очереди фоновых задач,
показатели WebRTC от клиентов. Только чтение, один лёгкий запрос на обновление (страница опрашивает раз в 5 секунд, пока открыта);
показатель, который получить нельзя, возвращается как `null` — интерфейс показывает «Нет данных»."""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, get_db, require_admin
from ..models import ConversationMap, GuestParticipant, Meeting, MeetingParticipant, Protocol, Recording, Room, StorageTransfer
from ..services import diagnostics, perf

router = APIRouter(prefix="/admin", tags=["admin"])

TOP_ROOMS = 8


def _avg(values: list) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


@router.get("/performance")
async def performance(request: Request, su: SessionUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    app = request.app
    hub: perf.PerfHub = app.state.perf
    host = hub.host.sample()
    hb = await app.state.bridge.heartbeat()
    asr_queue = hb.get("queue_depth") if hb else None
    meetings_active = (await db.execute(select(func.count()).select_from(Meeting).where(Meeting.ended_at.is_(None)))).scalar_one()
    assessment = perf.assess(host, asr_queue=asr_queue, active_meetings=meetings_active)

    # комнаты: одним запросом — сколько людей в каждой идущей встрече (сотрудники + гости), без запроса на каждую встречу
    users = (select(MeetingParticipant.meeting_id.label("mid"), func.count(func.distinct(MeetingParticipant.user_id)).label("n"))
             .where(MeetingParticipant.left_at.is_(None)).group_by(MeetingParticipant.meeting_id).subquery())
    guests = (select(GuestParticipant.meeting_id.label("mid"), func.count().label("n")).where(GuestParticipant.left_at.is_(None))
              .group_by(GuestParticipant.meeting_id).subquery())
    rows = (await db.execute(
        select(Meeting.id, Room.name, Room.room_type, func.coalesce(users.c.n, 0) + func.coalesce(guests.c.n, 0))
        .join(Room, Room.id == Meeting.room_id).outerjoin(users, users.c.mid == Meeting.id).outerjoin(guests, guests.c.mid == Meeting.id)
        .where(Meeting.ended_at.is_(None)))).all()
    people = sum(int(r[3]) for r in rows)
    by_room = sorted(({"name": r[1], "type": r[2], "participants": int(r[3])} for r in rows), key=lambda x: -x["participants"])[:TOP_ROOMS]
    rooms = {"active_meetings": meetings_active, "participants": people, "presentations": sum(1 for r in rows if r[2] == "presentation"),
             "audience_in_presentations": sum(int(r[3]) for r in rows if r[2] == "presentation"), "top": by_room}

    lk = await hub.livekit()
    cm = [json.loads(x) for x in await app.state.redis.lrange("clientdiag:metrics", 0, 99)]
    fresh = [m for m in cm if isinstance(m.get("ts"), (int, float)) and time.time() - m["ts"] < 300]      # только свежие (последние 5 минут)
    webrtc = {"samples": len(fresh), "rtt_ms": _avg([m.get("rtt_ms") for m in fresh]), "packet_loss_pct": _avg([m.get("packet_loss_pct") for m in fresh]),
              "jitter_ms": _avg([m.get("jitter_ms") for m in fresh]), "bitrate_in_kbps": _avg([m.get("bitrate_in_kbps") for m in fresh]),
              "bitrate_out_kbps": _avg([m.get("bitrate_out_kbps") for m in fresh]),
              "note": "Средние по выборке клиентов (отчёты участников); при большой аудитории отчитывается небольшая доля зрителей."}

    keys = ("queue_depth", "active_meetings", "avg_infer_ms", "avg_queue_ms", "rtf", "dropped", "errors", "torch_threads", "torch_interop_threads",
            "recorder_queue", "recorder_dropped")
    asr = {"ok": bool(hb and hb.get("model_loaded")), **{k: (hb or {}).get(k) for k in keys}}

    queued_protocols = (await db.execute(select(func.count()).select_from(Protocol).where(Protocol.status == "pending"))).scalar_one()
    maps_running = (await db.execute(select(func.count()).select_from(ConversationMap).where(ConversationMap.status.in_(("pending", "running"))))).scalar_one()
    mixes = (await db.execute(select(func.count()).select_from(Recording).where(Recording.status == "processing"))).scalar_one()
    transfers = (await db.execute(select(func.count()).select_from(StorageTransfer).where(StorageTransfer.state.in_(("queued", "running"))))).scalar_one()
    jobs = {"gate": hub.gate.snapshot(), "protocols_pending": queued_protocols, "maps_pending": maps_running, "recordings_processing": mixes,
            "storage_transfers_active": transfers}

    return {
        "ts": time.time(), "assessment": assessment, "host": host.as_dict(), "rooms": rooms, "livekit": lk, "asr": asr, "jobs": jobs,
        "events": app.state.event_hub.snapshot(), "webrtc": webrtc, "kernel": diagnostics.kernel_report(),
        "containers": {"backend": host.as_dict()["container"],
                       "note": "Ресурсы других контейнеров без доступа к Docker из приложения не видны (приложению не выдаётся сокет Docker): смотрите «docker stats» на сервере."},
    }
