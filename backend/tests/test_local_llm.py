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

    def __init__(self, reply: str = "<think>рассуждения модели</think>\n**Резюме**\n\n- решение по бюджету принято", health: int = 200):
        self.reply, self.health, self.seen, self.hosts = reply, health, [], []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.hosts.append(f"{req.url.host}:{req.url.port}{req.url.path}")
        if req.url.path == "/health":
            return httpx.Response(self.health, json={"status": "ok" if self.health == 200 else "loading model"})
        self.seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": self.reply}}], "usage": {"prompt_tokens": 50, "completion_tokens": 20}})


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
        assert body["messages"][0]["content"].endswith("/no_think") and "Не выдумывай" in body["messages"][0]["content"]
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
