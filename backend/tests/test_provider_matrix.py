"""Надёжность клиента модели: потоковый и обычный ответы, ошибки в теле HTTP 200, пустые ответы, предел длины, окно контекста. Провайдеры подменены (MockTransport):
проверяется, что КАЖДЫЙ из известных способов «ответить странно» превращается либо в готовый текст, либо в понятную причину, и что лишних обращений нет."""
from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.llm import LlmClient, LlmError

from .test_llm_generation import ext, ok_json, run


def sse(*events: object, done: bool = True) -> httpx.Response:
    lines = [f"data: {json.dumps(e, ensure_ascii=False)}\n\n" if not isinstance(e, str) else e for e in events]
    if done:
        lines.append("data: [DONE]\n\n")
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="".join(lines).encode())


def delta(text=None, finish=None, **extra):
    d = {"content": text} if text is not None else {}
    d.update(extra)
    return {"choices": [{"delta": d, "finish_reason": finish}]}


def client(handler, **over):
    return LlmClient(ext(**over), transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------------------------------------ потоковый ответ
def test_openai_stream_is_assembled_and_counted_and_requested_with_usage():
    seen: list[dict] = []

    def h(req):
        seen.append(json.loads(req.content))
        return sse(delta("# Прото"), delta("кол\n\n* пункт"), delta(None, "stop"), {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 7}})

    r = run(client(h).complete("s", "u"))
    assert r.text == "# Протокол\n\n* пункт" and r.prompt_tokens == 100 and r.completion_tokens == 7 and not r.truncated and r.finish_reason == "stop"
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True} and len(seen) == 1


def test_stream_cut_by_length_is_marked_truncated():
    r = run(client(lambda req: sse(delta("начало"), delta(None, "length"))).complete("s", "u"))
    assert r.truncated and r.text == "начало"


def test_anthropic_stream_is_assembled():
    def h(req):
        assert json.loads(req.content)["stream"] is True
        return sse({"type": "message_start", "message": {"usage": {"input_tokens": 50}}},
                   {"type": "content_block_start", "content_block": {"type": "thinking"}},
                   {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "..."}},
                   {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Готово"}},
                   {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " к печати"}},
                   {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}},
                   {"type": "message_stop"}, done=False)

    r = run(client(h, type="anthropic", base_url="https://api.anthropic.example/v1").complete("s", "u"))
    assert r.text == "Готово к печати" and r.prompt_tokens == 50 and r.completion_tokens == 9


def test_provider_that_ignores_stream_still_works_and_stream_options_rejection_is_handled():
    calls: list[dict] = []

    def h(req):
        b = json.loads(req.content)
        calls.append(b)
        if "stream_options" in b:
            return httpx.Response(400, json={"error": {"message": "Unknown parameter: stream_options"}})
        return ok_json()          # обычный JSON, потока нет

    r = run(client(h).complete("s", "u"))
    assert r.text.startswith("**Протокол**") and [("stream_options" in c) for c in calls] == [True, False]


def test_provider_rejecting_streaming_falls_back_to_a_plain_request_once():
    calls: list[dict] = []

    def h(req):
        b = json.loads(req.content)
        calls.append(b)
        if b.get("stream"):
            return httpx.Response(400, json={"error": {"message": "streaming is not supported for this model"}})
        return ok_json()

    r = run(client(h).complete("s", "u"))
    assert r.text and [bool(c.get("stream")) for c in calls] == [True, False]


def test_stream_can_be_switched_off_in_settings():
    seen: list[dict] = []

    def h(req):
        seen.append(json.loads(req.content))
        return ok_json()

    run(client(h, stream_response=False).complete("s", "u"))
    assert "stream" not in seen[0]


def test_error_event_inside_the_stream_is_reported_with_the_providers_words():
    with pytest.raises(LlmError) as e:
        run(client(lambda req: sse(delta("нача"), {"error": {"message": "upstream overloaded"}}, done=False)).complete("s", "u"))
    assert e.value.code == "provider_error" and "upstream overloaded" in e.value.describe()


