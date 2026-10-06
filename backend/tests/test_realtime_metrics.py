"""Realtime-диагностика: путь входа, жизненный цикл Room, счётчики переподключений, VAD-настройки из админки."""
from __future__ import annotations

import json

from app.workers.asr_sync import VAD_CONFIG_KEY, publish_desired

from .conftest import login, make_room, make_settings, running_app


def test_full_join_path_metrics_are_recorded_and_averaged(client):
    make_room(client)
    login(client, "alice")
    body = {"join_api_ms": 24, "room_create_ms": 3, "livekit_connect_ms": 450, "signaling_connect_ms": 140, "ice_connect_ms": 310, "get_user_media_ms": 480,
            "microphone_publish_ms": 180, "backend_ws_connect_ms": 60, "participant_active_ms": 540, "total_join_ms": 540, "rtt_ms": 20, "packet_loss_pct": 0.5,
            "bitrate_out_kbps": 800, "bitrate_in_kbps": 2400}
    assert client.post("/api/v1/client/metrics", json=body).status_code == 204
    login(client, "root")
    d = client.get("/api/v1/admin/system").json()
    t = d["timings"]
    for k in ("room_create_ms", "livekit_connect_ms", "get_user_media_ms", "backend_ws_connect_ms", "total_join_ms"):
        assert t[k]["avg"] == body[k], k
    assert d["realtime"]["client"] == {"samples": 1, "rtt_ms": 20, "packet_loss_pct": 0.5, "bitrate_out_kbps": 800, "bitrate_in_kbps": 2400}


def test_room_lifecycle_counts_show_whether_room_is_recreated(client):
    login(client, "alice")
    ev = lambda e, reason=None, detail=None: client.post("/api/v1/client/events", json={"event": e, "reason": reason, "detail": detail})  # noqa: E731
    assert ev("room_lifecycle", "ROOM_CREATE", "instance=aaaa1111 reason=initial").status_code == 204
    assert ev("room_lifecycle", "CONNECT_OK", "instance=aaaa1111").status_code == 204
    assert ev("reconnecting").status_code == 204 and ev("reconnected").status_code == 204
    assert ev("room_lifecycle", "ROOM_CREATE", "instance=bbbb2222 reason=rejoin").status_code == 204
    assert ev("screen_lifecycle", "SCREEN_PUBLISH_OK", "instance=bbbb2222").status_code == 204
    assert ev("screen_frozen", "framesDecoded_stalled").status_code == 204
    login(client, "root")
    rt = client.get("/api/v1/admin/system").json()["realtime"]
    assert rt["counters"] == {"room_create": 1, "room_create_rejoin": 1, "reconnecting": 1, "reconnected": 1, "screen_frozen": 1}
    assert rt["rooms_per_join"] == 2.0, "два объекта Room на одно входа: первый и повторный — видно, что Room пересоздавался"
    d = client.get("/api/v1/admin/client-diagnostics").json()
    assert {e["event"] for e in d["events"]} == {"reconnecting", "reconnected", "screen_frozen"}, "ошибки не вытесняются фазами жизненного цикла"
    assert [x["reason"] for x in d["lifecycle"]][::-1] == ["ROOM_CREATE", "CONNECT_OK", "ROOM_CREATE", "SCREEN_PUBLISH_OK"]
    assert "instance=aaaa1111" in d["lifecycle"][-1]["detail"]


def test_vad_parameters_are_admin_settings_validated_and_published_for_asr(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory) as c:
        login(c, "root")
        assert c.get("/api/v1/admin/settings/asr").json()["vad_end_silence_ms"] is None, "по умолчанию — значения из .env"
        ok = c.put("/api/v1/admin/settings/asr", json={"vad_threshold": 0.5, "vad_end_silence_ms": 400, "vad_min_speech_ms": 200, "vad_pad_ms": 120, "vad_max_segment_seconds": 12})
        assert ok.status_code == 200 and ok.json()["vad_end_silence_ms"] == 400
        for bad in ({"vad_threshold": 1.5}, {"vad_end_silence_ms": 10}, {"vad_max_segment_seconds": 40}):
            assert c.put("/api/v1/admin/settings/asr", json=bad).status_code == 422
        app = c.app_obj

        async def pub():
            async with app.state.session_maker() as db:
                await publish_desired(db, app.state.settings_svc, app.state.redis)
            return json.loads(await app.state.redis.get(VAD_CONFIG_KEY))

        assert c.portal.call(pub) == {"threshold": 0.5, "end_silence_ms": 400, "min_speech_ms": 200, "pad_ms": 120, "max_segment_seconds": 12}
        c.put("/api/v1/admin/settings/asr", json={k: None for k in ("vad_threshold", "vad_end_silence_ms", "vad_min_speech_ms", "vad_pad_ms", "vad_max_segment_seconds")})

        async def cleared():
            async with app.state.session_maker() as db:
                await publish_desired(db, app.state.settings_svc, app.state.redis)
            return await app.state.redis.get(VAD_CONFIG_KEY)

        assert c.portal.call(cleared) is None
