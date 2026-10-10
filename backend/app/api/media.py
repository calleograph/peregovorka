"""Записи встречи для просмотра в браузере: список материалов и потоковая отдача с поддержкой HTTP Range (перемотка без загрузки всего файла).

Основной материал — общая запись всей встречи (`mix_audio` / `mix_video`). Файлы отдельных участников — только администраторам (как и прежде). Доступ к встрече определяется теми же правилами,
что у стенограммы и протоколов (`get_meeting_for_user`); скачать исходный файл могут администратор, руководитель комнаты и организатор встречи. Физические пути и ссылки на SMB наружу не выдаются:
файл читается сервером и отдаётся потоком по диапазонам, целиком в память не загружается.
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..models import Recording
from ..services.audit import write_audit
from ..services.reconcile import mark_missing
from ..services.storage import StorageError, StorageNotFound
from .meetings import can_edit_protocol, get_meeting_for_user

router = APIRouter(prefix="/meetings", tags=["media"])
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
MIX_KINDS = ("mix_video", "mix_audio")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | str:
    """None — диапазон не задан (отдаём всё); 'bad' — недопустимый; иначе (начало, конец включительно). Один диапазон — этого достаточно плееру."""
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return "bad"
    a, b = m.groups()
    if a == "":                                     # «последние N байт»
        n = int(b)
        if n == 0:
            return "bad"
        return max(0, size - n), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size or end < start:
        return "bad"
    return start, min(end, size - 1)


def _item(r: Recording, can_download: bool) -> dict:
    return {"id": str(r.id), "kind": r.kind, "mime": r.mime, "status": r.status, "error": r.error, "size_bytes": r.size_bytes, "duration_s": r.duration_s, "has_video": r.has_video,
            "started_at": r.started_at, "created_at": r.created_at, "file_state": r.file_state, "can_download": can_download}


@router.get("/{meeting_id}/media")
async def list_media(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Записи встречи: общая запись (видео и звук) — всем, кто видит встречу; индивидуальные — только администратору."""
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    rows = (await db.execute(select(Recording).where(Recording.meeting_id == meeting_id).order_by(Recording.created_at))).scalars().all()
    dl = can_edit_protocol(meeting, su)
    mixes = sorted((r for r in rows if r.kind in MIX_KINDS), key=lambda r: MIX_KINDS.index(r.kind))
    out = {"mixes": [_item(r, dl) for r in mixes], "participants": [], "recording_mode": getattr(meeting.room, "recording_mode", "audio")}
    if su.is_admin:
        out["participants"] = [{**_item(r, True), "identity": r.participant_identity, "name": r.path.rsplit("/", 1)[-1]} for r in rows if r.kind == "participant"]
    return out


@router.get("/{meeting_id}/media/{recording_id}/stream")
async def stream_media(meeting_id: uuid.UUID, recording_id: uuid.UUID, request: Request, download: bool = False, su: SessionUser = Depends(require_user),
                       db: AsyncSession = Depends(get_db)):
    meeting = await get_meeting_for_user(request, db, meeting_id, su)
    rec = await db.get(Recording, recording_id)
    # чужая запись и запись другой встречи неотличимы от несуществующей (защита от подстановки идентификаторов)
    if rec is None or rec.meeting_id != meeting.id:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if rec.kind == "participant" and not su.is_admin:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if download and not can_edit_protocol(meeting, su):
        raise HTTPException(status_code=403, detail="Скачивать записи могут администратор, руководитель комнаты и организатор встречи")
    if rec.status == "processing":
        raise HTTPException(status_code=409, detail={"code": "processing", "message": "Запись ещё формируется. Обновите страницу через несколько минут."})
    if rec.status == "failed":
        raise HTTPException(status_code=409, detail={"code": "failed", "message": rec.error or "Запись не удалось сформировать."})
    if rec.file_state == "missing":
        raise HTTPException(status_code=410, detail={"code": "file_missing", "message": "Файл записи удалён из хранилища (обнаружено при сверке)."})
    try:
        size, reader = await request.app.state.protocols.media_source(db, rec)
    except StorageNotFound:
        await mark_missing(db, rec, request.app.state.journal, "запись встречи")
        raise HTTPException(status_code=410, detail={"code": "file_missing", "message": "Файл записи не найден в хранилище — вероятно, его удалили вне приложения."}) from None
    except StorageError:
        raise HTTPException(status_code=503, detail={"code": "storage_unavailable", "message": "Хранилище записей сейчас недоступно. Повторите позже."}) from None
    rng = parse_range(request.headers.get("range"), size)
    if rng == "bad":
        return StreamingResponse(iter(()), status_code=416, headers={"Content-Range": f"bytes */{size}"})
    start, end = rng if rng else (0, size - 1)
    mime = rec.mime or "application/octet-stream"
    name = ("Запись встречи" + (".mp4" if rec.has_video else ".m4a" if rec.kind == "mix_audio" else ".wav")) if rec.kind != "participant" else rec.path.rsplit("/", 1)[-1]
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
               "Content-Disposition": ("attachment" if download else "inline") + "; filename*=UTF-8''" + _q(name)}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    # событие журнала аудита — на начало воспроизведения и скачивание (а не на каждый запрос перемотки)
    if download or start == 0:
        await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="recording.download" if download else "recording.play",
                          target_type="recording", target_id=str(rec.id), ip=client_ip(request), details={"kind": rec.kind, "meeting_id": str(meeting.id)})
        await db.commit()
    ps = request.app.state.protocols
    ps.touch_read(rec.id)                                    # пока читают (и 2 минуты после), перенос между хранилищами не удаляет файл-источник

    async def tracked():
        ps.read_begin(rec.id)
        try:
            async for chunk in iterate_in_threadpool(reader(start, end)):
                yield chunk
        finally:
            ps.read_end(rec.id)

    body = tracked()
    return StreamingResponse(body, status_code=206 if rng else 200, media_type=mime, headers=headers)


def _q(s: str) -> str:
    from urllib.parse import quote  # noqa: PLC0415

    return quote(s, safe="")