def test_interrupted_stream_is_an_error_not_a_complete_document():
    with pytest.raises(LlmError) as e:
        run(client(lambda req: sse(delta("половина текста"), done=False)).complete("s", "u"))
    assert e.value.code.startswith("transport_") and "оборван" in e.value.describe() + e.value.detail


# ------------------------------------------------------------------------------------------------ ответ-ошибка, непонятный формат
def test_http_200_with_error_body_names_the_providers_message_instead_of_unexpected_format():
    h = lambda req: httpx.Response(200, json={"error": {"message": "No endpoints found for model m", "code": 404}})   # noqa: E731
    with pytest.raises(LlmError) as e:
        run(client(h, stream_response=False).complete("s", "u"))
    assert e.value.code == "provider_error" and "No endpoints found" in e.value.describe()


def test_unexpected_json_and_non_json_bodies_say_what_was_received():
    with pytest.raises(LlmError) as e:
        run(client(lambda req: httpx.Response(200, json={"id": "x", "object": "thing"}), stream_response=False).complete("s", "u"))
    assert e.value.code == "invalid_response" and "id, object" in e.value.describe()
    with pytest.raises(LlmError) as e:
        run(client(lambda req: httpx.Response(200, text="<html>Gateway</html>"), stream_response=False).complete("s", "u"))
    assert e.value.code == "invalid_response" and "не JSON" in e.value.describe()


# ------------------------------------------------------------------------------------------------ пустой ответ
def test_flaky_empty_answer_with_normal_finish_is_retried_exactly_once():
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 40, "completion_tokens": 0}})
        return ok_json()

    c = client(h, stream_response=False)
    r = run(c.complete("s", "u"))
    assert r.text and n["i"] == 2 and c.stats["empty_retries"] == 1


def test_persistently_empty_answer_stops_after_one_retry_with_the_providers_details():
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "  \n"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 0}})

    with pytest.raises(LlmError) as e:
        run(client(h, stream_response=False).complete("s", "u"))
    assert n["i"] == 2 and e.value.code == "empty" and "finish_reason=stop" in e.value.describe() and "модель m" in e.value.describe()


@pytest.mark.parametrize("msg,finish,code", [({"content": "", "refusal": "Не могу"}, "stop", "empty_refusal"), ({"content": ""}, "content_filter", "empty_filtered")])
def test_refusal_and_filter_are_not_retried(msg, finish, code):
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": finish}], "usage": {"completion_tokens": 0}})

    with pytest.raises(LlmError) as e:
        run(client(h, stream_response=False).complete("s", "u"))
    assert n["i"] == 1 and e.value.code == code


def test_reasoning_only_answer_is_not_blamed_on_an_empty_model_and_is_not_blindly_retried():
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "reasoning_content": "думаю…"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 300}})

    with pytest.raises(LlmError) as e:
        run(client(h, stream_response=False).complete("s", "u"))
    assert n["i"] == 1 and "рассуждения" in e.value.describe()


def _reasoning_gateway(seen: list[dict]):
    """Шлюз рассуждающей модели: пока рассуждения не отключены параметром reasoning, весь лимит уходит на «мысли» (текста нет, finish=length)."""
    def h(req):
        b = json.loads(req.content)
        seen.append(b)
        if b.get("reasoning") == {"effort": "none"}:
            return httpx.Response(200, json={"choices": [{"message": {"content": "Протокол готов"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 40}})
        n = b["max_tokens"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "reasoning": "думаю…"}, "finish_reason": "length"}],
                                         "usage": {"completion_tokens": n, "completion_tokens_details": {"reasoning_tokens": n}}})
    return h


