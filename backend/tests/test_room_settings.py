"""Комната как единица настройки: языковая модель (системная → комната → встреча, запасной вариант), рассылка материалов (архив, настройки встречи).
Модели — подключения по API: системное («main-model») и отдельное подключение «Сильная внешняя» («big-model»); какой моделью сформирован документ, видно по запросу."""
from __future__ import annotations

import io
import json
import uuid
import zipfile

import httpx

from app.services.mail_delivery import MailError  # noqa: F401  (проверка импорта)

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_access_and_protocols import end_by_alice, meeting_with_two
from .test_admin_features import _drain
from .test_mail import LEADERS, add_profile, fake_smtp, messages, run_queue  # noqa: F401
from .test_transcripts import _join

MAIN = {"provider": "external", "type": "openai_compatible", "base_url": "https://llm-main.test/v1", "model": "main-model", "api_key": "K"}


def ext_profile(c, name="Сильная внешняя", model="big-model"):
    login(c, "root")
    r = c.post("/api/v1/admin/api-profiles", json={"kind": "llm", "name": name, "secret": "SECRET-X", "config": {"type": "openai_compatible", "base_url": "https://llm-x.test/v1", "model": model}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def app_(tmp_path, directory, seen=None, **over):
    def model(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(req.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "**Резюме** от модели"}}]})

    return running_app(make_settings(tmp_path, **over), directory, transports={"llm": httpx.MockTransport(model)})


def manage(c, room_id, **body):
    r = c.patch(f"/api/v1/rooms/{room_id}/manage", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def plan(c, mid):
    return c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction").json()["plan"]


def default_ext_profile(c, model="default-ext-model"):
    """Подключение, отмеченное «по умолчанию»."""
    login(c, "root")
    r = c.post("/api/v1/admin/api-profiles", json={"kind": "llm", "name": "Внешний по умолчанию", "secret": "SECRET-D", "make_default": True,
                                                    "config": {"type": "openai_compatible", "base_url": "https://llm-d.test/v1", "model": model}})
    assert r.status_code == 201 and r.json()["is_default"] is True, r.text
    return r.json()["id"]


def generate(c, mid, kind="summary"):
    r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": kind, "instruction": "x"})
    assert r.status_code == 202, r.text
    _drain(c)


# ------------------------------------------------------------------------------------------------ модель комнаты
def test_room_exposes_llm_options_and_effective_model(tmp_path, directory):
    with app_(tmp_path, directory) as c:
        pid = ext_profile(c)
        put_settings(c, "llm", **MAIN)
        room = make_room(c, moderators=LEADERS)
        login(c, "carol")                                   # руководитель комнаты
        got = c.get(f"/api/v1/rooms/{room['id']}/manage").json()
        assert got["llm"] == {"mode": "inherit", "profile_id": None} and got["llm_effective"]["source"] == "system"
        opts = got["llm_options"]
        assert opts["system"]["provider"] == "external" and opts["system"]["model"] == "main-model" and "local" not in opts
        assert pid in [p["id"] for p in opts["profiles"]] and "SECRET" not in json.dumps(got)
        assert opts["on_missing"] == "system"


def test_room_can_use_a_profile_off_or_inherit_and_the_removed_local_mode_is_refused(tmp_path, directory):
    seen: list[str] = []
    with app_(tmp_path, directory, seen) as c:
        pid = ext_profile(c)
        put_settings(c, "llm", provider="off")
        room = make_room(c, moderators=LEADERS)
        login(c, "carol")
        out = manage(c, room["id"], llm={"mode": "profile", "profile_id": pid})        # системная «отключена», комната выбрала подключение
        assert out["llm_effective"]["name"] == "Сильная внешняя" and out["llm_effective"]["source"] == "room" and out["llm_effective"]["available"] is True
        mid = _join(c, "alice", room["id"])["meeting_id"]
        end_by_alice(c, mid)
        login(c, "carol")
        p = plan(c, mid)
        assert p["llm_ready"] is True and p["llm_model"] == "big-model" and p["llm_source"] == "room" and "llm_local" not in p
        out = manage(c, room["id"], llm={"mode": "off"})
        assert out["llm_effective"]["available"] is False and "отключена" in out["llm_effective"]["reason"]
        assert plan(c, mid)["llm_ready"] is False
        out = manage(c, room["id"], llm={"mode": "inherit"})
        assert out["llm"]["mode"] == "inherit" and out["llm_effective"]["available"] is False
        for bad in ({"mode": "cloud"}, {"mode": "profile"}, {"mode": "local", "local_model": "qwen3-1.7b-q4_k_m"}):
            assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"llm": bad}).status_code == 422, bad
        login(c, "alice")                                  # обычный участник настройки менять не может
        assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"llm": {"mode": "off"}}).status_code in (403, 404)


