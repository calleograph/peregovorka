"""Комната как единица настройки: языковая модель (системная → комната → встреча, запасной вариант), рассылка материалов (архив, настройки встречи)."""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from dataclasses import replace

import httpx

from app.services import local_llm as ll
from app.services.mail_delivery import MailError  # noqa: F401  (проверка импорта)

from .conftest import OTHER_GROUP, login, make_room, make_settings, put_settings, running_app
from .test_access_and_protocols import end_by_alice, meeting_with_two
from .test_admin_features import _drain, anon_ok
from .test_local_llm import FAKE_MODEL, Runtime, model_env
from .test_mail import LEADERS, add_profile, fake_smtp, messages, run_queue  # noqa: F401
from .test_transcripts import _join


def ext_profile(c, name="Сильная внешняя", model="big-model"):
    login(c, "root")
    r = c.post("/api/v1/admin/api-profiles", json={"kind": "llm", "name": name, "secret": "SECRET-X", "config": {"type": "openai_compatible", "base_url": "https://llm-x.test/v1", "model": model}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def app_(tmp_path, directory, rt, ext_seen=None, **over):
    def external(req: httpx.Request) -> httpx.Response:
        if ext_seen is not None:
            ext_seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "**Резюме** от внешней модели"}}]})

    return running_app(make_settings(tmp_path, **model_env(tmp_path), **over), directory,
                       transports={"local_llm": httpx.MockTransport(rt), "llm": httpx.MockTransport(external), "anonymizer": httpx.MockTransport(anon_ok)})


def manage(c, room_id, **body):
    r = c.patch(f"/api/v1/rooms/{room_id}/manage", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def plan(c, mid):
    return c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").json()["plan"]


# ------------------------------------------------------------------------------------------------ модель комнаты
def test_room_exposes_llm_options_and_effective_model(tmp_path, directory):
    with app_(tmp_path, directory, Runtime()) as c:
        pid = ext_profile(c)
        put_settings(c, "llm", provider="local")
        room = make_room(c, moderators=LEADERS)
        login(c, "carol")                                   # руководитель комнаты
        got = c.get(f"/api/v1/rooms/{room['id']}/manage").json()
        assert got["llm"]["mode"] == "inherit" and got["llm_effective"]["source"] == "system"
        opts = got["llm_options"]
        assert opts["system"]["provider"] == "local" and [m["id"] for m in opts["local"]] == ["qwen3-0.6b-q4_k_m", "qwen3-1.7b-q4_k_m"] and opts["local"][0]["installed"] is True and opts["local"][1]["installed"] is False
        assert pid in [p["id"] for p in opts["profiles"]] and "SECRET" not in json.dumps(got)
        assert opts["on_missing"] == "system"


def test_room_can_use_local_external_off_or_inherit(tmp_path, directory):
    ext_seen: list[dict] = []
    rt = Runtime()
    with app_(tmp_path, directory, rt, ext_seen) as c:
        pid = ext_profile(c)
        put_settings(c, "llm", provider="off")
        room = make_room(c, moderators=LEADERS)
        login(c, "carol")
        # системная — «отключена», а комната выбрала локальную модель
        out = manage(c, room["id"], llm={"mode": "local", "local_model": "qwen3-0.6b-q4_k_m"})
        assert out["llm"]["mode"] == "local" and out["llm_effective"]["name"].startswith("Qwen3") and out["llm_effective"]["available"] is True
        _r, mid = (room, _join(c, "alice", room["id"])["meeting_id"])
        end_by_alice(c, mid)
        login(c, "carol")
        p = plan(c, mid)
        assert p["llm_ready"] is True and p["llm_local"] is True and p["llm_source"] == "room"
        # внешний профиль
        out = manage(c, room["id"], llm={"mode": "profile", "profile_id": pid})
        assert out["llm_effective"]["name"] == "Сильная внешняя" and out["llm_effective"]["source"] == "room"
        p = plan(c, mid)
        assert p["llm_ready"] is True and p["llm_local"] is False and p["llm_model"] == "big-model"
        # отключена
        out = manage(c, room["id"], llm={"mode": "off"})
        assert out["llm_effective"]["available"] is False and "отключена" in out["llm_effective"]["reason"]
        assert plan(c, mid)["llm_ready"] is False
        # «как системная» — системная отключена
        out = manage(c, room["id"], llm={"mode": "inherit"})
        assert out["llm"]["mode"] == "inherit" and out["llm_effective"]["available"] is False
        # недопустимое
        assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"llm": {"mode": "cloud"}}).status_code == 422
        assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"llm": {"mode": "profile"}}).status_code == 422
        assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"llm": {"mode": "local", "local_model": "gpt-9"}}).status_code == 422
        login(c, "alice")                                  # обычный участник настройки менять не может
        assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"llm": {"mode": "off"}}).status_code in (403, 404)


