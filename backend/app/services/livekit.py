"""Выдача LiveKit-токенов и вспомогательные вызовы LiveKit.

Identity берётся ТОЛЬКО из внутреннего идентификатора пользователя (u-<uuid hex>),
никогда из пользовательского ввода. Токен короткоживущий и привязан к одной
LiveKit-комнате (= одна встреча).
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import uuid

from livekit import api as lkapi

from ..config import Settings

log = logging.getLogger("app.livekit")

IDENTITY_PREFIX = "u-"
GUEST_IDENTITY_PREFIX = "g-"
PHONE_IDENTITY_PREFIX = "p-"      # исходящий звонок из комнаты (identity выдаём мы)
SIP_IDENTITY_PREFIX = "sip_"      # входящий звонок (identity выдаёт LiveKit SIP)
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


def parse_phone_identity(identity: str) -> uuid.UUID | None:
    """p-<32 hex> → UUID телефонного участника (исходящий звонок); всё остальное → None."""
    if not identity.startswith(PHONE_IDENTITY_PREFIX):
        return None
    try:
        return uuid.UUID(hex=identity[len(PHONE_IDENTITY_PREFIX):])
    except ValueError:
        return None


def is_phone_identity(identity: str) -> bool:
    return identity.startswith(PHONE_IDENTITY_PREFIX) or identity.startswith(SIP_IDENTITY_PREFIX)


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
                     camera_allowed: bool = True, screen_share_allowed: bool = True, sources: list[str] | None = None) -> str:
    """`sources` (роль и режим комнаты — services/roles.py) имеет приоритет; пустой список — токен слушателя (публиковать нельзя)."""
    return _issue_token(settings, identity=user_identity(user_id), display_name=display_name, livekit_room=livekit_room,
                        sources=publish_sources(camera=camera_allowed, screen_share=screen_share_allowed) if sources is None else sources)


def issue_guest_token(settings: Settings, *, guest_id: uuid.UUID, display_name: str, livekit_room: str,
                      camera_allowed: bool = True, sources: list[str] | None = None) -> str:
    """Токен гостя: без демонстрации экрана (гость — минимальные права), если `sources` не заданы явно."""
    return _issue_token(settings, identity=guest_identity(guest_id), display_name=f"{display_name} (гость)", livekit_room=livekit_room,
                        sources=publish_sources(camera=camera_allowed, screen_share=False) if sources is None else sources)


def _issue_token(settings: Settings, *, identity: str, display_name: str, livekit_room: str, sources: list[str]) -> str:
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise RuntimeError("LIVEKIT_API_KEY/LIVEKIT_API_SECRET не заданы")
    grants = lkapi.VideoGrants(
        room_join=True,
        room=livekit_room,
        can_subscribe=True,
        can_publish=bool(sources),   # слушатель презентационной комнаты: принимать можно, публиковать — нет (проверяет сам сервер звонков)
        can_publish_data=False,
        can_publish_sources=list(sources),
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


async def set_publish_permission(settings: Settings, room_name: str, identity: str, sources: list[str]) -> bool:
    """Изменить права публикации участника в ИДУЩЕЙ комнате без переподключения («дать слово» / «забрать слово»). Сервер звонков сам
    прекращает уже опубликованные дорожки, которые больше не разрешены. True — применено; False — звонковый сервер недоступен/участника нет."""
    from livekit.protocol import models as lkmodels  # noqa: PLC0415

    smap = {"microphone": lkmodels.TrackSource.MICROPHONE, "camera": lkmodels.TrackSource.CAMERA,
            "screen_share": lkmodels.TrackSource.SCREEN_SHARE, "screen_share_audio": lkmodels.TrackSource.SCREEN_SHARE_AUDIO}
    perm = lkmodels.ParticipantPermission(can_subscribe=True, can_publish=bool(sources), can_publish_data=False,
                                          can_publish_sources=[smap[x] for x in sources if x in smap])
    try:
        async with lkapi.LiveKitAPI(settings.livekit_http_url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
            await lk.room.update_participant(lkapi.UpdateParticipantRequest(room=room_name, identity=identity, permission=perm))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось изменить права участника", extra={"room": room_name, "error": type(exc).__name__})
        return False


def permission_sources(permission) -> set[str]:
    """Что разрешено публиковать по ParticipantPermission сервера звонков (пустое множество — публиковать нельзя)."""
    from livekit.protocol import models as lkmodels  # noqa: PLC0415

    if permission is None or not getattr(permission, "can_publish", False):
        return set()
    names = {lkmodels.TrackSource.MICROPHONE: "microphone", lkmodels.TrackSource.CAMERA: "camera",
             lkmodels.TrackSource.SCREEN_SHARE: "screen_share", lkmodels.TrackSource.SCREEN_SHARE_AUDIO: "screen_share_audio"}
    listed = list(getattr(permission, "can_publish_sources", []) or [])
    return {names[s] for s in listed if s in names} if listed else set(names.values())      # пустой список источников = можно любые


async def enforce_sources(settings: Settings, room_name: str, identity: str, allowed: list[str], *, tries: int = 4, pause: float = 0.4) -> dict:
    """Убедиться, что после смены прав у участника не осталось опубликованных дорожек запрещённых источников («забрать слово»).

    Сервер звонков при смене разрешений обычно сам прекращает такие дорожки; здесь это проверяется, а не предполагается: оставшиеся дорожки
    выключаются (mute), а если запрещённая дорожка не исчезла за ~1,5 с (модифицированный клиент, версия сервера не снимает дорожку) —
    участник отключается от комнаты, и вернуться с прежними правами он не может: новый токен выдаётся по текущему состоянию слова.
    Остальные участники и трансляция выступающего не затрагиваются. Результат: {"stopped": [источники], "removed": bool, "checked": bool}."""
    from livekit.protocol import models as lkmodels  # noqa: PLC0415

    names = {lkmodels.TrackSource.MICROPHONE: "microphone", lkmodels.TrackSource.CAMERA: "camera",
             lkmodels.TrackSource.SCREEN_SHARE: "screen_share", lkmodels.TrackSource.SCREEN_SHARE_AUDIO: "screen_share_audio"}
    out: dict = {"stopped": [], "removed": False, "checked": False}
    allow = set(allowed)
    try:
        async with lkapi.LiveKitAPI(settings.livekit_http_url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
            for attempt in range(tries):
                try:
                    p = await lk.room.get_participant(lkapi.RoomParticipantIdentity(room=room_name, identity=identity))
                except Exception as exc:  # noqa: BLE001
                    text = str(exc).lower()
                    if "not_found" in text or "not found" in text or "does not exist" in text:
                        out["checked"] = True            # участника уже нет — публиковать нечего
                        return out
                    raise
                bad = [t for t in p.tracks if names.get(t.source, "unknown") not in allow]
                out["checked"] = True
                if not bad:
                    return out
                for t in bad:
                    out["stopped"].append(names.get(t.source, "unknown"))
                    if not t.muted:
                        with contextlib.suppress(Exception):
                            await lk.room.mute_published_track(lkapi.MuteRoomTrackRequest(room=room_name, identity=identity, track_sid=t.sid, muted=True))
                if attempt < tries - 1:
                    await asyncio.sleep(pause)
            await lk.room.remove_participant(lkapi.RoomParticipantIdentity(room=room_name, identity=identity))
            out["removed"] = True
            log.warning("Запрещённые дорожки не исчезли после смены прав — участник отключён", extra={"room": room_name})
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось проверить дорожки участника", extra={"room": room_name, "error": type(exc).__name__})
        return out


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
    return await mute_sources(settings, room_name, {"microphone"}, only=only, exclude=exclude)


async def mute_sources(settings: Settings, room_name: str, sources: set[str], *, only: set[str] | None = None, exclude: set[str] | None = None) -> list[str] | None:
    """Выключает на стороне сервера LiveKit дорожки указанных источников (microphone | camera | screen_share | screen_share_audio).
    Остальные дорожки участника и другие участники не затрагиваются — например, остановка показа экрана одного не трогает показ другого."""
    from livekit.protocol import models as lkmodels  # noqa: PLC0415

    smap = {"microphone": lkmodels.TrackSource.MICROPHONE, "camera": lkmodels.TrackSource.CAMERA,
            "screen_share": lkmodels.TrackSource.SCREEN_SHARE, "screen_share_audio": lkmodels.TrackSource.SCREEN_SHARE_AUDIO}
    wanted = {smap[s] for s in sources if s in smap}
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
                    if t.source in wanted and not t.muted:
                        await lk.room.mute_published_track(lkapi.MuteRoomTrackRequest(room=room_name, identity=p.identity, track_sid=t.sid, muted=True))
                        hit = True
                if hit:
                    done.append(p.identity)
        return done
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось выключить дорожки участников", extra={"room": room_name, "sources": sorted(sources), "error": type(exc).__name__})
        return None