def test_deleted_profile_falls_back_to_system_model_with_a_note_or_becomes_unavailable_by_policy(tmp_path, directory):
    seen: list[str] = []
    with app_(tmp_path, directory, seen) as c:
        pid = ext_profile(c)
        default_ext_profile(c)
        put_settings(c, "llm", on_missing="system", **MAIN)
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "carol")
        manage(c, room["id"], llm={"mode": "profile", "profile_id": pid})
        end_by_alice(c, mid)
        login(c, "root")
        assert c.delete(f"/api/v1/admin/api-profiles/{pid}").status_code in (200, 204)
        login(c, "carol")
        p = plan(c, mid)                                   # комната не сломалась: системная модель с пометкой (а не «по умолчанию» среди подключений)
        assert p["llm_ready"] is True and "удалён" in p["llm_note"] and p["llm_source"] == "system"
        generate(c, mid)
        assert set(seen) == {"default-ext-model"}, "системная модель задачи (подключение по умолчанию), а не удалённое"
        put_settings(c, "llm", on_missing="unavailable")
        login(c, "carol")
        p = plan(c, mid)
        assert p["llm_ready"] is False and "удалён" in p["llm_reason"]
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x"}).status_code == 409


# ------------------------------------------------------------------------------------------------ модель и рассылка встречи
def test_meeting_overrides_win_over_room_and_can_be_reset(tmp_path, directory):
    seen: list[str] = []
    with app_(tmp_path, directory, seen) as c:
        pid = ext_profile(c)
        put_settings(c, "llm", **MAIN)
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "alice")
        assert c.get(f"/api/v1/meetings/{mid}/settings").status_code == 404           # участник настройки встречи не видит
        login(c, "carol")
        s = c.get(f"/api/v1/meetings/{mid}/settings").json()
        assert s["llm"]["effective"]["source"] == "system" and s["delivery"]["override"] is False and s["ended"] is False
        s = c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": {"mode": "profile", "profile_id": pid}}).json()
        assert s["llm"]["effective"]["name"] == "Сильная внешняя" and s["llm"]["effective"]["source"] == "meeting" and s["llm"]["room"]["mode"] == "inherit"
        end_by_alice(c, mid)
        login(c, "carol")
        generate(c, mid, "protocol")
        assert seen and set(seen) == {"big-model"}, "для этой встречи — выбранное подключение, а не системное"
        s = c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": None}).json()
        assert s["llm"]["override"] is None and s["llm"]["effective"]["source"] == "system"
        assert c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": {"mode": "magic"}}).status_code == 422
        assert c.put(f"/api/v1/meetings/{mid}/settings", json={"delivery": {"enabled": False, "materials": []}}).status_code == 409


