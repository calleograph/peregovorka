"""Что клиенту разрешено в комнате: собирается на сервере из ролей (services/roles.py) и состояния встречи; интерфейс лишь отражает это."""
from __future__ import annotations

from ..auth.deps import SessionUser
from ..models import Room
from ..services import roles
from .schemas import ClientConfig


def build_client_config(room: Room, screen, result, *, su: SessionUser | None = None, guest: bool = False, attachments: bool = True) -> ClientConfig:
    sources = list(result.sources if result.sources is not None else roles.publish_sources(room, su, guest=guest))
    return ClientConfig(
        screen_profile=screen.profile, screen_eco_kbps=getattr(screen, "eco_bitrate_kbps", 800), screen_share_audio=False if guest else screen.share_audio, one_sharer_at_a_time=screen.one_sharer_at_a_time,
        can_moderate=bool(su and roles.can_manage_room(room, su)), is_guest=guest,
        can_manage=bool(su and roles.can_manage_room(room, su)), can_control=bool(su and roles.can_control_meeting(room, su)),
        presentation=roles.is_presentation(room), sources=sources, floor=bool(result.floor),
        can_edit_board=roles.can_edit_board(room, su, guest=guest, has_floor=bool(result.floor)),
        can_view_board=roles.can_view_board(room, su, guest=guest), board_access=roles.board_level(room),
        recording_allowed=bool(room.record_audio), attachments=attachments,
        mute_on_join=room.mute_on_join, welcome_message=room.welcome_message or None)
