"""Админка: выбор ASR-модели (Full / Q5_K_M), состояние, тест и сравнение; выбор хранится в настройках и раздаётся ASR через Redis."""
from __future__ import annotations

import json

import httpx

from app.workers.asr_sync import DESIRED_MODEL_KEY, publish_desired

from .conftest import login, make_settings, running_app

FULL, Q5 = "gigaam-v3-e2e-rnnt-full", "gigaam-test-alt"


def models_payload(q5_present: bool = True, active: str = FULL) -> dict:
    def m(mid, title, runtime, quant, present, missing, size):
        return {"id": mid, "title": title, "runtime": runtime, "quant": quant or "full", "device": "cpu", "family": "gigaam", "files": [], "description": "",
                "present": present, "missing": missing, "size_bytes": size, "status": "active" if mid == active else ("available" if present else "missing"),
                "error": None, "load_ms": 3400 if mid == active else None, "active": mid == active, "runtime_available": True}
    return {"active_id": active, "loading_id": None, "device": "cpu", "ready": True, "threads": {"intra": 2, "interop": 1}, "test_audio_s": 19.5,
            "models": [m(FULL, "GigaAM v3 e2e RNNT — Full", "pytorch", "", True, [], 887_000_000),
                       m(Q5, "GigaAM v3 e2e RNNT — Q5_K_M", "gguf", "Q5_K_M", q5_present, [] if q5_present else ["gigaam-v3-e2e-rnnt-Q5_K_M.gguf"], 330_000_000 if q5_present else 0)]}


class FakeAsr:
    def __init__(self, q5_present=True, down=False):
        self.q5_present, self.down, self.calls = q5_present, down, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, req.url.path, req.headers.get("x-internal-token"), body))
        if req.url.path == "/models" and req.method == "GET":
            return httpx.Response(200, json=models_payload(self.q5_present))
        if req.url.path == "/models/test":
            return httpx.Response(200, json={"ok": True, "model_id": body.get("model_id") or FULL, "inference_ms": 900, "rtf": 0.046, "cpu_s": 3.1, "ram_mb": 1500,
                                             "wer": 0.04, "punctuation": {"f1": 0.9}})
        if req.url.path == "/models/compare":
            if body.get("force") is not True and self.calls and False:
                return httpx.Response(409, json={"ok": False})
            return httpx.Response(200, json={"ok": True, "results": [], "summary": ["Быстрее: Q5_K_M"]})
        return httpx.Response(404)


def app_with(tmp_path, directory, asr):
    return running_app(make_settings(tmp_path), directory, transports={"asr": httpx.MockTransport(asr)})


def test_only_admin_can_manage_models(tmp_path, directory):
    with app_with(tmp_path, directory, FakeAsr()) as c:
        login(c, "alice")
        for call in (lambda: c.get("/api/v1/admin/asr/models"), lambda: c.put("/api/v1/admin/asr/active", json={"model_id": Q5}),
                     lambda: c.post("/api/v1/admin/asr/test", json={}), lambda: c.post("/api/v1/admin/asr/compare", json={})):
            assert call().status_code == 403


def test_models_list_shows_runtime_size_status_and_selected(tmp_path, directory):
    with app_with(tmp_path, directory, FakeAsr()) as c:
        login(c, "root")
        d = c.get("/api/v1/admin/asr/models").json()
        assert d["reachable"] and d["active_id"] == FULL and d["desired"] == "" and d["device"] == "cpu"
        by = {m["id"]: m for m in d["models"]}
        assert by[FULL]["runtime"] == "pytorch" and by[Q5]["runtime"] == "gguf" and by[FULL]["status"] == "active" and by[Q5]["size_bytes"] == 330_000_000


def test_selecting_installed_model_saves_publishes_to_asr_and_audits(tmp_path, directory):
    with app_with(tmp_path, directory, FakeAsr()) as c:
        login(c, "root")
        r = c.put("/api/v1/admin/asr/active", json={"model_id": Q5})
        assert r.status_code == 200 and r.json()["desired"] == Q5 and "перезагрузит" in r.json()["note"]
        assert c.get("/api/v1/admin/settings/asr").json()["active_model"] == Q5, "выбор хранится в настройках, а не в compose"
        assert c.get("/api/v1/admin/asr/models").json()["desired"] == Q5
        redis = c.app_obj.state.redis
        assert c.portal.call(lambda: redis.get(DESIRED_MODEL_KEY)) == Q5
        acts = [a for a in c.get("/api/v1/admin/audit").json() if a["action"] == "asr.model_change"]
        assert acts and acts[0]["details"]["to"] == Q5


