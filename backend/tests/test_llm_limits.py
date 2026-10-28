"""Пределы длины ответа: значения по умолчанию 60 000 / 40 000 / 60 000, глобальное применение при каждом запуске, отказ модели от большого предела,
оборыв на стороне провайдера (например, на 1200 токенах), диагностика «запрошено / применено»."""
from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.llm import LlmClient, LlmError, provider_token_cap
from app.services.llm_choice import LlmChoice, limit_report
from app.services.settings import LlmSettings

from .conftest import login, put_settings
from .test_access_and_protocols import configure, end_by_alice, llm_app, meeting_with_two
from .test_admin_features import _drain
from .test_llm_generation import ext, ok_json, run


# ------------------------------------------------------------------------------------------------ значения по умолчанию
def test_new_install_gets_60000_40000_60000_and_old_null_means_default_but_admin_numbers_stay():
    s = LlmSettings()
    assert (s.limit_protocol, s.limit_summary, s.limit_map) == (60000, 40000, 60000)
    old = LlmSettings.model_validate({"limit_protocol": None, "limit_summary": None, "limit_map": None})       # настройки до 0.46.0 хранили null
    assert (old.limit_protocol, old.limit_summary, old.limit_map) == (60000, 40000, 60000)
    mine = LlmSettings.model_validate({"limit_protocol": 12000, "limit_summary": 3000, "limit_map": None})
    assert (mine.limit_protocol, mine.limit_summary, mine.limit_map) == (12000, 3000, 60000), "введённое администратором не перезаписывается"


# ------------------------------------------------------------------------------------------------ глобальное применение
def _summary(c, mid):
    pid = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "Коротко"}).json()["protocol_id"]
    _drain(c)
    return c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()


def test_global_limit_applies_to_finished_and_new_meetings_at_each_launch_and_is_recorded(tmp_path, directory):
    seen: list[dict] = []
    with llm_app(tmp_path, directory, seen) as c:
        configure(c)
        _room, old_mid = meeting_with_two(c)
        end_by_alice(c, old_mid)
        login(c, "alice")
        g = _summary(c, old_mid)["generation"]
        assert seen[-1]["max_tokens"] == 40000 and g["limit_requested"] == 40000 and g["limit_applied"] == 40000

        login(c, "root")
        put_settings(c, "llm", limit_summary=12345)              # администратор меняет общий предел, встреча уже завершена
        login(c, "alice")
        g = _summary(c, old_mid)["generation"]
        assert seen[-1]["max_tokens"] == 12345 and g["limit_requested"] == 12345, "завершённая ранее встреча берёт новое общее значение"

        login(c, "root")
        _room2, new_mid = meeting_with_two(c)
        end_by_alice(c, new_mid)
        login(c, "alice")
        _summary(c, new_mid)
        assert seen[-1]["max_tokens"] == 12345, "новая встреча берёт то же актуальное значение"

        login(c, "root")
        put_settings(c, "llm", limit_summary=None)               # пусто — значение по умолчанию, а не «без потолка»
        login(c, "alice")
        _summary(c, old_mid)
        assert seen[-1]["max_tokens"] == 40000


def test_connection_limit_is_honored_but_never_hidden_in_the_report(tmp_path, directory):
    seen: list[dict] = []
    with llm_app(tmp_path, directory, seen) as c:
        configure(c)
        put_settings(c, "llm", max_tokens_summary=1200)           # явная настройка подключения ниже общего предела
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "alice")
        g = _summary(c, mid)["generation"]
        assert seen[-1]["max_tokens"] == 1200 and g["limit_requested"] == 40000 and g["limit_applied"] == 1200
        assert "ограничено настройкой подключения" in g["limit_note"] and "1200" in g["limit_note"] and "40000" in g["limit_note"]


