"""Протокол и резюме одной встречи выбирают модель независимо; выбранная модель совпадает с фактически вызванной (то, что получил провайдер)."""
from __future__ import annotations

from .conftest import login
from .test_access_and_protocols import configure, end_by_alice, llm_app, meeting_with_two
from .test_admin_features import _drain
from .test_llm_admin_api import add_profile


def make(c, mid, kind, **extra):
    pid = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": kind, "instruction": "Коротко", **extra})
    assert pid.status_code in (200, 201, 202), pid.text
    pid = pid.json()["protocol_id"]
    _drain(c)
    return c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()["generation"]


def test_protocol_and_summary_call_their_own_models_and_once_choice_wins(tmp_path, directory):
    seen: list[dict] = []
    with llm_app(tmp_path, directory, seen) as c:
        configure(c)                                              # системная модель — «m»
        a, b = add_profile(c, "Подключение A", "model-a"), add_profile(c, "Подключение B", "model-b")
        room, mid = meeting_with_two(c)
        login(c, "root")
        r = c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"llm": {"mode": "profile", "profile_id": a["id"]}, "llm_summary": {"mode": "profile", "profile_id": b["id"]}})
        assert r.status_code == 200, r.text
        end_by_alice(c, mid)
        login(c, "root")

        n = len(seen)
        g = make(c, mid, "protocol")
        assert {s["model"] for s in seen[n:]} == {"model-a"} and g["model"] == "model-a" and g["llm_source"] == "room"

        n = len(seen)
        g = make(c, mid, "summary")
        assert {s["model"] for s in seen[n:]} == {"model-b"} and g["model"] == "model-b" and g["llm_source"] == "room"

        n = len(seen)                                             # разовый выбор сильнее комнаты, но только для этого формирования
        g = make(c, mid, "summary", llm_once=f"profile:{a['id']}")
        assert {s["model"] for s in seen[n:]} == {"model-a"} and g["llm_source"] == "once" and g["llm_once"]

        n = len(seen)
        g = make(c, mid, "summary")
        assert {s["model"] for s in seen[n:]} == {"model-b"} and not g.get("llm_once")

        # уровень встречи сильнее комнаты и действует только на свой документ
        assert c.put(f"/api/v1/meetings/{mid}/settings", json={"llm_summary": {"mode": "profile", "profile_id": a["id"]}}).status_code == 200
        n = len(seen)
        assert make(c, mid, "summary")["llm_source"] == "meeting" and {s["model"] for s in seen[n:]} == {"model-a"}
        n = len(seen)
        assert make(c, mid, "protocol")["llm_source"] == "room" and {s["model"] for s in seen[n:]} == {"model-a"}
        assert c.put(f"/api/v1/meetings/{mid}/settings", json={"llm_summary": None}).status_code == 200

        # отключение для одного из документов не затрагивает другой
        c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"llm_summary": {"mode": "off"}})
        n = len(seen)
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"}).status_code >= 400 and len(seen) == n
        g = make(c, mid, "protocol")
        assert {s["model"] for s in seen[n:]} == {"model-a"}