def test_deleted_profile_falls_back_to_system_model_with_a_note_or_becomes_unavailable_by_policy(tmp_path, directory):
    with app_(tmp_path, directory, Runtime()) as c:
        pid = ext_profile(c)
        put_settings(c, "llm", provider="local", on_missing="system")
        room = make_room(c, moderators=LEADERS)
        manage_ = lambda **b: manage(c, room["id"], **b)  # noqa: E731
        login(c, "carol")
        manage_(llm={"mode": "profile", "profile_id": pid})
        mid = _join(c, "alice", room["id"])["meeting_id"]
        end_by_alice(c, mid)
        login(c, "root")
        assert c.delete(f"/api/v1/admin/api-profiles/{pid}").status_code in (200, 204)
        login(c, "carol")
        p = plan(c, mid)                                   # комната не сломалась: используется системная локальная модель с пометкой
        assert p["llm_ready"] is True and p["llm_local"] is True and "удалён" in p["llm_note"] and p["llm_source"] == "system"
        got = c.get(f"/api/v1/rooms/{room['id']}/manage").json()
        assert got["llm_effective"]["available"] is True and "удалён" in got["llm_effective"]["note"]
        # политика «недоступна»: понятное состояние вместо молчаливой подмены
        put_settings(c, "llm", on_missing="unavailable")
        login(c, "carol")
        p = plan(c, mid)
        assert p["llm_ready"] is False and "удалён" in p["llm_reason"]
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})
        assert r.status_code == 409


def test_local_model_not_downloaded_in_room_uses_policy(tmp_path, directory):
    rt = Runtime()
    with app_(tmp_path, directory, rt) as c:
        put_settings(c, "llm", enabled=True, type="openai_compatible", base_url="https://llm-main.test/v1", model="main-model", api_key="k")
        room = make_room(c, moderators=LEADERS)
        login(c, "carol")
        (tmp_path / "models-llm" / "Qwen3-0.6B-Q4_K_M.gguf").unlink()           # файл пропал после выбора
        out = manage(c, room["id"], llm={"mode": "local", "local_model": "qwen3-0.6b-q4_k_m"})
        eff = out["llm_effective"]
        assert eff["available"] is True and eff["source"] == "system" and "не загружена" in eff["note"]       # запасной вариант — системная внешняя модель
        login(c, "root")
        put_settings(c, "llm", on_missing="unavailable")
        login(c, "carol")
        eff = c.get(f"/api/v1/rooms/{room['id']}/manage").json()["llm_effective"]
        assert eff["available"] is False and "не загружена" in eff["reason"]


# ------------------------------------------------------------------------------------------------ модель и рассылка встречи
def test_meeting_overrides_win_over_room_and_can_be_reset(tmp_path, directory):
    ext_seen: list[dict] = []
    rt = Runtime()
    with app_(tmp_path, directory, rt, ext_seen) as c:
        pid = ext_profile(c)
        put_settings(c, "llm", provider="local")
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "alice")
        assert c.get(f"/api/v1/meetings/{mid}/settings").status_code == 404           # участник настройки встречи не видит
        login(c, "carol")
        s = c.get(f"/api/v1/meetings/{mid}/settings").json()
        assert s["llm"]["effective"]["source"] == "system" and s["delivery"]["override"] is False and s["ended"] is False
        # для этой встречи нужна сильная внешняя модель
        s = c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": {"mode": "profile", "profile_id": pid}}).json()
        assert s["llm"]["effective"]["name"] == "Сильная внешняя" and s["llm"]["effective"]["source"] == "meeting" and s["llm"]["room"]["mode"] == "inherit"
        end_by_alice(c, mid)
        login(c, "carol")
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})
        assert r.status_code == 202
        _drain(c)
        assert ext_seen and rt.seen == [], "для этой встречи использована внешняя модель, а не локальная"
        # сброс: «как в комнате»
        s = c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": None}).json()
        assert s["llm"]["override"] is None and s["llm"]["effective"]["source"] == "system"
        assert c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": {"mode": "magic"}}).status_code == 422
        # рассылку менять после завершения нельзя
        assert c.put(f"/api/v1/meetings/{mid}/settings", json={"delivery": {"enabled": False, "materials": []}}).status_code == 409


