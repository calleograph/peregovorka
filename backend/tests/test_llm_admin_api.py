"""Раздел «Языковая модель»: нейтральные значения на чистой установке, назначения по задачам (в том числе конкретное внешнее подключение), потолок ответа по задаче,
каталог подключений без ключей, диагностика, статистика с фильтрами и «архивной» моделью. Без сети."""
from __future__ import annotations

import json

import httpx

from app.services.llm_stats import is_archived, model_stats

from .conftest import login, make_room, make_settings, put_settings, running_app

ADM = "/api/v1/admin"


def add_profile(c, name="Подключение A", model="example-model", **cfg):
    login(c, "root")
    r = c.post(f"{ADM}/api-profiles", json={"kind": "llm", "name": name, "config": {"type": "openai_compatible", "base_url": "https://api.example.com/v1", "model": model, **cfg}, "secret": "sk-test-secret-1"})
    assert r.status_code == 201, r.text
    return r.json()


def test_clean_install_has_no_configured_models_or_external_apis(client):
    login(client, "root")
    ch = client.get(f"{ADM}/llm/choices").json()
    assert ch["external"] == [], "внешних API после чистой установки нет"
    assert {m["id"] for m in ch["local"]} == {"qwen3-1.7b-q4_k_m"}, "снятая с вооружения 0.6B не предлагается"
    assert all(t["mode"] == "off" for t in ch["tasks"].values()), ch["tasks"]
    eff = client.get(f"{ADM}/llm/effective").json()
    assert {k: eff[k]["enabled"] for k in ("protocol", "summary", "map")} == {"protocol": False, "summary": False, "map": False}


def test_each_task_can_use_its_own_external_connection_and_the_task_limit_caps_the_answer(client):
    a, b = add_profile(client, "Подключение A", "model-a"), add_profile(client, "Подключение B", "model-b", max_tokens_protocol=9000)
    put_settings(client, "llm", provider="external", protocol_profile=a["id"], summary_provider="external", summary_profile=b["id"], map_provider="local", limit_protocol=3000)
    eff = client.get(f"{ADM}/llm/effective").json()
    assert eff["protocol"]["model"] == "model-a" and eff["protocol"]["profile_id"] == a["id"] and eff["protocol"]["local"] is False
    assert eff["summary"]["model"] == "model-b" and eff["map"]["local"] is True
    assert eff["protocol"]["max_output_tokens"] == 3000, "потолок задачи ниже предела подключения"
    assert eff["protocol"]["max_output_note"], "если контекст не указан — об этом сказано явно"
    ch = client.get(f"{ADM}/llm/choices").json()
    assert {p["id"] for p in ch["external"]} >= {a["id"], b["id"]} and "sk-test-secret" not in json.dumps(ch)
    assert ch["tasks"]["protocol"]["profile_id"] == a["id"] and ch["tasks"]["map"]["mode"] == "local"
    # потолок реально попадает в запрос: проверяем через выбор модели для задачи
    async def limit():
        from app.services.llm_choice import resolve_llm
        st = client.app_obj.state
        async with st.session_maker() as db:
            room = (await st.protocols.profiles.list(db, "llm"), None)
            from app.models import Room
            from sqlalchemy import select
            r = (await db.execute(select(Room).limit(1))).scalars().first()
            ch = await resolve_llm(st.protocols.profiles, st.local_llm, db, r, None, "protocol")
            return ch.settings.output_limit("protocol")[0], room
    make_room(client)
    assert client.portal.call(limit)[0] == 3000


def test_deleted_connection_falls_back_to_the_default_instead_of_breaking(client):
    a = add_profile(client, "Подключение A", "model-a")
    b = add_profile(client, "Подключение B", "model-b")
    put_settings(client, "llm", provider="external", protocol_profile=a["id"])
    assert client.delete(f"{ADM}/api-profiles/{a['id']}").status_code == 204
    eff = client.get(f"{ADM}/llm/effective").json()
    assert eff["protocol"]["enabled"] is True and eff["protocol"]["model"] in ("model-b", "")      # назначение не ломается: берётся подключение по умолчанию
    assert b["id"]


def llm_ok(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.content)
    text = '{"ok": true}' if "response_format" in body else "готово"
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}})


def test_diagnostics_reports_config_without_secrets_and_runs_both_tests(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory, transports={"llm": httpx.MockTransport(llm_ok)}) as c:
        a = add_profile(c, "Подключение A", "model-a")
        r = c.post(f"{ADM}/llm/diagnose", json={"target": f"profile:{a['id']}"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["ok"] is True and [t["name"] for t in out["tests"]] == ["Короткий ответ", "Строгий JSON (structured output)"]
        assert out["config"]["model"] == "model-a" and out["config"]["host"] == "api.example.com" and "sk-test-secret" not in json.dumps(out)
        assert c.post(f"{ADM}/llm/diagnose", json={"target": "task:protocol"}).json()["ok"] is False, "задача не настроена — понятный отказ, не ошибка сервера"
        assert c.post(f"{ADM}/llm/diagnose", json={"target": "что-то"}).status_code == 422
        login(c, "carol")
        assert c.post(f"{ADM}/llm/diagnose", json={"target": "local"}).status_code == 403 and c.get(f"{ADM}/llm/choices").status_code == 403


def test_old_06b_statistics_is_marked_archived_and_hidden_by_default(client):
    assert is_archived("qwen3-0.6b-q4_k_m") and not is_archived("qwen3-1.7b-q4_k_m")
    runs = [{"kind": "protocol", "status": "ready", "at": None, "meta": {"model": "qwen3-0.6b-q4_k_m", "llm_local": True, "model_title": "Qwen3 0.6B"}},
            {"kind": "map", "status": "ready", "at": None, "meta": {"model": "qwen3-1.7b-q4_k_m", "llm_local": True, "model_title": "Qwen3 1.7B", "llm_s": 10}}]
    rows = {r["model"]: r for r in model_stats(runs)}
    assert rows["qwen3-0.6b-q4_k_m"]["archived"] is True and rows["qwen3-1.7b-q4_k_m"]["archived"] is False and rows["qwen3-1.7b-q4_k_m"]["kind"] == "map"
    login(client, "root")
    out = client.get(f"{ADM}/llm/stats").json()
    assert set(out) == {"documents", "models", "archived_hidden"}
    assert client.get(f"{ADM}/llm/stats", params={"kind": "map", "where": "local", "days": 30, "archived": True}).status_code == 200
    assert client.get(f"{ADM}/llm/stats", params={"kind": "bad"}).status_code == 422
