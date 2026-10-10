"""Генерация документов: времена и сведения о модели, прогноз, пределы длины ответа, температура и возможности провайдера, заголовки,
разовый выбор модели, статистика по моделям."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.llm import LlmClient, LlmError
from app.services import llm_stats
from app.services.settings import LlmSettings

from .conftest import login, make_settings, put_settings, running_app
from .test_access_and_protocols import configure, end_by_alice, llm_app, meeting_with_two
from .test_admin_features import _drain, anon_ok

ADM = "/api/v1/admin"


def ok_json(finish: str = "stop", text: str = "**Протокол**\n\n* пункт") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": finish}], "usage": {"prompt_tokens": 40, "completion_tokens": 12}})


def ext(**over) -> LlmSettings:
    base = dict(provider="external", type="openai_compatible", base_url="https://llm.example.local/v1", model="m", api_key="k")
    base.update(over)
    return LlmSettings(**base)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------------------------------------ клиент
def test_temperature_defaults_to_zero_and_is_sent_only_when_the_provider_accepts_it():
    seen: list[dict] = []

    def h(req):
        seen.append(json.loads(req.content))
        return ok_json()

    c = LlmClient(ext(), transport=httpx.MockTransport(h))
    r = run(c.complete("s", "u"))
    assert seen[-1]["temperature"] == 0.0 and r.finish_reason == "stop" and not r.truncated
    run(LlmClient(ext(send_temperature=False), transport=httpx.MockTransport(h)).complete("s", "u"))
    assert "temperature" not in seen[-1]


def test_provider_that_rejects_temperature_is_retried_once_without_it():
    seen: list[dict] = []

    def h(req):
        body = json.loads(req.content)
        seen.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature' does not support 0 with this model"}})
        return ok_json()

    run(LlmClient(ext(), transport=httpx.MockTransport(h)).complete("s", "u"))
    assert len(seen) == 2 and "temperature" in seen[0] and "temperature" not in seen[1]


def test_system_message_is_merged_into_user_message_when_not_supported():
    seen: list[dict] = []

    def h(req):
        seen.append(json.loads(req.content))
        return ok_json()

    run(LlmClient(ext(supports_system=False), transport=httpx.MockTransport(h)).complete("СИСТЕМА", "ПОЛЬЗОВАТЕЛЬ"))
    assert [m["role"] for m in seen[-1]["messages"]] == ["user"] and "СИСТЕМА" in seen[-1]["messages"][0]["content"]
    run(LlmClient(ext(type="anthropic", base_url="", supports_system=False), transport=httpx.MockTransport(
        lambda req: (seen.append(json.loads(req.content)), httpx.Response(200, json={"content": [{"type": "text", "text": "x"}], "stop_reason": "end_turn"}))[1])).complete("СИСТЕМА", "П"))
    assert "system" not in seen[-1] and "СИСТЕМА" in seen[-1]["messages"][0]["content"]


def test_extra_and_secret_headers_are_sent_but_cannot_override_service_headers():
    seen: list[httpx.Request] = []

    def h(req):
        seen.append(req)
        return ok_json()

    cfg = ext(extra_headers={"X-Org": "org-1"}, secret_headers=json.dumps({"X-Route-Key": "s3cret"}))
    run(LlmClient(cfg, transport=httpx.MockTransport(h)).complete("s", "u"))
    assert seen[0].headers["X-Org"] == "org-1" and seen[0].headers["X-Route-Key"] == "s3cret" and seen[0].headers["Authorization"] == "Bearer k"
    with pytest.raises(ValueError):
        ext(extra_headers={"Content-Type": "x"})        # служебные заголовки задавать нельзя
    with pytest.raises(ValueError):
        ext(extra_headers={"X-A": "a\r\nInjected: 1"})
    # без ключа API свой Authorization (нестандартные шлюзы) передаётся как есть
    run(LlmClient(ext(api_key="", extra_headers={"Authorization": "Token abc"}), transport=httpx.MockTransport(h)).complete("s", "u"))
    assert seen[-1].headers["Authorization"] == "Token abc"


def test_response_limit_is_split_between_summary_and_protocol_and_respects_context_window():
    c = ext()
    assert c.output_limit("summary") == (2000, "") and c.output_limit("protocol") == (7000, "")
    assert ext(max_tokens_summary=1500, max_tokens_protocol=8000).output_limit("protocol") == (8000, "")
    tokens, note = ext(max_tokens_protocol=9000, context_window=8192).output_limit("protocol")
    assert tokens == 6144 and "окном контекста" in note
    assert ext(max_tokens=16000).output_limit("protocol")[0] == 16000, "изменённая администратором прежняя настройка остаётся для протокола"
    seen: list[dict] = []

    def h(req):
        seen.append(json.loads(req.content))
        return ok_json("length")

    r = run(LlmClient(ext(max_tokens_summary=1234), transport=httpx.MockTransport(h), purpose="summary").complete("s", "u"))
    assert seen[-1]["max_tokens"] == 1234 and r.truncated and r.finish_reason == "length" and r.max_tokens == 1234
    c2 = LlmClient(ext(), transport=httpx.MockTransport(h))
    run(c2.complete("s", "u"))
    assert c2.stats["length"] == 1 and c2.stats["finish"] == {"length": 1} and c2.stats["max_tokens"] == 7000


# ----------------------------------------------------------------------------- секретные заголовки в настройках
def test_secret_headers_are_encrypted_never_returned_and_merge_keeps_unchanged_values(client):
    login(client, "root")
    r = client.put(f"{ADM}/settings/llm", json={"provider": "external", "type": "custom", "base_url": "https://llm.example.local/v1", "model": "m",
                                                "extra_headers": {"X-Org": "org-1"}, "secret_headers": json.dumps({"X-Key": "top-secret-1"})})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "top-secret-1" not in r.text and body["secret_headers_set"] is True and body["secret_header_names"] == ["X-Key"] and body["extra_headers"] == {"X-Org": "org-1"}
    # второй заголовок добавлен, значение первого не присылается (null — оставить)
    r = client.put(f"{ADM}/settings/llm", json={"secret_headers": json.dumps({"X-Key": None, "X-Two": "top-secret-2"})})
    assert r.status_code == 200 and r.json()["secret_header_names"] == ["X-Key", "X-Two"] and "top-secret" not in r.text
    cfg = client.portal.call(lambda: _llm_cfg(client))
    assert cfg.secret_header_map() == {"X-Key": "top-secret-1", "X-Two": "top-secret-2"}
    raw = client.portal.call(lambda: _raw_setting(client, "llm.secret_headers"))
    assert "top-secret" not in raw, "в базе значение зашифровано"
    r = client.put(f"{ADM}/settings/llm", json={"secret_headers": json.dumps({"X-Two": None})})       # X-Key убран из списка
    assert r.json()["secret_header_names"] == ["X-Two"]
    assert client.put(f"{ADM}/settings/llm", json={"secret_headers": json.dumps({"Host": "x"})}).status_code == 422


async def _llm_cfg(client):
    async with client.app_obj.state.session_maker() as db:
        return await client.app_obj.state.settings_svc.get(db, "llm")


async def _raw_setting(client, key):
    from sqlalchemy import select
    from app.models import AppSetting
    async with client.app_obj.state.session_maker() as db:
        return (await db.execute(select(AppSetting.value).where(AppSetting.key == key))).scalar_one()


def test_profile_secret_headers_roundtrip_without_leaking(client):
    login(client, "root")
    cfg = {"type": "custom", "base_url": "https://llm.example.local/v1", "model": "m", "extra_headers": {"X-Org": "o"},
           "secret_headers": json.dumps({"X-Key": "profile-secret"})}
    r = client.post(f"{ADM}/api-profiles", json={"kind": "llm", "name": "Шлюз", "config": cfg, "secret": "api-key-1"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert "profile-secret" not in r.text and "api-key-1" not in r.text and r.json()["config"]["secret_header_names"] == ["X-Key"] and r.json()["secret_set"] is True
    r = client.patch(f"{ADM}/api-profiles/{pid}", json={"config": {"secret_headers": json.dumps({"X-Key": None, "X-New": "n"}), "temperature": 0}})
    assert r.status_code == 200 and r.json()["config"]["secret_header_names"] == ["X-Key", "X-New"] and "profile-secret" not in r.text
    listing = client.get(f"{ADM}/api-profiles", params={"kind": "llm"})
    assert "profile-secret" not in listing.text and "api-key-1" not in listing.text

    async def go():
        async with client.app_obj.state.session_maker() as db:
            return (await client.app_obj.state.profiles.get_settings(db, "llm", pid)).settings
    s = client.portal.call(go)
    assert s.api_key == "api-key-1" and s.secret_header_map() == {"X-Key": "profile-secret", "X-New": "n"}
    # смена ключа не теряет заголовки и наоборот
    assert client.patch(f"{ADM}/api-profiles/{pid}", json={"secret": "api-key-2"}).status_code == 200
    s = client.portal.call(go)
    assert s.api_key == "api-key-2" and "X-Key" in s.secret_header_map()


# ----------------------------------------------------------------------------- времена и сведения о документе
def test_document_records_timing_model_and_generation_details(tmp_path, directory):
    seen: list[dict] = []
    with llm_app(tmp_path, directory, seen) as c:
        configure(c)
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "alice")
        pid = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "Коротко"}).json()["protocol_id"]
        _drain(c)
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()
        t, g = got["timing"], got["generation"]
        assert all(t[k] for k in ("requested_at", "started_at", "llm_started_at", "llm_finished_at", "finished_at"))
        assert t["total_s"] >= t["llm_s"] >= 0 and t["queue_s"] >= 0 and t["prepare_s"] is not None
        assert datetime.fromisoformat(t["requested_at"]) <= datetime.fromisoformat(t["finished_at"])
        assert g["model"] == "m" and g["llm_local"] is False and g["api_type"] == "openai" and g["input_chars"] > 0 and g["output_chars"] > 0 and g["llm_calls"] >= 1
        assert g["llm_source"] == "system" and g["max_tokens"] == 2000, "для резюме действует свой предел длины ответа"
        assert seen[-1]["max_tokens"] == 2000 and seen[-1]["temperature"] == 0.0
        pid2 = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "Подробно"}).json()["protocol_id"]
        _drain(c)
        assert seen[-1]["max_tokens"] == 7000
        assert c.get(f"/api/v1/meetings/{mid}/protocols/{pid2}").json()["generation"]["max_tokens"] == 7000


def test_truncated_answer_is_marked_and_counted(tmp_path, directory):
    def llm(req):
        return ok_json("length")

    with running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm)}) as c:
        configure(c)
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "alice")
        pid = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x"}).json()["protocol_id"]
        _drain(c)
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()
        assert got["status"] == "ready" and got["truncated"] is True and got["warnings"], "оборванный документ не выглядит как полноценный"
        assert got["generation"]["length_hits"] == 1 and got["generation"]["finish"] == {"length": 1}
        login(c, "root")
        st = c.get(f"{ADM}/llm/stats").json()
        row = [m for m in st["models"] if m["kind"] == "protocol"][0]
        assert row["truncated"] == 1 and row["length_hits"] == 1 and row["documents"] == 1


def test_failed_attempt_keeps_model_and_time_and_is_counted_as_an_error(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon_ok),
                                                                    "llm": httpx.MockTransport(lambda r: httpx.Response(401, json={}))}) as c:
        configure(c)
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "alice")
        pid = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x"}).json()["protocol_id"]
        _drain(c)
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()
        assert got["status"] == "failed" and got["generation"]["failed"] is True and got["generation"]["model"] == "m"
        assert got["timing"]["requested_at"] and got["timing"]["finished_at"] and got["generation"]["error_code"] == "unauthorized"
        login(c, "root")
        row = c.get(f"{ADM}/llm/stats").json()["models"][0]
        assert row["failed"] == 1 and row["ok"] == 0


# ---------------------------------------------------------------------------------------------- прогноз
def runs(n: int, per_k: float, kind="protocol", model="m", local=False):
    return [{"kind": kind, "status": "ready", "at": datetime.now(timezone.utc),
             "meta": {"model": model, "llm_local": local, "llm_profile": "" if local else "P", "llm_s": per_k * 10, "input_chars": 10_000, "completion_tokens": 300, "output_chars": 900}}
            for _ in range(n)]


def test_forecast_uses_history_of_the_same_model_and_otherwise_a_wide_estimate():
    est = llm_stats.forecast([], kind="protocol", model="m", local=True, profile="", chars=30_000, limit=6000)
    assert est["basis"] == "estimate" and est["samples"] == 0 and est["chunks"] == 5 and est["high_s"] > est["low_s"] * 2
    hist = llm_stats.forecast(runs(5, 20.0), kind="protocol", model="m", local=False, profile="P", chars=30_000, limit=100_000)
    assert hist["basis"] == "history" and hist["samples"] == 5 and 400 <= hist["low_s"] <= 600 <= hist["high_s"] <= 900
    other = llm_stats.forecast(runs(5, 20.0), kind="protocol", model="other", local=False, profile="P", chars=30_000, limit=100_000)
    assert other["basis"] == "estimate", "история другой модели не используется"
    few = llm_stats.forecast(runs(2, 20.0), kind="protocol", model="m", local=False, profile="P", chars=30_000, limit=100_000)
    assert few["basis"] == "estimate", "меньше трёх документов — ложной точности нет"


def test_forecast_text_is_a_rounded_range():
    assert llm_stats.format_range(10, 40) == "меньше минуты"
    assert llm_stats.format_range(200, 400) == "примерно 3–7 минут"
    assert llm_stats.format_range(60, 100) == "примерно 1–2 минуты"
    assert llm_stats.format_range(11 * 3600, 12 * 3600) == "более 3 часов"
    assert llm_stats.plural_min(21) == "минута" and llm_stats.plural_min(5) == "минут" and llm_stats.plural_min(12) == "минут"


def test_model_stats_aggregate_by_model_and_task():
    rs = runs(3, 10.0) + runs(1, 30.0, kind="summary") + [{"kind": "protocol", "status": "failed", "at": datetime.now(timezone.utc), "meta": {"model": "m", "llm_profile": "P", "failed": True}}]
    st = {(m["model"], m["kind"]): m for m in llm_stats.model_stats(rs)}
    p = st[("m", "protocol")]
    assert p["ok"] == 3 and p["failed"] == 1 and p["documents"] == 4 and p["avg_s"] == 100.0 and p["median_s"] == 100.0 and p["avg_input_chars"] == 10_000
    assert p["tokens_per_s"] == 3.0 and st[("m", "summary")]["documents"] == 1


# ------------------------------------------------------------------------------ окно «Сформировать»: прогноз и разовая модель
def test_dialog_plan_shows_forecast_limit_and_one_time_model_only_to_managers(tmp_path, directory):
    seen: list[dict] = []

    def llm(req):
        seen.append(json.loads(req.content))
        return ok_json()

    with running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm)}) as c:
        configure(c)
        login(c, "root")
        prof = c.post(f"{ADM}/api-profiles", json={"kind": "llm", "name": "Резервный шлюз", "secret": "k2",
                                                   "config": {"type": "openai_compatible", "base_url": "https://alt.example.local/v1", "model": "alt-model"}}).json()
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "alice")
        d = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction", params={"kind": "protocol"}).json()
        assert d["can_override"] is False and "llm_choices" not in d
        assert d["plan"]["forecast"]["basis"] == "estimate" and d["plan"]["forecast"]["text"] and d["plan"]["max_output_tokens"] == 7000
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x", "llm_once": f"profile:{prof['id']}"})
        assert r.status_code == 403, "участник без прав руководителя не выбирает другую модель"
        assert not seen

        login(c, "root")
        d = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction", params={"kind": "protocol", "llm": f"profile:{prof['id']}"}).json()
        assert d["can_override"] is True and any(x["key"] == f"profile:{prof['id']}" for x in d["llm_choices"])
        assert d["plan"]["once"] is True and d["plan"]["llm_profile"] == "Резервный шлюз" and d["plan"]["llm_model"] == "alt-model" and d["plan"]["llm_source"] == "once"
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x", "llm_once": f"profile:{prof['id']}"})
        assert r.status_code == 202, r.text
        _drain(c)
        assert seen[-1]["model"] == "alt-model"
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{r.json()['protocol_id']}").json()
        assert got["generation"]["llm_source"] == "once" and got["generation"]["llm_once"] == f"profile:{prof['id']}" and got["generation"]["model"] == "alt-model"
        # настройки комнаты и системы не изменились: обычное формирование снова идёт системной моделью
        r2 = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x"})
        _drain(c)
        assert seen[-1]["model"] == "m" and r2.status_code == 202
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x", "llm_once": "profile:00000000-0000-0000-0000-000000000000"}).status_code == 409
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x", "llm_once": "мусор"}).status_code == 422


def test_effective_models_panel_shows_what_will_actually_be_called(client):
    put_settings(client, "llm", provider="local")
    d = client.get(f"{ADM}/llm/effective").json()
    assert d["protocol"]["local"] is True and d["protocol"]["enabled"] and "1.7B" in d["protocol"]["name"] and d["protocol"]["max_output_tokens"] == 1500
    put_settings(client, "llm", provider="external", type="openai", model="gpt-x", api_key="k", summary_provider="off", context_window=8192, max_tokens_protocol=9000)
    d = client.get(f"{ADM}/llm/effective").json()
    assert d["protocol"]["local"] is False and d["protocol"]["model"] == "gpt-x" and d["protocol"]["max_output_tokens"] == 6144 and "окном контекста" in d["protocol"]["max_output_note"]
    assert d["summary"]["enabled"] is False and d["rooms_with_own_model"] == {"protocol": 0, "summary": 0, "map": 0}