def test_meeting_delivery_override_and_archive(client, fake_smtp):
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": {"enabled": True, "archive": False, "materials": ["protocol"],
                                                                                  "recipients": {"leaders": False, "participants": False, "emails": ["room@example.local"]}}})
    mid = _join(client, "alice", room["id"])["meeting_id"]
    _join(client, "bob", room["id"])
    login(client, "carol")
    s = client.get(f"/api/v1/meetings/{mid}/settings").json()
    assert s["delivery"]["effective"]["recipients"]["emails"] == ["room@example.local"] and s["delivery"]["override"] is False
    # руководитель встречи меняет рассылку только для этой встречи: другие получатели, два материала, одним архивом
    spec = {"enabled": True, "archive": True, "materials": ["protocol", "summary"], "recipients": {"leaders": False, "participants": False, "emails": ["only-this@example.local"]}}
    s = client.put(f"/api/v1/meetings/{mid}/settings", json={"delivery": spec}).json()
    assert s["delivery"]["override"] is True and s["delivery"]["effective"]["archive"] is True and s["delivery"]["room"]["recipients"]["emails"] == ["room@example.local"]
    assert client.put(f"/api/v1/meetings/{mid}/settings", json={"delivery": {"enabled": True, "materials": []}}).status_code == 422
    import asyncio  # noqa: F401
    from app.models import Protocol

    async def add():
        async with client.app_obj.state.session_maker() as db:
            for kind, text in (("protocol", "# Протокол\n\nРешили."), ("summary", "Кратко: ок.")):
                db.add(Protocol(meeting_id=uuid.UUID(mid), kind=kind, status="ready", content=text, created_by="test"))
            await db.commit()

    client.portal.call(add)
    login(client, "root")
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    _drain(client)
    rows = messages(client)
    assert {r["recipient"] for r in rows} == {"only-this@example.local"}, "для встречи действуют её настройки, а не комнаты"
    assert run_queue(client) == 1
    atts = list(fake_smtp.sent[0].iter_attachments())
    assert len(atts) == 1 and atts[0].get_filename().endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(atts[0].get_payload(decode=True)))
    assert len(z.namelist()) == 2 and all(n.endswith(".docx") for n in z.namelist())


def test_room_delivery_spec_keeps_archive_flag_and_defaults_to_separate_files(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    out = client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": {"enabled": True, "materials": ["summary"], "recipients": {"leaders": True}}}).json()
    assert out["mail_delivery"]["archive"] is False
    out = client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": {"enabled": True, "archive": True, "materials": ["summary"], "recipients": {"leaders": True}}}).json()
    assert out["mail_delivery"]["archive"] is True


def test_materials_tab_data_shows_retention_read_only_and_leader_can_edit_instruction(client):
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    got = client.get(f"/api/v1/rooms/{room['id']}/manage").json()
    assert set(got["retention"]) == {"text_days", "audio_days", "history_access", "anonymize_mode"}
    out = manage(client, room["id"], protocol_instructions="Только решения и сроки")
    assert out["protocol_instructions"] == "Только решения и сроки"
    r = client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"text_retention_days": 1})          # сроки хранения — только администратор
    assert r.status_code in (200, 422) and client.get(f"/api/v1/rooms/{room['id']}/manage").json()["retention"]["text_days"] != 1


# ------------------------------------------------------------------------------------------------ приоритет выбора модели
def default_ext_profile(c, model="big-model"):
    """Внешний профиль, отмеченный «по умолчанию среди внешних API»."""
    login(c, "root")
    r = c.post("/api/v1/admin/api-profiles", json={"kind": "llm", "name": "Внешний по умолчанию", "secret": "SECRET-D", "make_default": True,
                                                    "config": {"type": "openai_compatible", "base_url": "https://llm-d.test/v1", "model": model}})
    assert r.status_code == 201 and r.json()["is_default"] is True, r.text
    return r.json()["id"]


def generate(c, mid, kind="summary"):
    r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": kind, "instruction": "x"})
    assert r.status_code == 202, r.text
    _drain(c)


def test_external_default_profile_never_overrides_system_local_mode(tmp_path, directory):
    """Регрессия: при системном режиме «Локальная» протокол/резюме из «Истории» шли во внешний профиль, отмеченный «по умолчанию»."""
    ext_seen: list[dict] = []
    rt = Runtime()
    with app_(tmp_path, directory, rt, ext_seen) as c:
        default_ext_profile(c)
        put_settings(c, "llm", provider="local")
        room, mid = meeting_with_two(c, moderators=LEADERS)
        end_by_alice(c, mid)
        login(c, "carol")
        # комната «Системная по умолчанию» — это локальная Qwen
        got = c.get(f"/api/v1/rooms/{room['id']}/manage").json()
        assert got["llm"]["mode"] == "inherit" and got["llm_effective"]["name"].startswith("Qwen3") and got["llm_options"]["system"]["provider"] == "local"
        p = plan(c, mid)
        assert p["llm_local"] is True and p["llm_source"] == "system" and p["llm_ready"] is True
        # История → сформировать резюме, протокол и повторная генерация
        for kind in ("summary", "protocol", "summary"):
            generate(c, mid, kind)
        assert len(rt.seen) >= 3, "документы должны формироваться локальной моделью"
        assert ext_seen == [], "внешний профиль по умолчанию не должен использоваться при системном режиме «Локальная»"


