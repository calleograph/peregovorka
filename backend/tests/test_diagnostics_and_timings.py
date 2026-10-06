"""Диагностика как часть продукта: отчёт, маскирование секретов, реальный WebSocket Upgrade, метрики времени входа."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from livekit import api as lkapi

from app.models import Meeting
from app.services import diagnostics, timings

from .conftest import login, make_room, make_settings, running_app
from .test_transcripts import _join, _signed_webhook


# ------------------------------------------------------------------------------ маскирование
def test_mask_hides_secrets_by_key_and_by_shape():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlMTIz"
    rep = {
        "livekit_api_secret": "S3CRET", "LDAP_BIND_PASSWORD": "pw", "app_master_key": "k", "access_token": jwt, "ok": True,
        "nested": {"internal_api_token": "t", "note": f"url wss://x/rtc/v1?access_token={jwt}&sdk=js", "n": 5},
        "list": [{"authorization": "Bearer abc"}, "LIVEKIT_API_SECRET=abcdef123 other"],
        "text": "DB_PASSWORD: hunter2, next",
    }
    out = json.dumps(diagnostics.mask_report(rep), ensure_ascii=False)
    for leaked in ("S3CRET", '"pw"', jwt, "abcdef123", "hunter2", "Bearer abc"):
        assert leaked not in out, leaked
    assert '"ok": true' in out and '"n": 5' in out and "sdk=js" in out, "несекретное остаётся"


def test_kernel_report_compares_with_livekit_recommendations(tmp_path):
    root = tmp_path / "sys"
    (root / "net" / "core").mkdir(parents=True)
    (root / "net/core/rmem_max").write_text("212992\n")
    (root / "net/core/wmem_max").write_text("212992\n")
    (root / "net/core/netdev_max_backlog").write_text("1000\n")
    bad = diagnostics.kernel_report(str(root))
    assert bad["ok"] is False and not any(v["ok"] for v in bad["params"].values())
    assert "tune-kernel.sh" in bad["note"]
    (root / "net/core/rmem_max").write_text("5000000\n")
    (root / "net/core/wmem_max").write_text("5000000\n")
    (root / "net/core/netdev_max_backlog").write_text("5000\n")
    assert diagnostics.kernel_report(str(root))["ok"] is True
    assert diagnostics.kernel_report(str(tmp_path / "nowhere"))["params"]["net.core.rmem_max"]["ok"] is None


# -------------------------------------------------------------------------- реальный WS Upgrade
async def test_ws_handshake_distinguishes_101_404_and_unreachable():
    from websockets.asyncio.server import serve

    async def handler(ws):
        await asyncio.sleep(0.05)

    def only_v1(conn, request):
        if not request.path.startswith("/rtc/v1"):
            return conn.respond(404, "not found\n")
        return None

    async with serve(handler, "127.0.0.1", 0, process_request=only_v1) as server:
        port = server.sockets[0].getsockname()[1]
        ok = await diagnostics.ws_handshake(f"ws://127.0.0.1:{port}/rtc/v1?access_token=SECRETTOKEN")
        old = await diagnostics.ws_handshake(f"ws://127.0.0.1:{port}/rtc?access_token=SECRETTOKEN")
    assert ok["ok"] is True and ok["status"] == 101
    assert old["ok"] is False and old["status"] == 404 and "устарев" in old["hint"]
    assert "SECRETTOKEN" not in json.dumps([ok, old])
    gone = await diagnostics.ws_handshake("ws://127.0.0.1:9/rtc/v1", timeout=2)
    assert gone["ok"] is False and gone["error"]


# ------------------------------------------------------------------------------ отчёт для админа
def test_report_is_admin_only_complete_and_has_no_secrets(tmp_path, directory):
    s = make_settings(tmp_path, livekit_public_url="", livekit_server_version="v1.13.7")
    with running_app(s, directory) as c:
        login(c, "alice")
        assert c.get("/api/v1/admin/diagnostics/report").status_code == 403
        login(c, "root")
        r = c.get("/api/v1/admin/diagnostics/report")
        assert r.status_code == 200
        rep = r.json()
        for key in ("app", "versions", "host", "kernel", "checks", "asr", "timings", "verdict"):
            assert key in rep, key
        assert rep["versions"]["livekit_server"] == "v1.13.7"
        assert rep["checks"]["redis"]["ok"] is True and rep["checks"]["postgres"]["ok"] is True
        assert rep["checks"]["livekit"]["rtc_v1_internal"]["ok"] is False, "LiveKit в тесте недоступен — это видно в отчёте"
        assert rep["asr"]["ready"] is False
        assert any("WebSocket" in v for v in rep["verdict"]) and any("ASR" in v for v in rep["verdict"])
        text = r.text
        for secret in (s.livekit_api_secret, s.internal_api_token, s.app_master_key, "access_token=eyJ"):
            assert secret not in text
        assert "diagnostics.report" in [a["action"] for a in c.get("/api/v1/admin/audit").json()]


# ---------------------------------------------------------------------------------- метрики времени
async def test_timings_store_validate_and_average(redis=None):
    from fakeredis import FakeAsyncRedis

    r = FakeAsyncRedis(decode_responses=True)
    for v in (1000, 2000, 3000, 4000):
        await timings.record(r, "ice_connect_ms", v)
    await timings.record(r, "ice_connect_ms", -1)
    await timings.record(r, "ice_connect_ms", 10**9)
    await timings.record(r, "ice_connect_ms", "abc")
    await timings.record(r, "unknown_metric", 5)
    avg = await timings.averages(r)
    assert avg["ice_connect_ms"] == {"n": 4, "avg": 2500, "p95": 4000, "max": 4000}
    assert avg["join_api_ms"] is None and set(avg) == set(timings.NAMES)
    assert await r.exists("timings:unknown_metric") == 0


def test_client_metrics_feed_admin_averages_and_join_reports_backend_time_and_asr_state(client):
    room = make_room(client)
    app = client.app_obj
    login(client, "alice")
    j = client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()
    assert j["asr_ready"] is False, "готовность ASR только сообщается; вход от неё не зависит"
    assert client.post("/api/v1/client/metrics", json={
        "join_api_ms": 120, "signaling_connect_ms": 900, "ice_connect_ms": 1500, "participant_active_ms": 2800,
        "microphone_publish_ms": 400, "rtt_ms": 20}).status_code == 204
    client.portal.call(lambda: app.state.redis.set("asr:heartbeat", json.dumps({"model_loaded": True, "livekit_sdk": "1.1.20", "torch_threads": 2})))
    other = client.post(f"/api/v1/rooms/{room['id']}/join", json={}).json()
    assert other["asr_ready"] is True
    login(client, "root")
    t = client.get("/api/v1/admin/system").json()["timings"]
    assert t["join_api_ms"]["avg"] == 120 and t["ice_connect_ms"]["avg"] == 1500 and t["microphone_publish_ms"]["avg"] == 400
    assert t["join_backend_ms"]["n"] >= 2 and t["join_backend_ms"]["avg"] < 5000
    assert client.get("/api/v1/admin/system").json()["versions"]["livekit_python_sdk_asr"] == "1.1.20"


# --------------------------------------------------------- комната не закрывается из-за краткого обрыва
def test_room_finished_webhook_does_not_end_meeting_inside_grace(tmp_path, directory):
    s = make_settings(tmp_path, meeting_end_grace_seconds=60)
    with running_app(s, directory) as c:
        room = make_room(c)
        a = _join(c, "alice", room["id"])
        app = c.app_obj
        for event in ("participant_left", "room_finished"):
            body, token = _signed_webhook(s, {"event": event, "room": {"name": a["livekit_room"]}, "participant": {"identity": a["identity"]}})
            assert c.post("/internal/v1/livekit/webhook", content=body, headers={"Authorization": token}).status_code == 200

        async def state():
            async with app.state.session_maker() as db:
                m = await db.get(Meeting, uuid.UUID(a["meeting_id"]))
                return m.ended_at, m.empty_since

        ended_at, empty_since = c.portal.call(state)
        assert ended_at is None, "встреча жива в течение льготного периода"
        assert empty_since is not None
        assert a["meeting_id"] in c.portal.call(lambda: app.state.redis.hgetall("asr:sessions")), "ASR-сессия не снята"
        # участник вернулся (обновил страницу) — встреча продолжается той же записью
        b = _join(c, "alice", room["id"])
        assert b["meeting_id"] == a["meeting_id"]


# --------------------------------------------------------------------------------- служебный токен
def test_internal_diag_token_is_protected_and_scoped_to_service_room(client, settings):
    assert client.get("/internal/v1/diag/token").status_code == 401
    r = client.get("/internal/v1/diag/token", headers={"Authorization": "Bearer internal-test-token"})
    assert r.status_code == 200
    claims = lkapi.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret).verify(r.json()["token"])
    assert r.json()["room"].startswith("diag-") and claims.video.room == r.json()["room"]
    assert claims.video.hidden and not claims.video.can_publish
    assert not claims.identity.startswith("u-"), "служебная identity не принимается за пользователя"
