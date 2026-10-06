"""Приём реплик от ASR: идентичность трека → пользователь, идемпотентная запись, живое событие."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meeting, MeetingParticipant, TranscriptSegment, User
from . import events
from .livekit import SERVICE_IDENTITY_PREFIX, parse_meeting_room_name, parse_user_identity

log = logging.getLogger("app.segments")


class IngestResult(str, Enum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


def segment_to_dict(seg: TranscriptSegment, display_name: str | None = None) -> dict:
    name = display_name if display_name is not None else (seg.user.display_name if seg.user else None)
    return {
        "id": seg.id,
        "uid": str(seg.segment_uid),
        "meeting_id": str(seg.meeting_id),
        "user_id": str(seg.user_id) if seg.user_id else None,
        "display_name": name or "Неизвестный участник",
        "identity": seg.participant_identity,
        "started_at": seg.started_at.isoformat(),
        "ended_at": seg.ended_at.isoformat(),
        "text": seg.text,
        "language": seg.language,
    }


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _safe_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _parse_meeting_id(value: str) -> uuid.UUID | None:
    return parse_meeting_room_name(value) if value.startswith("m-") else _safe_uuid(value)


async def ingest_segment(db: AsyncSession, redis: Redis, fields: dict[str, str]) -> IngestResult:
    try:
        uid = uuid.UUID(fields["segment_uid"])
        meeting_id = _parse_meeting_id(fields["meeting_id"])
        identity = fields["identity"]
        started, ended = _parse_dt(fields["started_at"]), _parse_dt(fields["ended_at"])
        text = (fields.get("text") or "").strip()
    except (KeyError, ValueError) as exc:
        log.warning("Некорректное сообщение сегмента", extra={"error": type(exc).__name__})
        return IngestResult.REJECTED
    if meeting_id is None or not text or identity.startswith(SERVICE_IDENTITY_PREFIX) or ended < started:
        return IngestResult.REJECTED

    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        log.warning("Сегмент для неизвестной встречи", extra={"meeting_id": str(meeting_id)})
        return IngestResult.REJECTED

    if (await db.execute(select(TranscriptSegment.id).where(TranscriptSegment.segment_uid == uid))).first():
        return IngestResult.DUPLICATE

    # Пользователь определяется ТОЛЬКО по identity трека и только если он был участником этой встречи.
    user_id = parse_user_identity(identity)
    user: User | None = None
    if user_id is not None:
        was_member = (await db.execute(select(MeetingParticipant.id).where(
            MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == user_id))).first()
        if was_member:
            user = await db.get(User, user_id)
        else:
            log.warning("Identity не из состава встречи", extra={"meeting_id": str(meeting_id)})

    def _json(name: str):
        try:
            return json.loads(fields[name]) if fields.get(name) else None
        except ValueError:
            return None

    metrics = {k: fields[k] for k in ("duration_ms", "infer_ms", "queue_ms") if k in fields}
    seg = TranscriptSegment(
        segment_uid=uid, meeting_id=meeting_id, room_id=meeting.room_id, user_id=user.id if user else None,
        participant_identity=identity[:80], started_at=started, ended_at=ended, text=text,
        language=(fields.get("language") or None), model=_json("model"), metrics=metrics or None,
    )
    db.add(seg)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return IngestResult.DUPLICATE
    await db.refresh(seg)
    await events.publish(redis, meeting_id, {"type": "segment", "segment": segment_to_dict(seg, user.display_name if user else None)})
    return IngestResult.STORED