def test_external_default_profile_is_used_only_when_system_mode_is_external(tmp_path, directory):
    ext_seen: list[dict] = []
    rt = Runtime()
    with app_(tmp_path, directory, rt, ext_seen) as c:
        default_ext_profile(c, model="default-ext-model")
        put_settings(c, "llm", provider="external", type="openai_compatible", base_url="https://main.test/v1", model="main-model", api_key="K")
        room, mid = meeting_with_two(c, moderators=LEADERS)
        end_by_alice(c, mid)
        login(c, "carol")
        p = plan(c, mid)
        assert p["llm_local"] is False and p["llm_model"] == "default-ext-model" and p["llm_source"] == "system"
        generate(c, mid)
        assert ext_seen and rt.seen == []
        # режим «Отключено»: профиль по умолчанию ничего не включает
        put_settings(c, "llm", provider="off")
        login(c, "carol")
        assert plan(c, mid)["llm_ready"] is False


def test_precedence_meeting_over_room_over_system(tmp_path, directory):
    ext_seen: list[dict] = []
    rt = Runtime()
    with app_(tmp_path, directory, rt, ext_seen) as c:
        pid = ext_profile(c)
        default_ext_profile(c, model="default-ext-model")
        put_settings(c, "llm", provider="local")
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "carol")
        eff = lambda: c.get(f"/api/v1/meetings/{mid}/settings").json()["llm"]["effective"]   # noqa: E731
        assert eff()["source"] == "system" and eff()["name"].startswith("Qwen3")            # 3. системная
        manage(c, room["id"], llm={"mode": "profile", "profile_id": pid})
        assert eff()["source"] == "room" and eff()["name"] == "Сильная внешняя"             # 2. комната
        c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": {"mode": "local", "local_model": "qwen3-0.6b-q4_k_m"}})
        assert eff()["source"] == "meeting" and eff()["name"].startswith("Qwen3")           # 1. встреча
        c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": {"mode": "off"}})
        assert eff()["source"] == "meeting" and eff()["available"] is False                 # встреча явно «отключена»
        c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": None})
        assert eff()["source"] == "room" and eff()["name"] == "Сильная внешняя"
        manage(c, room["id"], llm={"mode": "inherit"})
        assert eff()["source"] == "system" and eff()["name"].startswith("Qwen3")


def test_unavailable_selected_model_falls_back_to_system_model_not_external_default(tmp_path, directory):
    ext_seen: list[dict] = []
    rt = Runtime()
    with app_(tmp_path, directory, rt, ext_seen) as c:
        pid = ext_profile(c)
        default_ext_profile(c)
        put_settings(c, "llm", provider="local")
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "carol")
        manage(c, room["id"], llm={"mode": "profile", "profile_id": pid})
        login(c, "root")
        assert c.delete(f"/api/v1/admin/api-profiles/{pid}").status_code in (200, 204)
        end_by_alice(c, mid)
        login(c, "carol")
        p = plan(c, mid)
        assert p["llm_local"] is True and "удалён" in (p["llm_note"] or ""), "выбранный профиль удалён → системная (локальная) модель, а не внешний по умолчанию"
        generate(c, mid)
        assert rt.seen and ext_seen == []
        # политика «не формировать»
        put_settings(c, "llm", on_missing="unavailable")
        login(c, "carol")
        p = plan(c, mid)
        assert p["llm_ready"] is False and "удалён" in (p["llm_reason"] or "")


# ------------------------------------------------------------------------------------------------ Qwen3 1.7B: необязательная модель в отдельном контейнере
def _with_17b(monkeypatch, tmp_path):
    """Подставной файл 1.7B с верными для него размером и хешем (настоящий весит 1,3 ГБ)."""
    import hashlib

    content = b"GGUF-1.7B" + bytes(range(200)) * 10
    (tmp_path / "models-llm" / "Qwen3-1.7B-Q4_K_M.gguf").write_bytes(content)
    monkeypatch.setitem(ll.LOCAL_MODELS, ll.QWEN3_17B.id, replace(ll.QWEN3_17B, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))


