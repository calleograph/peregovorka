"""Руководство встречей: выключить микрофон у всех участников или у одного. Доступно администратору сервера и руководителям комнаты."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import SessionUser, client_ip, get_db, require_user
from ..auth.guests import Actor, require_actor
from ..models import Meeting
from ..services import roles
from ..services.access import can_access_meeting, can_access_meeting_actor
from ..services.audit import write_audit
from ..services.meetings import JoinError
from ..services.livekit import mute_microphones, parse_guest_identity, parse_user_identity, user_identity
from ..services.rooms import is_moderator

router = APIRouter(prefix="/meetings", tags=["moderation"])


async def _meeting_for_moderator(request: Request, db: AsyncSession, meeting_id: uuid.UUID, su: SessionUser) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or meeting.ended_at is not None:
        raise HTTPException(status_code=404, detail="Встреча не найдена или уже завершена")
    if not is_moderator(meeting.room, su):
        raise HTTPException(status_code=403, detail="Выключать микрофоны могут администраторы и руководители этой комнаты")
    return meeting


@router.post("/{meeting_id}/moderation/mute-all")
async def mute_all(meeting_id: uuid.UUID, request: Request, su: SessionUser = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Выключить микрофоны у всех участников, кроме самого руководителя."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    done = await mute_microphones(request.app.state.settings, meeting.livekit_room, exclude={user_identity(su.user_id)})
    if done is None:
        raise HTTPException(status_code=503, detail="Сервер звонков недоступен — микрофоны не выключены")
    request.app.state.journal.emit("room", "moderator_mute_all", user=su.sam_account_name, room=meeting.room.name, meeting_id=str(meeting.id),
                                   ip=client_ip(request), message=f"выключено микрофонов: {len(done)}", data={"muted": len(done)})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.mute_all", target_type="meeting",
                      target_id=str(meeting.id), ip=client_ip(request), details={"muted": len(done), "room": meeting.room.slug})
    await db.commit()
    return {"muted": len(done)}


@router.post("/{meeting_id}/moderation/mute")
async def mute_one(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                   db: AsyncSession = Depends(get_db)):
    """Выключить микрофон у одного участника (identity из списка участников комнаты)."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    identity = body.get("identity")
    uid = (parse_user_identity(identity) or parse_guest_identity(identity)) if isinstance(identity, str) else None
    if uid is None:
        raise HTTPException(status_code=422, detail="identity: идентификатор участника")
    done = await mute_microphones(request.app.state.settings, meeting.livekit_room, only={identity})
    if done is None:
        raise HTTPException(status_code=503, detail="Сервер звонков недоступен — микрофон не выключен")
    request.app.state.journal.emit("room", "moderator_mute", user=su.sam_account_name, room=meeting.room.name, meeting_id=str(meeting.id),
                                   ip=client_ip(request), message=f"участник {identity[:12]}: {'выключен' if done else 'микрофон уже был выключен'}",
                                   data={"target": identity, "muted": bool(done)})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.mute", target_type="meeting",
                      target_id=str(meeting.id), ip=client_ip(request), details={"target": identity, "muted": bool(done), "room": meeting.room.slug})
    await db.commit()
    return {"muted": len(done)}


def _identity_of(body: dict[str, Any]) -> str:
    identity = body.get("identity")
    if not isinstance(identity, str) or not (parse_user_identity(identity) or parse_guest_identity(identity)):
        raise HTTPException(status_code=422, detail="identity: идентификатор участника")
    return identity


def _join_error(exc: JoinError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.get("/{meeting_id}/floor")
async def floor_state(meeting_id: uuid.UUID, request: Request, actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    """Кому сейчас дано слово и кто руководитель (для пометок в списке участников). Видят все участники встречи."""
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or not await can_access_meeting_actor(db, request.app.state.redis, meeting, actor):
        raise HTTPException(status_code=404, detail="Встреча не найдена или доступ закрыт")
    svc = request.app.state.meetings
    r = request.app.state.redis
    return {"presentation": meeting.room.room_type == "presentation", "floor": await svc.floor_list(meeting_id),
            "leaders": sorted(await r.smembers(svc._priv_key(meeting_id)))}  # noqa: SLF001


@router.post("/{meeting_id}/moderation/floor")
async def give_or_take_floor(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                             db: AsyncSession = Depends(get_db)):
    """«Дать слово» / «Забрать слово» участнику презентационной комнаты. Право временное: действует до конца встречи."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    identity = _identity_of(body)
    granted = body.get("granted")
    if not isinstance(granted, bool):
        raise HTTPException(status_code=422, detail="granted: true|false")
    try:
        state = await request.app.state.meetings.set_floor(db, meeting, identity, granted, by=su.display_name)
    except JoinError as exc:
        raise _join_error(exc) from None
    request.app.state.journal.emit("room", "floor_given" if state else "floor_taken", user=su.sam_account_name, room=meeting.room.name,
                                   meeting_id=str(meeting.id), ip=client_ip(request), data={"target": identity})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.floor", target_type="meeting", target_id=str(meeting.id),
                      ip=client_ip(request), details={"target": identity, "granted": state, "room": meeting.room.slug})
    await db.commit()
    return {"identity": identity, "granted": state}


@router.post("/{meeting_id}/moderation/kick")
async def kick(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
               db: AsyncSession = Depends(get_db)):
    """Удалить участника из идущей встречи."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    identity = _identity_of(body)
    try:
        kind = await request.app.state.meetings.kick_participant(db, meeting, identity)
    except JoinError as exc:
        raise _join_error(exc) from None
    request.app.state.journal.emit("room", "participant_removed", level="warn", user=su.sam_account_name, room=meeting.room.name,
                                   meeting_id=str(meeting.id), ip=client_ip(request), data={"target": identity, "kind": kind})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.kick", target_type="meeting", target_id=str(meeting.id),
                      ip=client_ip(request), details={"target": identity, "kind": kind, "room": meeting.room.slug})
    await db.commit()
    return {"identity": identity, "removed": True}


@router.post("/{meeting_id}/transcription")
async def toggle_transcription(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                               db: AsyncSession = Depends(get_db)):
    """«Остановить / возобновить транскрибацию»: звонок и запись аудио не затрагиваются. Руководитель комнаты (или любой участник в комнате без руководителей)."""
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or meeting.ended_at is not None:
        raise HTTPException(status_code=404, detail="Встреча не найдена или уже завершена")
    if not roles.can_control_meeting(meeting.room, su) or not await can_access_meeting(db, request.app.state.redis, meeting, su):
        raise HTTPException(status_code=403, detail="Транскрибацией управляют руководители комнаты")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="Ожидается enabled: true|false")
    try:
        state = await request.app.state.meetings.set_transcription(db, meeting, enabled)
    except JoinError as exc:
        raise _join_error(exc) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.transcription", target_type="meeting",
                      target_id=str(meeting.id), ip=client_ip(request), details={"enabled": state, "room": meeting.room.slug})
    await db.commit()
    return {"enabled": state}


