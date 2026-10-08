"""Роли и права в переговорной — единое место, откуда берутся все решения о правах (на backend, а не только в интерфейсе).

Роли (от старшей к младшей):
  * `system_admin` — администратор сервера: вся система (LLM, LDAP, хранилища, обновление) и любая комната;
  * `room_leader`  — руководитель комнаты (их может быть несколько): полностью управляет своей комнатой — настройки, доступ, гости, режим, запись,
                     участники встречи, доска; к глобальным настройкам доступа не имеет;
  * `presenter`    — «ведущий»: участник, которому руководитель на время ТЕКУЩЕЙ встречи «дал слово» (в презентационной комнате — право говорить,
                     показывать камеру и экран, править доску); после встречи сбрасывается, постоянной роли не меняет;
  * `participant`  — сотрудник из AD;
  * `guest`        — вход по гостевой ссылке (минимальные права).

Что можно публиковать (микрофон/камера/экран) определяет `publish_sources` — они же попадают в LiveKit-токен (`can_publish_sources`), поэтому
обойти запрет через SDK нельзя: сервер звонков сам не примет дорожку.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..auth.deps import SessionUser
from ..models import Room
from .rooms import _matches

ROLE_ADMIN, ROLE_LEADER, ROLE_PRESENTER, ROLE_PARTICIPANT, ROLE_GUEST = "system_admin", "room_leader", "presenter", "participant", "guest"
ROOM_REGULAR, ROOM_PRESENTATION = "regular", "presentation"
ALL_SOURCES = ["microphone", "camera", "screen_share", "screen_share_audio"]


def is_presentation(room: Room) -> bool:
    return room.room_type == ROOM_PRESENTATION


def is_room_leader(room: Room, su: SessionUser) -> bool:
    """Руководитель этой комнаты (по списку руководителей: группы и люди AD); администратор сервера сюда не входит."""
    return _matches(room.moderators, su)


def can_manage_room(room: Room, su: SessionUser) -> bool:
    """Настройки комнаты, доступ, гости, управление участниками: руководитель комнаты или администратор сервера."""
    return su.is_admin or is_room_leader(room, su)


def room_role(room: Room, su: SessionUser, *, has_floor: bool = False) -> str:
    if su.is_admin:
        return ROLE_ADMIN
    if is_room_leader(room, su):
        return ROLE_LEADER
    return ROLE_PRESENTER if has_floor else ROLE_PARTICIPANT


def _participant_sources(room: Room, *, guest: bool) -> list[str]:
    src = ["microphone"]
    if room.camera_allowed:
        src.append("camera")
    if room.screen_share_allowed and not guest:  # гость экран не показывает
        src += ["screen_share", "screen_share_audio"]
    return src


def publish_sources(room: Room, su: SessionUser | None, *, guest: bool = False, has_floor: bool = False) -> list[str]:
    """Что может публиковать участник сейчас. Руководители и администраторы — всё; остальные — по правам комнаты, а в презентационной
    комнате — только пока у них есть слово."""
    if su is not None and (su.is_admin or is_room_leader(room, su)):
        return list(ALL_SOURCES)
    if is_presentation(room) and not has_floor:
        return []
    return _participant_sources(room, guest=guest)


def can_edit_board(room: Room, su: SessionUser | None, *, guest: bool = False, has_floor: bool = False) -> bool:
    """Правка общей доски: руководитель/администратор — всегда; остальные — если комната разрешает доску, а в презентационной — ещё и со словом."""
    if su is not None and (su.is_admin or is_room_leader(room, su)):
        return True
    if not room.board_allowed:
        return False
    return has_floor if is_presentation(room) else True


def can_control_meeting(room: Room, su: SessionUser) -> bool:
    """Записью, транскрибацией, микрофонами и словом управляют руководители и администраторы. Если у комнаты руководителей нет вовсе
    (так было до появления ролей) — любой участник, как раньше (кроме презентационной комнаты: там без руководителя управлять нечем и некому)."""
    if can_manage_room(room, su):
        return True
    return not room.moderators and not is_presentation(room)


@dataclass(frozen=True)
class FloorState:
    identities: frozenset[str]

    def has(self, identity: str) -> bool:
        return identity in self.identities