# ------------------------------------------------------------------------------------------------ модель не поддерживает запрошенный объём
@pytest.mark.parametrize("text,requested,expected", [
    ("max_tokens is too large: 60000. This model supports at most 16384 completion tokens, whereas you provided 60000.", 60000, 16384),
    ("max_tokens: 60000 > 8192, which is the maximum allowed number of output tokens for claude-x", 60000, 8192),
    ("Invalid max_tokens value, the valid range of max_tokens is [1, 8192]", 60000, 8192),
    ("This model's maximum context length is 32768 tokens. However, you requested 70000 tokens (10000 in the messages, 60000 in the completion).", 60000, 22736),
    ("max_completion_tokens must be less than or equal to 4096", 60000, 4096),
    ("something else is wrong", 60000, None),
    ("supports at most 90000 completion tokens", 60000, None),
])
def test_provider_token_cap_is_parsed_from_refusals(text, requested, expected):
    assert provider_token_cap(text, requested) == expected


def test_model_that_names_its_limit_gets_one_retry_with_it_and_the_clamp_is_recorded():
    seen: list[dict] = []

    def h(req):
        body = json.loads(req.content)
        seen.append(body)
        if body["max_tokens"] > 16384:
            return httpx.Response(400, json={"error": {"message": f"max_tokens is too large: {body['max_tokens']}. This model supports at most 16384 completion tokens"}})
        return ok_json()

    c = LlmClient(ext(), transport=httpx.MockTransport(h))
    r = run(c.complete("s", "u"))
    assert [b["max_tokens"] for b in seen] == [60000, 16384] and r.max_tokens == 16384 and r.requested == 60000
    assert c.stats["clamps"] == [{"requested": 60000, "applied": 16384, "reason": "provider", "model": "m"}]
    ch = LlmChoice(ext(), "Основной", "system", True, limit_requested=60000)
    note = limit_report(ch, ext(), "protocol", c.stats)
    assert "16384" in note and "60000" in note and "предел назвал провайдер" in note


def test_unparsable_length_refusal_is_a_clear_error_not_a_silent_reduction():
    def h(req):
        return httpx.Response(400, json={"error": {"message": "Request too large for model: tokens"}})

    with pytest.raises(LlmError) as e:
        run(LlmClient(ext(), transport=httpx.MockTransport(h)).complete("s", "u"))
    assert e.value.code == "limit_rejected" and "60000" in e.value.describe() and "Request too large" in e.value.describe()


# ------------------------------------------------------------------------------------------------ фактический предел провайдера 1200
def test_provider_cutting_the_answer_at_1200_tokens_is_detected_and_not_blamed_on_the_app_limit():
    def cut(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "начало длинного документа"}, "finish_reason": "length"}],
                                         "usage": {"prompt_tokens": 500, "completion_tokens": 1200}})

    c = LlmClient(ext(), transport=httpx.MockTransport(cut))
    r = run(c.complete("s", "u"))
    assert r.truncated and r.requested == 60000 and c.stats["provider_cap"] == 1200
    note = limit_report(LlmChoice(ext(), "Основной", "system", True, limit_requested=60000), ext(), "protocol", c.stats)
    assert "1200" in note and "60000" in note and "модель или шлюз" in note

    def empty_cut(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {"prompt_tokens": 500, "completion_tokens": 1200}})

    calls: list[int] = []

    def counting(req):
        calls.append(1)
        return empty_cut(req)

    with pytest.raises(LlmError) as e:
        run(LlmClient(ext(), transport=httpx.MockTransport(counting)).complete("s", "u"))
    msg = e.value.describe()
    assert e.value.code == "empty_length" and "Провайдер оборвал ответ на 1200" in msg and "запрошено 60000" in msg and len(calls) == 1, "повтор с большим лимитом не поможет"
    assert "на «рассуждения»" not in msg


def test_local_model_cap_is_explained_against_the_requested_value():
    local = ext(provider="local")
    note = limit_report(LlmChoice(local, "Qwen3", "system", True, limit_requested=60000), local.model_copy(update={"model": "qwen3"}), "protocol", {"max_tokens": 1500})
    assert "локальная модель" in note and "1500" in note and "60000" in note


def test_admin_effective_models_report_the_requested_limit(tmp_path, directory):
    with llm_app(tmp_path, directory, []) as c:
        configure(c)
        login(c, "root")
        d = c.get("/api/v1/admin/llm/effective").json()
        assert (d["protocol"]["max_output_tokens"], d["summary"]["max_output_tokens"], d["map"]["max_output_tokens"]) == (60000, 40000, 60000)
        assert d["protocol"]["requested_tokens"] == 60000