# ------------------------------------------------------------------------------------------ показ экрана и камера (модерация)
@router.post("/{meeting_id}/moderation/stop-share")
async def stop_share(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                     db: AsyncSession = Depends(get_db)):
    """Остановить показ экрана одного участника (руководитель). `block: true` — и запретить повторный показ до конца встречи.
    Показы других участников продолжаются."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    identity = _identity_of(body)
    block = body.get("block", False)
    if not isinstance(block, bool):
        raise HTTPException(status_code=422, detail="block: true|false")
    try:
        res = await request.app.state.meetings.stop_share(db, meeting, identity, block=block, by=su.display_name)
    except JoinError as exc:
        raise _join_error(exc) from None
    request.app.state.journal.emit("room", "moderator_stop_share", user=su.sam_account_name, room=meeting.room.name, meeting_id=str(meeting.id), ip=client_ip(request),
                                   data={"target": identity, "blocked": block, "was_sharing": res["stopped"]})
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.stop_share", target_type="meeting", target_id=str(meeting.id),
                      ip=client_ip(request), details={"target": identity, "blocked": block, "room": meeting.room.slug})
    await db.commit()
    return res


@router.post("/{meeting_id}/moderation/allow-share")
async def allow_share(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                      db: AsyncSession = Depends(get_db)):
    """Снять запрет показа экрана, наложенный в этой встрече."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    identity = _identity_of(body)
    try:
        res = await request.app.state.meetings.allow_share(db, meeting, identity, by=su.display_name)
    except JoinError as exc:
        raise _join_error(exc) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.allow_share", target_type="meeting", target_id=str(meeting.id),
                      ip=client_ip(request), details={"target": identity, "room": meeting.room.slug})
    await db.commit()
    return res


@router.post("/{meeting_id}/moderation/stop-camera")
async def stop_camera(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                      db: AsyncSession = Depends(get_db)):
    """Выключить камеру участника (руководитель). Участник может включить её снова."""
    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    identity = _identity_of(body)
    try:
        res = await request.app.state.meetings.stop_camera(db, meeting, identity, by=su.display_name)
    except JoinError as exc:
        raise _join_error(exc) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.stop_camera", target_type="meeting", target_id=str(meeting.id),
                      ip=client_ip(request), details={"target": identity, "room": meeting.room.slug})
    await db.commit()
    return res


# ------------------------------------------------------------------------------------------ сцена ведущего (для всех)
@router.get("/{meeting_id}/stage")
async def get_stage(meeting_id: uuid.UUID, request: Request, actor: Actor = Depends(require_actor), db: AsyncSession = Depends(get_db)):
    """Что ведущий показывает всем (видят все участники встречи, в том числе гости): после входа и переподключения."""
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or not await can_access_meeting_actor(db, request.app.state.redis, meeting, actor):
        raise HTTPException(status_code=404, detail="Встреча не найдена или доступ закрыт")
    return await request.app.state.stage.get(meeting_id)


@router.put("/{meeting_id}/stage")
async def set_stage(meeting_id: uuid.UUID, request: Request, body: dict[str, Any] = Body(...), su: SessionUser = Depends(require_user),
                    db: AsyncSession = Depends(get_db)):
    """Задать общую сцену (руководитель/администратор): до четырёх элементов {type: camera|screen|board, identity}. Пустой список — очистить."""
    from ..services.stage import StageError  # noqa: PLC0415

    meeting = await _meeting_for_moderator(request, db, meeting_id, su)
    try:
        state = await request.app.state.stage.set(db, meeting, body.get("items"), by=su.display_name)
    except StageError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None
    await write_audit(db, actor_user_id=su.user_id, actor_name=su.display_name, action="meeting.stage", target_type="meeting", target_id=str(meeting.id),
                      ip=client_ip(request), details={"items": state["items"], "room": meeting.room.slug})
    await db.commit()
    return state
