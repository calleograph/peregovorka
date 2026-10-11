"""Данные комнаты для экрана перед входом: кто руководитель (can_manage) и «входят без микрофона». От них зависит, что покажет экран приветствия презентации:
руководителю — проверку оборудования и выбор состояния микрофона, зрителю — «Вы входите в режиме зрителя»."""
from __future__ import annotations

from .conftest import login, make_room
from .test_roles_floor import LEADERS, presentation  # noqa: F401

API = "/api/v1"


def test_resolve_tells_the_welcome_screen_who_is_a_leader_and_who_is_a_viewer(client):
    room = presentation(client, mute_on_join=True)
    slug = room["slug"]
    login(client, "carol")                                   # руководитель по спискам комнаты
    mine = client.get(f"{API}/rooms/resolve/{slug}").json()["room"]
    assert mine["can_manage"] is True and mine["room_type"] == "presentation" and mine["mute_on_join"] is True
    login(client, "root")                                    # администратор — тоже руководитель любой комнаты
    assert client.get(f"{API}/rooms/resolve/{slug}").json()["room"]["can_manage"] is True
    login(client, "alice")                                   # рядовой участник — зритель
    viewer = client.get(f"{API}/rooms/resolve/{slug}").json()["room"]
    assert viewer["can_manage"] is False and viewer["room_type"] == "presentation" and viewer["guest_token"] is None


def test_regular_room_info_is_unchanged_for_everyone_except_the_new_flag(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "alice")
    info = client.get(f"{API}/rooms/resolve/{room['slug']}").json()["room"]
    assert info["room_type"] == "regular" and info["can_manage"] is False and info["mute_on_join"] is False
