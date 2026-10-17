"""Скачивание схемы доски попадает в журнал аудита: кто, когда, откуда, какой формат и сколько страниц; содержимое схемы в журнал не попадает."""
from __future__ import annotations

import pytest

from .conftest import login, make_room
from .test_collab_guests import BOARD
from .test_transcripts import _join


def audit_rows(client, action):
    login(client, "root")
    return [a for a in client.get("/api/v1/admin/audit").json() if a["action"] == action]


def meeting_with_board(client):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    assert client.put(f"/api/v1/meetings/{mid}/whiteboard", json={"xml": BOARD, "seq": 0}).json()["saved"]
    return room, mid


@pytest.mark.parametrize("fmt,pages", [("png", 1), ("svg", 3), ("pdf", 2), ("drawio", 1)])
def test_browser_export_is_recorded_with_format_and_pages(client, fmt, pages):
    _room, mid = meeting_with_board(client)
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/export", json={"format": fmt, "pages": pages}).status_code == 204
    rows = audit_rows(client, "board.export")
    assert len(rows) == 1 and rows[0]["target_id"] == mid and rows[0]["details"]["format"] == fmt and rows[0]["details"]["pages"] == pages
    assert rows[0]["ip"], "источник запроса записан"
    assert "GW-1" not in str(rows[0]), "содержимое схемы в журнал не попадает"


@pytest.mark.parametrize("body", [{"format": "exe", "pages": 1}, {"format": "png", "pages": 0}, {"format": "png", "pages": 9999}, {"format": "png", "pages": "2"},
                                  {"format": "png", "pages": True}, {"pages": 1}])
def test_bad_export_reports_are_rejected_and_not_recorded(client, body):
    _room, mid = meeting_with_board(client)
    login(client, "alice")
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/export", json=body).status_code == 422
    assert audit_rows(client, "board.export") == []


def test_someone_without_access_cannot_write_to_the_audit_through_this_endpoint(client):
    _room, mid = meeting_with_board(client)
    client.cookies.clear()
    assert client.post(f"/api/v1/meetings/{mid}/whiteboard/export", json={"format": "png", "pages": 1}).status_code in (401, 403)
    assert audit_rows(client, "board.export") == []


def test_downloading_the_server_file_is_recorded_too(client):
    _room, mid = meeting_with_board(client)
    login(client, "alice")
    r = client.get(f"/api/v1/meetings/{mid}/whiteboard.drawio")
    assert r.status_code == 200 and "GW-1" in r.text
    rows = audit_rows(client, "board.download")
    assert len(rows) == 1 and rows[0]["details"]["format"] == "drawio" and rows[0]["details"]["source"] == "server" and rows[0]["target_id"] == mid