def test_selecting_missing_model_is_a_clear_error_and_changes_nothing(tmp_path, directory):
    with app_with(tmp_path, directory, FakeAsr(q5_present=False)) as c:
        login(c, "root")
        r = c.put("/api/v1/admin/asr/active", json={"model_id": Q5})
        assert r.status_code == 409 and "gigaam-v3-e2e-rnnt-Q5_K_M.gguf" in r.json()["detail"] and "не установлена" in r.json()["detail"]
        assert c.get("/api/v1/admin/settings/asr").json()["active_model"] == ""
        assert c.portal.call(lambda: c.app_obj.state.redis.get(DESIRED_MODEL_KEY)) is None
        assert c.put("/api/v1/admin/asr/active", json={"model_id": "нет-такой"}).status_code == 422
        assert c.put("/api/v1/admin/asr/active", json={"model_id": "unknown-model"}).status_code == 422


def test_asr_unreachable_does_not_break_admin_and_choice_is_kept_for_later(tmp_path, directory):
    with app_with(tmp_path, directory, FakeAsr(down=True)) as c:
        login(c, "root")
        d = c.get("/api/v1/admin/asr/models").json()
        assert d["reachable"] is False and "недоступен" in d["error"]
        r = c.put("/api/v1/admin/asr/active", json={"model_id": Q5})
        assert r.status_code == 200 and "будет применён при его запуске" in r.json()["note"]
        assert c.post("/api/v1/admin/asr/test", json={}).status_code == 502


def test_test_and_compare_are_proxied_with_service_token(tmp_path, directory):
    asr = FakeAsr()
    with app_with(tmp_path, directory, asr) as c:
        login(c, "root")
        t = c.post("/api/v1/admin/asr/test", json={"model_id": Q5, "repeat": 50}).json()
        assert t["ok"] and t["model_id"] == Q5 and t["rtf"] == 0.046
        cmp_ = c.post("/api/v1/admin/asr/compare", json={"force": True}).json()
        assert cmp_["summary"] == ["Быстрее: Q5_K_M"]
        posts = [x for x in asr.calls if x[0] == "POST"]
        assert all(x[2] == "internal-test-token" for x in posts), "изменяющие вызовы защищены сервисным токеном"
        assert posts[0][3]["repeat"] == 10, "число повторов ограничено"
        acts = {a["action"] for a in c.get("/api/v1/admin/audit").json()}
        assert {"asr.model_test", "asr.model_compare"} <= acts


def test_desired_model_is_republished_after_redis_loss(tmp_path, directory):
    with app_with(tmp_path, directory, FakeAsr()) as c:
        login(c, "root")
        c.put("/api/v1/admin/asr/active", json={"model_id": Q5})
        app = c.app_obj

        async def lose_and_resync():
            await app.state.redis.delete(DESIRED_MODEL_KEY)
            async with app.state.session_maker() as db:
                return await publish_desired(db, app.state.settings_svc, app.state.redis), await app.state.redis.get(DESIRED_MODEL_KEY)

        assert c.portal.call(lose_and_resync) == (Q5, Q5)
        c.put("/api/v1/admin/settings/asr", json={"active_model": ""})  # вернуться к модели по умолчанию (.env)

        async def resync():
            async with app.state.session_maker() as db:
                await publish_desired(db, app.state.settings_svc, app.state.redis)
            return await app.state.redis.get(DESIRED_MODEL_KEY)

        assert c.portal.call(resync) is None


def test_diagnostics_report_and_system_status_show_active_model_and_runtime(tmp_path, directory):
    with app_with(tmp_path, directory, FakeAsr()) as c:
        hb = {"model_loaded": True, "provider": {"provider": "gigaam", "name": "GigaAM v3 e2e RNNT — Full", "device": "cpu", "runtime": "pytorch", "model_id": FULL, "quant": ""},
              "active_model": FULL, "models": [{"id": FULL, "title": "Full", "status": "active", "error": None}], "avg_infer_ms": 900, "rtf": 0.05}
        c.portal.call(lambda: c.app_obj.state.redis.set("asr:heartbeat", json.dumps(hb)))
        login(c, "root")
        rep = c.get("/api/v1/admin/diagnostics/report").json()
        assert rep["asr"]["runtime"] == "pytorch" and rep["asr"]["model_id"] == FULL and rep["asr"]["device"] == "cpu"
        sysd = c.get("/api/v1/admin/system").json()
        assert sysd["checks"]["asr"]["active_model"] == FULL and sysd["checks"]["asr"]["provider"]["runtime"] == "pytorch"
        hb["model_loaded"] = False
        hb["models"] = [{"id": Q5, "title": "Q5_K_M", "status": "error", "error": "Runtime GGUF недоступен"}]
        c.portal.call(lambda: c.app_obj.state.redis.set("asr:heartbeat", json.dumps(hb)))
        rep = c.get("/api/v1/admin/diagnostics/report").json()
        assert any("Runtime GGUF недоступен" in v for v in rep["verdict"])
