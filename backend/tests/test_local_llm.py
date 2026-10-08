"""Встроенная локальная языковая модель (Qwen3 0.6B Q4_K_M, llama.cpp): состояние файла, режимы «Локальная / Внешняя / Отключено», протокол через
локальную модель без выхода данных наружу, предупреждения для длинных стенограмм, исправление «llm_model». Без сети: httpx.MockTransport и временные файлы."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import httpx

from app.integrations.llm import strip_thinking
from app.services import local_llm as ll

from .conftest import login, make_settings, put_settings, running_app
from .test_access_and_protocols import end_by_alice, meeting_with_two
from .test_admin_features import _drain, anon_ok
from .test_updates import chan, heartbeat

FAKE_MODEL = b"GGUF" + bytes(range(256)) * 40          # маленький «файл модели» для тестов


def model_env(tmp_path: Path, *, write: bool = True, content: bytes = FAKE_MODEL, declared: bytes = FAKE_MODEL):
    d = tmp_path / "models-llm"
    d.mkdir(exist_ok=True)
    if write:
        (d / "Qwen3-0.6B-Q4_K_M.gguf").write_bytes(content)
    return dict(local_llm_models_dir=str(d), local_llm_model_bytes=len(declared), local_llm_model_sha256=hashlib.sha256(declared).hexdigest())


class Runtime:
    """Подставной llama-server: /health и /v1/chat/completions; запоминает запросы."""

    def __init__(self, reply: str = "<think>рассуждения модели</think>\n**Резюме**\n\n- решение по бюджету принято", health: int = 200, finish: str = "stop"):
        self.reply, self.health, self.seen, self.hosts, self.finish = reply, health, [], [], finish

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.hosts.append(f"{req.url.host}:{req.url.port}{req.url.path}")
        if req.url.path == "/health":
            return httpx.Response(self.health, json={"status": "ok" if self.health == 200 else "loading model"})
        self.seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": self.reply}, "finish_reason": self.finish}], "usage": {"prompt_tokens": 50, "completion_tokens": 20}})


def app_with(tmp_path, directory, rt: Runtime, anon_calls: list | None = None, **settings_over):
    def anon(req):
        if anon_calls is not None:
            anon_calls.append(req.url.path)
        return anon_ok(req)

    return running_app(make_settings(tmp_path, **settings_over), directory,
                       transports={"local_llm": httpx.MockTransport(rt), "anonymizer": httpx.MockTransport(anon)})


# ------------------------------------------------------------------------------------------------ чистые функции
def test_strip_thinking_removes_reasoning_blocks_even_unclosed():
    assert strip_thinking("<think>\nчто-то\n</think>\n\nОтвет") == "Ответ"
    assert strip_thinking("Начало <think>оборвано без закрытия") == "Начало"
    assert strip_thinking("Без размышлений") == "Без размышлений"


def test_local_settings_become_a_plain_openai_compatible_call(tmp_path):
    from app.services.settings import LlmSettings

    local = ll.LocalLlm(make_settings(tmp_path))
    eff, is_local = local.effective(LlmSettings(provider="local"))
    assert is_local and eff.enabled and eff.type == "openai_compatible" and eff.base_url == "http://llm-local:8080/v1" and eff.model == "qwen3-0.6b-q4_k_m"
    assert eff.allow_http and not eff.use_corporate_ca and eff.api_key == "" and eff.max_tokens <= 1200 and eff.timeout >= 600
    off, loc = local.effective(LlmSettings(provider="off", enabled=True, model="m", base_url="https://x.test"))
    assert not off.enabled and not loc
    ext, loc = local.effective(LlmSettings(enabled=True, type="openai", model="gpt"))       # настройки до 0.5.0: «включена» = внешняя
    assert ext.enabled and not loc and ext.model == "gpt"
    assert LlmSettings().effective_provider == "off" and LlmSettings(enabled=True, model="m", base_url="https://x.test").effective_provider == "external"


def test_registry_is_ready_for_stronger_models():
    assert ll.DEFAULT_LOCAL_MODEL in ll.LOCAL_MODELS and ll.LOCAL_MODELS[ll.DEFAULT_LOCAL_MODEL].light
    m = ll.LOCAL_MODELS[ll.DEFAULT_LOCAL_MODEL]
    assert m.size_bytes == 484_220_320 and len(m.sha256) == 64 and m.file.endswith(".gguf") and m.runtime.startswith("llama.cpp")


# ------------------------------------------------------------------------------------------------ состояние файла
def test_file_states_missing_partial_bad_size_bad_hash_ok(tmp_path):
    env = model_env(tmp_path, write=False)
    local = ll.LocalLlm(make_settings(tmp_path, **env))
    m = local.model()
    assert local.file_state(m)["state"] == "missing"
    (tmp_path / "models-llm" / "Qwen3-0.6B-Q4_K_M.gguf.part").write_bytes(b"x")
    assert local.file_state(m)["state"] == "partial"
    f = tmp_path / "models-llm" / "Qwen3-0.6B-Q4_K_M.gguf"
    f.write_bytes(FAKE_MODEL[:-5])
    assert local.file_state(m)["state"] == "bad_size"
    f.write_bytes(FAKE_MODEL[:-1] + b"\x00")                       # тот же размер, другое содержимое — повреждение
    st = local.file_state(m)
    assert st["state"] == "bad_hash" and st["sha256_state"] == "mismatch"
    f.write_bytes(FAKE_MODEL)
    time.sleep(0.01)
    st = local.file_state(m)
    assert st["state"] == "ok" and st["sha256_state"] == "ok" and st["size_bytes"] == len(FAKE_MODEL)


def test_admin_status_api(tmp_path, directory):
    rt = Runtime()
    with app_with(tmp_path, directory, rt, **model_env(tmp_path, write=False)) as c:
        login(c, "alice")
        assert c.get("/api/v1/admin/llm/local").status_code == 403
        login(c, "root")
        st = c.get("/api/v1/admin/llm/local").json()
        assert st["file"]["state"] == "missing" and st["ready"] is False and st["provider"] == "off" and st["model"]["light"] is True
        assert "Qwen3 0.6B" in st["model"]["title"] and "облегч" in st["model"]["note"] and "наружу" in st["endpoint"]
        assert "/models" not in json.dumps(st) and "tmp" not in json.dumps(st).lower()                # путей хоста в ответе нет
        r = c.post("/api/v1/admin/llm/local/test").json()
        assert r["ok"] is False and "не загружена" in r["message"]
        (tmp_path / "models-llm" / "Qwen3-0.6B-Q4_K_M.gguf").write_bytes(FAKE_MODEL)
        st = c.get("/api/v1/admin/llm/local").json()
        assert st["file"]["state"] == "ok" and st["runtime"]["ready"] is True and st["ready"] is True
        t = c.post("/api/v1/admin/llm/local/test").json()
        assert t["ok"] is True and rt.hosts[-1] == "llm-local:8080/v1/chat/completions"
        rt.health = 503
        assert c.get("/api/v1/admin/llm/local").json()["runtime"]["ready"] is False


def test_diagnostics_report_lists_local_llm_without_calling_missing_model_a_failure(tmp_path, directory):
    from app.services.diagnostics import build_report

    with app_with(tmp_path, directory, Runtime(), **model_env(tmp_path, write=False)) as c:
        rep = c.portal.call(lambda: build_report(c.app_obj))
        chk = rep["checks"]["llm_local"]
        assert chk["ok"] is True and chk["configured"] is False and chk["state"] == "missing"           # необязательная возможность: не сбой
        put_settings(c, "llm", provider="local")
        rep = c.portal.call(lambda: build_report(c.app_obj))
        assert rep["checks"]["llm_local"]["ok"] is False and "не готова" in rep["checks"]["llm_local"]["error"]    # а выбрана — значит сбой


# ------------------------------------------------------------------------------------------------ настройка режима
def test_provider_modes_are_saved_and_validated(tmp_path, directory):
    with app_with(tmp_path, directory, Runtime(), **model_env(tmp_path)) as c:
        login(c, "root")
        got = put_settings(c, "llm", provider="local")
        assert got["provider"] == "local"
        assert put_settings(c, "llm", provider="off")["provider"] == "off"
        r = c.put("/api/v1/admin/settings/llm", json={"provider": "external"})
        assert r.status_code == 422 and "модель" in r.text.lower()                      # внешняя без модели/адреса — ошибка
        ok = put_settings(c, "llm", provider="external", type="openai", model="gpt-x", api_key="k")
        assert ok["provider"] == "external" and "k" not in json.dumps(ok.get("api_key", ""))                   # ключ наружу не отдаётся
        assert c.put("/api/v1/admin/settings/llm", json={"provider": "cloud"}).status_code == 422


# ------------------------------------------------------------------------------------------------ протокол через локальную модель
def test_protocol_via_local_model_keeps_data_inside_and_cleans_output(tmp_path, directory):
    rt, anon_calls = Runtime(), []
    with app_with(tmp_path, directory, rt, anon_calls, **model_env(tmp_path)) as c:
        put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")        # обезличиватель включён (наследуется)
        put_settings(c, "llm", provider="local")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "bob")
        plan = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").json()["plan"]
        assert plan["llm_ready"] is True and plan["llm_local"] is True and plan["anonymize"] is False and plan["warnings"] == []
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "Кратко: решения, задачи, ответственные"})
        assert r.status_code == 202
        _drain(c)
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{r.json()['protocol_id']}").json()
        assert got["status"] == "ready", got
        assert "<think>" not in got["content"] and "рассуждения" not in got["content"] and "решение по бюджету" in got["content"]
        assert anon_calls == [], "данные не покидают сервер — внешнее обезличивание для локальной модели не вызывается"
        assert set(rt.hosts) == {"llm-local:8080/v1/chat/completions"}
        body = rt.seen[-1]
        assert body["model"] == "qwen3-0.6b-q4_k_m" and body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["messages"][0]["content"].endswith("/no_think") and "не выдумывай" in body["messages"][0]["content"]
        assert "Authorization" not in json.dumps(body)
        meta = got["meta"] if "meta" in got else {}
        assert not meta or meta.get("llm_local") is True


def test_room_can_still_force_anonymization_for_local_model(tmp_path, directory):
    rt, anon_calls = Runtime(), []
    with app_with(tmp_path, directory, rt, anon_calls, **model_env(tmp_path)) as c:
        put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
        put_settings(c, "llm", provider="local")
        _room, mid = meeting_with_two(c, anonymize_mode="on")
        end_by_alice(c, mid)
        login(c, "bob")
        c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})
        _drain(c)
        assert anon_calls, "явное «обезличивать» для переговорки сохраняется и для локальной модели"


def test_local_model_not_downloaded_blocks_generation_with_a_clear_message(tmp_path, directory):
    rt = Runtime()
    with app_with(tmp_path, directory, rt, **model_env(tmp_path, write=False)) as c:
        put_settings(c, "llm", provider="local")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "bob")
        plan = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").json()["plan"]
        assert plan["llm_ready"] is False and any("не загружена" in w for w in plan["warnings"])
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"}).status_code == 409
        assert rt.seen == []


def test_long_transcript_warns_that_the_light_model_may_be_worse(tmp_path, directory, monkeypatch):
    monkeypatch.setitem(ll.LOCAL_MODELS, ll.DEFAULT_LOCAL_MODEL, replace(ll.QWEN3_06B, warn_input_chars=10))
    rt = Runtime()
    with app_with(tmp_path, directory, rt, **model_env(tmp_path)) as c:
        put_settings(c, "llm", provider="local")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "bob")
        plan = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").json()["plan"]
        assert plan["llm_ready"] is True and plan["input_chars"] > 10
        assert any("облегч" in w and "качество" in w for w in plan["warnings"]), plan["warnings"]
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})
        _drain(c)
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{r.json()['protocol_id']}").json()
        assert got["status"] == "ready"


def test_long_input_is_split_by_the_model_limit_not_the_global_one(tmp_path, directory, monkeypatch):
    monkeypatch.setitem(ll.LOCAL_MODELS, ll.DEFAULT_LOCAL_MODEL, replace(ll.QWEN3_06B, max_input_chars=40))
    rt = Runtime(reply="Заметки")
    with app_with(tmp_path, directory, rt, **model_env(tmp_path)) as c:
        put_settings(c, "llm", provider="local")
        room, mid = meeting_with_two(c)
        from .test_transcripts import _feed_and_consume, _segment, _join  # noqa: F401

        end_by_alice(c, mid)
        login(c, "bob")
        c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})
        _drain(c)
        assert len(rt.seen) >= 3, "стенограмма длиннее предела локальной модели → заметки по фрагментам + итоговый вызов"


def test_external_provider_is_unchanged_and_never_uses_the_local_endpoint(tmp_path, directory):
    seen: list[dict] = []

    def external(req: httpx.Request) -> httpx.Response:
        seen.append({"url": str(req.url), "body": json.loads(req.content)})
        return httpx.Response(200, json={"choices": [{"message": {"content": "**Протокол**\n\n- ок"}}]})

    rt = Runtime()
    with running_app(make_settings(tmp_path, **model_env(tmp_path)), directory,
                     transports={"local_llm": httpx.MockTransport(rt), "llm": httpx.MockTransport(external), "anonymizer": httpx.MockTransport(anon_ok)}) as c:
        put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
        put_settings(c, "llm", provider="external", type="openai", model="gpt-x", api_key="k")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "bob")
        plan = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").json()["plan"]
        assert plan["llm_local"] is False and plan["anonymize"] is True
        c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})
        _drain(c)
        assert seen and "chat_template_kwargs" not in seen[-1]["body"] and rt.seen == []


# ------------------------------------------------------------------------------------------------ повторная загрузка (исправление)
def test_llm_model_repair_can_be_requested_even_when_the_scan_found_nothing(client):
    d = chan(client)
    heartbeat(d, uid=0)
    login(client, "root")
    assert client.post("/api/v1/admin/updates/repairs/llm_model/fix").status_code == 200
    req = dict(line.split("=", 1) for line in (d / "request.txt").read_text().splitlines())
    assert req["action"] == "repair" and req["repair"] == "llm_model"
    (d / "request.txt").unlink()
    assert client.post("/api/v1/admin/updates/repairs/sysctl/fix").status_code == 409          # остальные — только найденные проверкой
    heartbeat(d, uid=1000)
    assert client.post("/api/v1/admin/updates/repairs/llm_model/fix").status_code == 409        # и только помощнику с правами


def test_old_clients_that_only_toggle_enabled_keep_working(tmp_path, directory):
    """Клиент прежней версии меняет только флаг «включена»: режим выводится из него заново, а не залипает на ранее выведенном «Отключено»."""
    with app_with(tmp_path, directory, Runtime(), **model_env(tmp_path)) as c:
        login(c, "root")
        assert c.get("/api/v1/admin/settings/llm").json()["provider"] == "off"
        got = put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")
        assert got["provider"] == "external" and got["enabled"] is True
        got = put_settings(c, "llm", enabled=False)
        assert got["provider"] == "off" and got["enabled"] is False
        assert put_settings(c, "llm", provider="local")["enabled"] is True


# ------------------------------------------------------------------------------------------------ конвейер: короткий промпт, заметки-извлечение, обрезка
def _llm(reply="ok", finish="stop", local=True, **cfg_over):
    from app.integrations.llm import LlmClient
    from app.services.settings import LlmSettings

    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    cfg = LlmSettings(enabled=True, provider="external", type="openai_compatible", base_url="http://llm.test/v1", model="m", max_tokens=1200, allow_http=True, **cfg_over)
    return LlmClient(cfg, transport=httpx.MockTransport(handler), local=local), seen


def _run(llm, **kw):
    import asyncio

    from app.services.protocols import run_llm_pipeline

    base = dict(kind="protocol", instruction="x", text="[10:00:00] Анна: Релиз переносим на вторник.\n" * 3, limit=100000, local=ll.QWEN3_06B, anonymized=False)
    base.update(kw)
    return asyncio.run(run_llm_pipeline(llm, **base))


def test_label_example_is_given_to_the_model_only_when_the_text_is_anonymized():
    from app.services.protocols import system_prompt

    assert "[ФИО_1]" in system_prompt(True) and "[ФИО_1]" not in system_prompt(False)
    ext, seen = _llm(local=False)
    _run(ext, local=None, anonymized=False)
    assert "[ФИО_1]" not in seen[0]["messages"][0]["content"], "небольшие модели подражают примеру и выдают [ФИО_1] вместо имён"
    ext, seen = _llm(local=False)
    _run(ext, local=None, anonymized=True)
    assert "[ФИО_1]" in seen[0]["messages"][0]["content"]


def test_local_model_gets_a_short_prompt_and_a_long_template_is_reported():
    long_template = "Сформировать протокол. Структура: 1) тема; 2) вопросы; 3) решения — таблица; 4) поручения — таблица. " * 8
    llm, seen = _llm()
    res = _run(llm, instruction=long_template)
    system = seen[0]["messages"][0]["content"]
    assert "Разделы: Обсуждение" in system and "таблица" not in system and len(system) < 600
    assert any("не применена" in w for w in res.warnings), res.warnings
    # короткие пожелания пользователя применяются, стандартная инструкция по умолчанию не дублируется
    llm, seen = _llm()
    res = _run(llm, instruction="Особое внимание срокам")
    assert "Особое внимание срокам" in seen[0]["messages"][0]["content"] and res.warnings == []
    from app.services.settings import DEFAULT_PROTOCOL_INSTRUCTION

    llm, seen = _llm()
    _run(llm, instruction=DEFAULT_PROTOCOL_INSTRUCTION)
    assert "Сформировать официальный протокол" not in seen[0]["messages"][0]["content"]
    # резюме — свой короткий промпт
    llm, seen = _llm()
    _run(llm, kind="summary", instruction="x")
    assert "не более 8 строк" in seen[0]["messages"][0]["content"]


def test_chunk_notes_extract_facts_instead_of_rewriting_the_transcript():
    llm, seen = _llm(reply="(1) решение: релиз во вторник")
    res = _run(llm, text=("[10:00:00] Анна: Релиз переносим на вторник.\n" * 30), limit=400)
    assert res.parts > 2 and res.calls == res.parts + 1 and not res.truncated
    notes = [b for b in seen if b["messages"][1]["content"].startswith("Фрагмент ")]
    assert len(notes) == res.parts
    for b in notes:
        sys = b["messages"][0]["content"]
        assert "НЕ копируй" in sys and "решения" in sys and "подробные заметки" not in sys
        assert b["max_tokens"] <= 600, "заметки короткие: если модель вместо списка переписывает текст, это быстро обрывается и видно"
    assert seen[-1]["messages"][1]["content"].startswith("Заметки по фрагментам") and seen[-1]["max_tokens"] == 1200


def test_truncated_answer_is_never_silent():
    llm, _ = _llm(reply="Обрывок документа", finish="length")
    res = _run(llm)
    assert res.truncated is True and any("не смогла полностью обработать стенограмму" in w and "неполным" in w for w in res.warnings), res.warnings
    # обрыв на шаге заметок тоже виден и называет часть
    llm, _ = _llm(reply="Обрывок", finish="length")
    res = _run(llm, text=("[10:00:00] Анна: Релиз переносим на вторник.\n" * 30), limit=400)
    assert res.truncated and any("часть 1 из" in w for w in res.warnings) and any("итоговый документ" in w for w in res.warnings)
    # внешняя модель: другой текст, подсказывающий настройку
    ext, _ = _llm(reply="Обрывок", finish="length", local=False)
    res = _run(ext, local=None)
    assert res.truncated and any("Максимальную длину ответа" in w for w in res.warnings)
    # нормальный ответ — без предупреждений
    ok, _ = _llm()
    assert _run(ok).warnings == [] and _run(ok).truncated is False


def test_anthropic_max_tokens_stop_reason_is_a_truncation():
    import asyncio

    from app.integrations.llm import LlmClient
    from app.services.settings import LlmSettings

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "x"}], "stop_reason": "max_tokens", "usage": {"input_tokens": 1, "output_tokens": 2}})

    cfg = LlmSettings(enabled=True, provider="external", type="anthropic", model="m", api_key="K", max_tokens=100)
    r = asyncio.run(LlmClient(cfg, transport=httpx.MockTransport(handler)).complete("s", "u"))
    assert r.truncated is True


def test_truncation_warning_reaches_the_saved_protocol_and_api(tmp_path, directory):
    rt = Runtime(reply="Обрывок документа", finish="length")
    with app_with(tmp_path, directory, rt, **model_env(tmp_path)) as c:
        put_settings(c, "llm", provider="local")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "bob")
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})
        _drain(c)
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{r.json()['protocol_id']}").json()
        assert got["status"] == "ready" and got["truncated"] is True
        assert any("не смогла полностью обработать стенограмму" in w for w in got["warnings"]), got["warnings"]
        listed = c.get(f"/api/v1/meetings/{mid}/protocols").json()
        items = listed["items"] if isinstance(listed, dict) else listed
        assert items and items[0]["truncated"] is True