def test_17b_is_listed_but_off_by_default_and_not_selectable_as_installed(tmp_path, directory, monkeypatch):
    with app_(tmp_path, directory, Runtime()) as c:
        _with_17b(monkeypatch, tmp_path)
        put_settings(c, "llm", provider="local")
        login(c, "root")
        st = c.get("/api/v1/admin/llm/local").json()
        models = {m["id"]: m for m in st["models"]}
        assert set(models) == {"qwen3-0.6b-q4_k_m", "qwen3-1.7b-q4_k_m"}
        assert models["qwen3-0.6b-q4_k_m"]["optional"] is False and models["qwen3-0.6b-q4_k_m"]["enabled_on_server"] is True
        m17 = models["qwen3-1.7b-q4_k_m"]
        assert m17["optional"] is True and m17["enabled_on_server"] is False and m17["file_state"] == "ok" and m17["ready"] is False
        assert "не включена" in m17["runtime"]["detail"]
        room = make_room(c, moderators=LEADERS)
        login(c, "carol")
        opts = c.get(f"/api/v1/rooms/{room['id']}/manage").json()["llm_options"]["local"]
        by = {m["id"]: m for m in opts}
        assert by["qwen3-0.6b-q4_k_m"]["installed"] is True and by["qwen3-1.7b-q4_k_m"]["installed"] is False, "файл есть, но модель на сервере не включена — выбирать её нельзя"


def test_17b_uses_its_own_container_and_falls_back_when_not_enabled(tmp_path, directory, monkeypatch):
    rt06, rt17 = Runtime(reply="Резюме от лёгкой"), Runtime(reply="Резюме от сильной")

    def router(req: httpx.Request) -> httpx.Response:
        return (rt17 if req.url.host == "llm-local-17b" else rt06)(req)

    def build(enabled: str):
        return running_app(make_settings(tmp_path, **model_env(tmp_path), local_llm_17b_enabled=enabled), directory,
                           transports={"local_llm": httpx.MockTransport(router), "anonymizer": httpx.MockTransport(anon_ok)})

    # выключена на сервере: комната, которая её выбрала, получает запасной вариант (системная 0.6B) с понятной пометкой
    with build("no") as c:
        _with_17b(monkeypatch, tmp_path)
        put_settings(c, "llm", provider="local")
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "carol")
        out = manage(c, room["id"], llm={"mode": "local", "local_model": "qwen3-1.7b-q4_k_m"})
        assert out["llm_effective"]["name"].startswith("Qwen3 0.6B") and "не включена на сервере" in (out["llm_effective"]["note"] or "")
        end_by_alice(c, mid)
        login(c, "carol")
        generate(c, mid)
        assert rt06.seen and not rt17.seen
    # включена: запросы идут в контейнер llm-local-17b
    rt06.seen.clear()
    with build("yes") as c:
        _with_17b(monkeypatch, tmp_path)
        put_settings(c, "llm", provider="local")
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "carol")
        out = manage(c, room["id"], llm={"mode": "local", "local_model": "qwen3-1.7b-q4_k_m"})
        assert out["llm_effective"]["name"].startswith("Qwen3 1.7B") and out["llm_effective"]["source"] == "room"
        end_by_alice(c, mid)
        login(c, "carol")
        generate(c, mid)
        assert rt17.seen and not rt06.seen
        assert rt17.hosts and set(rt17.hosts) == {"llm-local-17b:8080/v1/chat/completions"}
        assert rt17.seen[-1]["model"] == "qwen3-1.7b-q4_k_m"
        # системная по умолчанию остаётся 0.6B: комната без выбора её не получает
        room2, mid2 = meeting_with_two(c, moderators=LEADERS)
        login(c, "carol")
        assert c.get(f"/api/v1/rooms/{room2['id']}/manage").json()["llm_effective"]["name"].startswith("Qwen3 0.6B")


def test_17b_url_and_registry_entry():
    m = ll.LOCAL_MODELS["qwen3-1.7b-q4_k_m"]
    assert m.optional and m.service == "llm-local-17b:8080" and m.size_bytes == 1_282_439_584 and len(m.sha256) == 64 and m.file.endswith(".gguf")
    assert ll.DEFAULT_LOCAL_MODEL == "qwen3-0.6b-q4_k_m", "системной по умолчанию остаётся лёгкая модель"
    from app.config import Settings

    local = ll.LocalLlm(Settings())
    assert local.url_for(m) == "http://llm-local-17b:8080" and local.url_for(ll.QWEN3_06B).endswith("llm-local:8080")