def test_delivery_follows_only_the_room_and_archive_works(client, fake_smtp):
    """С 0.64 автоматическую рассылку решает только комната: своя настройка у встречи отклоняется, письма уходят по комнате, архив — из настроек комнаты."""
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": {"enabled": True, "archive": True, "materials": ["protocol", "summary"],
                                                                                  "recipients": {"leaders": False, "participants": False, "emails": ["room@example.local"]}}})
    mid = _join(client, "alice", room["id"])["meeting_id"]
    _join(client, "bob", room["id"])
    login(client, "carol")
    s = client.get(f"/api/v1/meetings/{mid}/settings").json()
    assert s["delivery"]["effective"]["recipients"]["emails"] == ["room@example.local"] and s["delivery"]["override"] is False
    spec = {"enabled": True, "archive": True, "materials": ["protocol", "summary"], "recipients": {"leaders": False, "participants": False, "emails": ["only-this@example.local"]}}
    assert client.put(f"/api/v1/meetings/{mid}/settings", json={"delivery": spec}).status_code == 409, "своей рассылки у встречи больше нет"
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
    assert {r["recipient"] for r in rows} == {"room@example.local"}, "письма — по настройкам комнаты"
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
    assert set(got["retention"]) == {"text_days", "audio_days", "video_days", "custom", "own", "server", "history_access", "anonymize_mode", "anonymize_profile", "privacy"}
    out = manage(client, room["id"], protocol_instructions="Только решения и сроки")
    assert out["protocol_instructions"] == "Только решения и сроки"
    r = client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"text_retention_days": 1})          # сроки хранения — только администратор
    assert r.status_code in (200, 422) and client.get(f"/api/v1/rooms/{room['id']}/manage").json()["retention"]["text_days"] != 1


# ------------------------------------------------------------------------------------------------ приоритет выбора модели
def test_default_profile_is_used_only_when_the_system_task_has_no_explicit_connection_and_never_turns_off_into_on(tmp_path, directory):
    seen: list[str] = []
    with app_(tmp_path, directory, seen) as c:
        default_ext_profile(c, model="default-ext-model")
        put_settings(c, "llm", **MAIN)
        room, mid = meeting_with_two(c, moderators=LEADERS)
        end_by_alice(c, mid)
        login(c, "carol")
        p = plan(c, mid)
        assert p["llm_model"] == "default-ext-model" and p["llm_source"] == "system"
        generate(c, mid)
        assert set(seen) == {"default-ext-model"}
        put_settings(c, "llm", provider="off")                  # режим «Отключено»: подключение по умолчанию ничего не включает
        login(c, "carol")
        assert plan(c, mid)["llm_ready"] is False


def test_precedence_meeting_over_room_over_system(tmp_path, directory):
    with app_(tmp_path, directory) as c:
        pid = ext_profile(c)
        put_settings(c, "llm", **MAIN)
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "carol")
        eff = lambda: c.get(f"/api/v1/meetings/{mid}/settings").json()["llm"]["effective"]   # noqa: E731
        assert eff()["source"] == "system"                                                  # 3. системная
        manage(c, room["id"], llm={"mode": "profile", "profile_id": pid})
        assert eff()["source"] == "room" and eff()["name"] == "Сильная внешняя"             # 2. комната
        c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": {"mode": "off"}})
        assert eff()["source"] == "meeting" and eff()["available"] is False                 # 1. встреча явно «отключена»
        c.put(f"/api/v1/meetings/{mid}/settings", json={"llm": None})
        assert eff()["source"] == "room" and eff()["name"] == "Сильная внешняя"
        manage(c, room["id"], llm={"mode": "inherit"})
        assert eff()["source"] == "system"


# ------------------------------------------------------------------------------------------------ отдельные модели для протокола и для резюме
def test_summary_and_protocol_have_separate_system_room_and_meeting_chains(tmp_path, directory):
    seen: list[str] = []
    with app_(tmp_path, directory, seen) as c:
        pid = ext_profile(c)
        default_ext_profile(c, model="default-ext-model")
        put_settings(c, "llm", summary_provider="external", **MAIN)
        room, mid = meeting_with_two(c, moderators=LEADERS)
        login(c, "carol")
        got = c.get(f"/api/v1/rooms/{room['id']}/manage").json()
        assert got["llm"]["mode"] == "inherit" and got["llm_summary"]["mode"] == "inherit"
        end_by_alice(c, mid)
        login(c, "carol")
        out = manage(c, room["id"], llm_summary={"mode": "off"})                           # комната меняет только резюме
        assert out["llm_summary_effective"]["available"] is False and "отключена" in out["llm_summary_effective"]["reason"]
        assert out["llm_effective"]["available"] is True and out["llm"]["mode"] == "inherit"
        assert c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction?kind=summary").json()["plan"]["llm_ready"] is False
        assert c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction?kind=protocol").json()["plan"]["llm_ready"] is True
        assert c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"}).status_code == 409
        s = c.put(f"/api/v1/meetings/{mid}/settings", json={"llm_summary": {"mode": "profile", "profile_id": pid}}).json()   # встреча поверх комнаты
        assert s["llm_summary"]["effective"]["source"] == "meeting" and s["llm_summary"]["effective"]["available"] is True and s["llm"]["override"] is None
        seen.clear()
        generate(c, mid, "summary")
        assert set(seen) == {"big-model"}, "для этой встречи резюме пишет выбранное подключение"
        s = c.put(f"/api/v1/meetings/{mid}/settings", json={"llm_summary": None}).json()
        assert s["llm_summary"]["override"] is None and s["llm_summary"]["effective"]["source"] == "room"


