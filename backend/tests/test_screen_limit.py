"""Одновременный показ экрана: до пяти участников; шестой получает отказ с причиной, освободившееся место доступно сразу."""
from __future__ import annotations

import uuid

from .conftest import make_room
from .test_transcripts import _join


def test_sixth_screen_share_is_stopped_and_a_freed_slot_is_available_at_once(client, monkeypatch):
    app = client.app_obj
    mid = uuid.uuid4()
    sharing: set[str] = set()
    muted: list[str] = []
    published: list[dict] = []

    async def sharers(_s, _room):
        return set(sharing)

    async def mute(_s, _room, _sources, *, only=None, exclude=None):
        muted.extend(only or [])
        sharing.difference_update(only or [])
        return list(only or [])

    async def publish(_r, _mid, ev):
        published.append(ev)

    monkeypatch.setattr("app.services.meetings.screen_sharers", sharers)
    monkeypatch.setattr("app.services.meetings.mute_sources", mute)
    monkeypatch.setattr("app.services.meetings.events.publish", publish)
    svc = app.state.meetings

    def start(who: str) -> bool:
        sharing.add(who)
        return client.portal.call(svc.enforce_share_limit, mid, "m-x", who, 5)

    assert [start(f"u-{i}") for i in range(5)] == [False] * 5, "пять показов идут одновременно"
    assert start("u-5") is True and muted == ["u-5"], "шестой показ остановлен сервером"
    assert published[-1]["reason"] == "limit" and published[-1]["identity"] == "u-5" and published[-1]["limit"] == 5
    sharing.discard("u-2")                                   # один завершил показ
    assert start("u-5") is False, "освободившееся место доступно сразу"
    assert start("u-6") is True, "и снова предел"
    assert start("u-0") is False, "повторная публикация того, кто уже показывает, места не теряет"
    sharing.clear()
    assert start("u-9") is False


def test_client_config_carries_the_share_limit(client):
    room = make_room(client)
    assert _join(client, "alice", room["id"])["client"]["max_sharers"] == 5
