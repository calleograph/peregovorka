"""Выдача LiveKit-токенов и вспомогательные вызовы LiveKit.

Identity берётся ТОЛЬКО из внутреннего идентификатора пользователя (u-<uuid hex>),
никогда из пользовательского ввода. Токен короткоживущий и привязан к одной
LiveKit-комнате (= одна встреча).
"""
from __future__ import annotations

import datetime
import logging
import uuid

from livekit import api as lkapi

from ..config import Settings

log = logging.getLogger("app.livekit")

IDENTITY_PREFIX = "u-"
GUEST_IDENTITY_PREFIX = "g-"
SERVICE_IDENTITY_PREFIX = "asr-"


def user_identity(user_id: uuid.UUID) -> str:
    return f"{IDENTITY_PREFIX}{user_id.hex}"


def guest_identity(guest_id: uuid.UUID) -> str:
    return f"{GUEST_IDENTITY_PREFIX}{guest_id.hex}"


def parse_user_identity(identity: str) -> uuid.UUID | None:
    """u-<32 hex> → UUID пользователя; всё остальное (в т.ч. g-*, asr-*) → None."""
    if not identity.startswith(IDENTITY_PREFIX):
        return None
    try:
        return uuid.UUID(hex=identity[len(IDENTITY_PREFIX):])
    except ValueError:
        return None


def parse_guest_identity(identity: str) -> uuid.UUID | None:
    """g-<32 hex> → UUID гостя; всё остальное → None."""
    if not identity.startswith(GUEST_IDENTITY_PREFIX):
        return None
    try:
        return uuid.UUID(hex=identity[len(GUEST_IDENTITY_PREFIX):])
    except ValueError:
        return None


def meeting_room_name(meeting_id: uuid.UUID) -> str:
    return f"m-{meeting_id.hex}"


def parse_meeting_room_name(name: str) -> uuid.UUID | None:
    if not name.startswith("m-"):
        return None
    try:
        return uuid.UUID(hex=name[2:])
    except ValueError:
        return None


def publish_sources(*, camera: bool, screen_share: bool) -> list[str]:
    sources = ["microphone"]
    if camera:
        sources.append("camera")
    if screen_share:
        sources += ["screen_share", "screen_share_audio"]
    return sources


def issue_user_token(settings: Settings, *, user_id: uuid.UUID, display_name: str, livekit_room: str,
                     camera_allowed: bool, screen_share_allowed: bool) -> str:
    return _issue_token(settings, identity=user_identity(user_id), display_name=display_name, livekit_room=livekit_room,
                        camera_allowed=camera_allowed, screen_share_allowed=screen_share_allowed)


def issue_guest_token(settings: Settings, *, guest_id: uuid.UUID, display_name: str, livekit_room: str,
                      camera_allowed: bool) -> str:
    """Токен гостя: те же права публикации, но без демонстрации экрана (гость — минимальные права)."""
    return _issue_token(settings, identity=guest_identity(guest_id), display_name=f"{display_name} (гость)",
                        livekit_room=livekit_room, camera_allowed=camera_allowed, screen_share_allowed=False)


def _issue_token(settings: Settings, *, identity: str, display_name: str, livekit_room: str,
                 camera_allowed: bool, screen_share_allowed: bool) -> str:
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise RuntimeError("LIVEKIT_API_KEY/LIVEKIT_API_SECRET не заданы")
    grants = lkapi.VideoGrants(
        room_join=True,
        room=livekit_room,
        can_subscribe=True,
        can_publish=True,
        can_publish_data=False,
        can_publish_sources=publish_sources(camera=camera_allowed, screen_share=screen_share_allowed),
        can_update_own_metadata=False,
        hidden=False,
    )
    return (
        lkapi.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(display_name)
        .with_ttl(datetime.timedelta(seconds=settings.livekit_token_ttl_seconds))
        .with_grants(grants)
        .to_jwt()
    )


def webhook_receiver(settings: Settings) -> lkapi.WebhookReceiver:
    return lkapi.WebhookReceiver(lkapi.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret))


async def delete_livekit_room(settings: Settings, room_name: str) -> None:
    """Принудительно закрыть комнату (отключить всех). Ошибки не критичны."""
    try:
        async with lkapi.LiveKitAPI(settings.livekit_http_url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
            await lk.room.delete_room(lkapi.DeleteRoomRequest(room=room_name))
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось закрыть комнату LiveKit", extra={"room": room_name, "error": type(exc).__name__})


async def remove_participant(settings: Settings, room_name: str, identity: str) -> None:
    """Отключить одного участника от комнаты LiveKit (например, гостя после отзыва ссылки). Ошибки не критичны."""
    try:
        async with lkapi.LiveKitAPI(settings.livekit_http_url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
            await lk.room.remove_participant(lkapi.RoomParticipantIdentity(room=room_name, identity=identity))
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось отключить участника", extra={"room": room_name, "error": type(exc).__name__})


async def list_present_identities(settings: Settings, room_name: str) -> set[str] | None:
    """Идентичности реально подключённых участников (без служебных asr-*).

    None — LiveKit недоступен (состояние неизвестно, ничего не завершаем).
    Пустое множество — комнаты нет/в ней никого нет.
    """
    try:
        async with lkapi.LiveKitAPI(settings.livekit_http_url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
            resp = await lk.room.list_participants(lkapi.ListParticipantsRequest(room=room_name))
        return {p.identity for p in resp.participants if not p.identity.startswith(SERVICE_IDENTITY_PREFIX)}
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "not_found" in text or "not found" in text or "does not exist" in text:
            return set()
        log.warning("LiveKit недоступен при сверке участников", extra={"error": type(exc).__name__})
        return None


async def mute_microphones(settings: Settings, room_name: str, *, only: set[str] | None = None, exclude: set[str] | None = None) -> list[str] | None:
    """Выключает (mute) микрофонные дорожки участников на стороне сервера LiveKit.

    only — только эти идентичности (иначе все); exclude — кроме них (например, сам руководитель). Служебные участники (ASR) не затрагиваются.
    Возвращает идентичности, у которых микрофон был включён и теперь выключен; None — LiveKit недоступен.
    Участник может включить микрофон сам: это «выключить звук», а не запрет.
    """
    from livekit.protocol import models as lkmodels  # noqa: PLC0415

    exclude = exclude or set()
    done: list[str] = []
    try:
        async with lkapi.LiveKitAPI(settings.livekit_http_url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
            resp = await lk.room.list_participants(lkapi.ListParticipantsRequest(room=room_name))
            for p in resp.participants:
                if p.identity.startswith(SERVICE_IDENTITY_PREFIX) or p.identity in exclude or (only is not None and p.identity not in only):
                    continue
                hit = False
                for t in p.tracks:
                    if t.source == lkmodels.TrackSource.MICROPHONE and not t.muted:
                        await lk.room.mute_published_track(lkapi.MuteRoomTrackRequest(room=room_name, identity=p.identity, track_sid=t.sid, muted=True))
                        hit = True
                if hit:
                    done.append(p.identity)
        return done
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось выключить микрофоны", extra={"room": room_name, "error": type(exc).__name__})
        return None