def test_system_summary_mode_off_and_same(tmp_path, directory):
    with app_(tmp_path, directory) as c:
        put_settings(c, "llm", summary_provider="off", **MAIN)
        room, mid = meeting_with_two(c, moderators=LEADERS)
        end_by_alice(c, mid)
        login(c, "carol")
        got = c.get(f"/api/v1/rooms/{room['id']}/manage").json()
        assert got["llm_summary_effective"]["available"] is False and got["llm_effective"]["available"] is True
        put_settings(c, "llm", summary_provider="same")
        login(c, "carol")
        got = c.get(f"/api/v1/rooms/{room['id']}/manage").json()
        assert got["llm_summary_effective"]["available"] is True and got["llm_summary_effective"]["name"] == got["llm_effective"]["name"]


def test_rooms_inherit_server_retention_and_an_explicit_value_overrides_it(client):
    """Общие сроки: текст бессрочно, аудио 60, видео 30. Новая комната наследует их; явный срок администратора — свой у комнаты; вернуть общий — признаком."""
    login(client, "root")
    room = make_room(client)
    got = client.get(f"/api/v1/rooms/{room['id']}/manage").json()["retention"]
    assert (got["text_days"], got["audio_days"], got["video_days"]) == (None, 60, 30) and got["custom"] == {"text": False, "audio": False, "video": False}
    r = client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"video_retention_days": 7}).json()
    assert r["video_retention_custom"] is True and r["video_retention_days"] == 7
    got = client.get(f"/api/v1/rooms/{room['id']}/manage").json()["retention"]
    assert got["video_days"] == 7 and got["custom"]["video"] is True and got["server"]["video"] == 30
    assert client.put("/api/v1/admin/settings/general", json={"default_audio_retention_days": 90}).status_code == 200
    assert client.get(f"/api/v1/rooms/{room['id']}/manage").json()["retention"]["audio_days"] == 90, "унаследованный срок следует за общим"
    client.patch(f"/api/v1/admin/rooms/{room['id']}", json={"video_retention_custom": False})
    assert client.get(f"/api/v1/rooms/{room['id']}/manage").json()["retention"]["video_days"] == 30, "вернули общий срок"


def test_retention_policy_helpers():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace as NS

    from app.services import retention_policy as rp
    from app.services.settings import GeneralSettings

    g = GeneralSettings()
    room = NS(text_retention_days=5, audio_retention_days=None, video_retention_days=None, text_retention_custom=True, audio_retention_custom=True, video_retention_custom=False)
    e = rp.effective(room, g)
    assert (e["text"], e["audio"], e["video"]) == (5, None, 30), "свой срок (в том числе «бессрочно») главнее общего"
    later = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert rp.inherited_paused(g.model_copy(update={"retention_cleanup_from": later})) is True
    assert rp.inherited_paused(g.model_copy(update={"retention_cleanup_from": "2000-01-01T00:00:00+00:00"})) is False
    assert rp.inherited_paused(g) is False and rp.inherited_paused(g.model_copy(update={"retention_cleanup_from": "мусор"})) is False
