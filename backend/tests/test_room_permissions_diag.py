"""0.12.0: разграничение полномочий вокруг материалов встречи и диагностика встречи для администратора.
Участник (не организатор и не руководитель) не формирует документы и не отправляет материалы — проверяет сервер, а не только скрытая кнопка;
организатор встречи (кто её начал) — может; диагностика встречи — только администратору и только по данным этой встречи."""
from __future__ import annotations

import json

from .conftest import login
from .test_access_and_protocols import end_by_alice, meeting_with_two


def test_ordinary_participant_cannot_generate_documents_but_organizer_can_open_the_dialog(client):
    _room, mid = meeting_with_two(client)             # Алиса вошла первой — организатор; Боб — обычный участник
    end_by_alice(client, mid)
    login(client, "bob")
    assert client.get(f"/api/v1/meetings/{mid}").json()["can_generate"] is False
    r = client.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol"})
    assert r.status_code == 403 and "организатор" in r.json()["detail"]
    assert client.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").status_code == 403
    assert client.post(f"/api/v1/meetings/{mid}/map").status_code == 403
    login(client, "alice")
    assert client.get(f"/api/v1/meetings/{mid}").json()["can_generate"] is True
    assert client.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").status_code == 200
    login(client, "root")
    assert client.get(f"/api/v1/meetings/{mid}").json()["can_generate"] is True


def test_manual_sending_is_for_organizer_and_leaders_not_for_everyone(client):
    _room, mid = meeting_with_two(client)
    end_by_alice(client, mid)
    login(client, "bob")
    assert client.get(f"/api/v1/meetings/{mid}/delivery").status_code in (403, 404)
    assert client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["alice@corp.test"]}).status_code in (403, 404)
    assert client.get(f"/api/v1/meetings/{mid}").json()["can_send_materials"] is False
    login(client, "alice")
    assert client.get(f"/api/v1/meetings/{mid}/delivery").status_code == 200
    assert client.get(f"/api/v1/meetings/{mid}").json()["can_send_materials"] is True


def test_meeting_diagnostics_is_admin_only_and_shows_only_this_meeting(client):
    _room, mid = meeting_with_two(client)
    _room2, other = meeting_with_two(client)
    login(client, "alice")
    for ev in ({"event": "join_ok", "meeting_id": mid}, {"event": "join_failed", "meeting_id": other}):
        assert client.post("/api/v1/client/events", json=ev).status_code == 204
    assert client.post("/api/v1/client/metrics", json={"meeting_id": mid, "rtt_ms": 42}).status_code == 204
    assert client.get(f"/api/v1/admin/meetings/{mid}/diagnostics").status_code == 403, "обычному участнику диагностика недоступна"
    login(client, "root")
    d = client.get(f"/api/v1/admin/meetings/{mid}/diagnostics")
    assert d.status_code == 200
    body = d.json()
    assert {e["event"] for e in body["events"]} == {"join_ok"}, "события другой встречи не показываются"
    assert [m["rtt_ms"] for m in body["metrics"]] == [42]
    assert "retention_note" in body and body["meeting"]["id"] == mid
    assert client.get("/api/v1/admin/meetings/00000000-0000-0000-0000-000000000000/diagnostics").status_code == 404
    audit = client.get("/api/v1/admin/audit").json()
    rows = audit if isinstance(audit, list) else audit.get("items", audit)
    assert "meeting.diagnostics_view" in json.dumps(rows, ensure_ascii=False)