def test_limit_eaten_by_reasoning_switches_reasoning_off_for_the_rest_of_the_document():
    seen: list[dict] = []
    c = client(_reasoning_gateway(seen), stream_response=False, reasoning="auto")
    r = run(c.complete("s", "u", max_tokens=600))
    assert r.text == "Протокол готов" and len(seen) == 2 and "reasoning" not in seen[0] and seen[1]["reasoning"] == {"effort": "none"}
    run(c.complete("s", "u", max_tokens=600))                       # следующий запрос документа сразу без рассуждений и с обычным лимитом: лишних обращений нет
    assert len(seen) == 3 and seen[2]["reasoning"] == {"effort": "none"} and seen[2]["max_tokens"] == 600 and c.stats["reasoning"] == "off_auto"


def test_reasoning_off_mode_sends_the_parameter_from_the_first_request_and_model_mode_never():
    seen: list[dict] = []
    run(client(_reasoning_gateway(seen), stream_response=False).complete("s", "u", max_tokens=600))        # по умолчанию — off
    assert len(seen) == 1 and seen[0]["reasoning"] == {"effort": "none"}
    seen.clear()
    with pytest.raises(LlmError) as e:
        run(client(_reasoning_gateway(seen), stream_response=False, reasoning="model").complete("s", "u", max_tokens=600))
    assert len(seen) == 2 and all("reasoning" not in b for b in seen) and e.value.code == "empty_length"


def test_gateway_that_rejects_the_reasoning_parameter_gets_the_request_without_it():
    seen: list[dict] = []

    def h(req):
        b = json.loads(req.content)
        seen.append(b)
        if "reasoning" in b:
            return httpx.Response(400, json={"error": {"message": "Unknown parameter: reasoning"}})
        return ok_json()

    c = client(h, stream_response=False, reasoning="off")
    assert run(c.complete("s", "u")).text and len(seen) == 2 and "reasoning" not in seen[1]
    run(c.complete("s", "u"))
    assert "reasoning" not in seen[-1] and c.stats["reasoning"] == "rejected"


def test_anthropic_never_gets_the_reasoning_parameter():
    seen: list[dict] = []

    def h(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})

    run(client(h, stream_response=False, type="anthropic", reasoning="off").complete("s", "u"))
    assert "reasoning" not in seen[0]


def test_context_window_is_read_from_the_provider_model_list_once_and_a_miss_changes_nothing():
    from app.integrations.llm import _WINDOWS, discover_window
    _WINDOWS.clear()
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        assert req.url.path.endswith("/models") and req.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"data": [{"id": "other", "context_length": 8000}, {"id": "m", "context_length": 1048576, "top_provider": {"context_length": 262144}}]})

    t = httpx.MockTransport(h)
    assert run(discover_window(ext(), transport=t)).context_window == 262144          # окно подключённого провайдера, а не общее у шлюза
    assert run(discover_window(ext(), transport=t)).context_window == 262144 and n["i"] == 1       # повторно — из кэша
    assert run(discover_window(ext(context_window=32000), transport=t)).context_window == 32000      # заданное администратором не перекрывается
    _WINDOWS.clear()
    assert run(discover_window(ext(model="нет такой"), transport=t)).context_window == 0
    _WINDOWS.clear()
    assert run(discover_window(ext(), transport=httpx.MockTransport(lambda r: httpx.Response(404)))).context_window == 0      # списка нет — настройки не меняются
    _WINDOWS.clear()
    assert run(discover_window(ext(type="anthropic"), transport=t)).context_window == 0


def test_text_in_stream_with_think_block_keeps_the_answer():
    r = run(client(lambda req: sse(delta("<think>размышляю</think>"), delta("Итог встречи"), delta(None, "stop"))).complete("s", "u"))
    assert r.text == "Итог встречи"


# ------------------------------------------------------------------------------------------------ предел ответа и стенограмма
def test_known_context_window_keeps_room_for_the_transcript():
    seen: list[int] = []

    def h(req):
        seen.append(json.loads(req.content)["max_tokens"])
        return ok_json()

    c = client(h, stream_response=False, context_window=32000, max_tokens_protocol=60000)
    run(c.complete("система", "я" * 40000))          # ≈ 20 000 токенов входа
    assert seen[0] <= 32000 - 20000 - 256 and seen[0] >= 1024
    assert c.stats["clamps"][-1]["reason"] == "context"
