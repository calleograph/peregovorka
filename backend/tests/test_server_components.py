"""Компоненты сервера из администрирования: белый список, совпадающий с помощником на хосте; состояние по components.json; подтверждение, права,
конфликты и аудит; проверки видеозаписи и трансляций по цепочке зависимостей."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from app.services import server_components as sc
from app.services import updates as upd

from .conftest import login, put_settings
from .test_updates import chan, heartbeat

REPO = Path(__file__).resolve().parents[2]
C = "/api/v1/admin/components"


def facts(d: Path, *, video=False, sip=False, redis=None, egress="running", health="healthy", image=True, **extra):
    f = {"ts": time.time(), "docker": True, "free_gb": 50, "env": {"video_enabled": video, "sip_enabled": sip, "livekit_redis": video or sip if redis is None else redis},
         "images": {"egress": image, "sip": True}, "containers": {"livekit": {"state": "running", "health": "healthy", "restarts": 0},
                                                               "livekit-egress": {"state": egress, "health": health, "restarts": 0} if egress else {"state": "absent"},
                                                               "livekit-sip": {"state": "absent"}, "web": {"state": "running", "health": "healthy", "started_at": "2026-10-30T08:00:00.0Z"},
                                                               "backend": {"state": "running", "health": "healthy"}}}
    f.update(extra)
    (d / "components.json").write_text(json.dumps(f), encoding="utf-8")


def helper_ok(d: Path, **over):
    heartbeat(d, uid=0, **over)
    (d / "helper.json").write_text(json.dumps({"ts": time.time(), "pid": 1, "uid": 0, "user": "root", "under_systemd": True, "unit": "peregovorka-updater-t", "stopped": False}))


def test_whitelist_matches_the_host_helper():
    text = (REPO / "scripts/lib/complib.sh").read_text(encoding="utf-8")
    shell = re.search(r"^COMPONENT_OPS=\(([^)]*)\)", text, re.M).group(1).split()
    assert tuple(shell) == upd.COMPONENT_OPS
    for op in shell:                                     # у каждого действия есть исполнение на хосте
        assert re.search(rf"^comp_apply_{op}\(\)", text, re.M), op


def test_state_follows_the_helper_facts_not_the_stale_env(client):
    d, s = chan(client), client.app_obj.state.settings
    ch = sc.channel_for(s)
    assert sc.describe("video", None, s)["state"] == "unknown"
    facts(d, video=False)
    v = sc.describe("video", sc.facts(ch), s)
    assert v["state"] == "off" and [o["id"] for o in v["ops"]] == ["video_enable"] and v["ops"][0]["primary"]
    assert sc.enabled_on_server(s, ch, "video") is False
    facts(d, video=True, image=False)
    assert sc.describe("video", sc.facts(ch), s)["message"].startswith("Установка не завершена: образ")
    facts(d, video=True, redis=False)
    assert "общей шине" in sc.describe("video", sc.facts(ch), s)["message"]
    facts(d, video=True, egress="exited")
    v = sc.describe("video", sc.facts(ch), s)
    assert v["state"] == "error" and v["ops"][0]["id"] == "video_restart" and {"video_enable", "video_disable"} <= {o["id"] for o in v["ops"]}
    facts(d, video=True)
    assert sc.describe("video", sc.facts(ch), s)["state"] == "ok"
    assert sc.enabled_on_server(s, ch, "video") is True, "включено из панели: backend видит это без перезапуска"


def test_actions_need_admin_confirmation_a_free_helper_and_are_audited(client):
    d = chan(client)
    facts(d, video=False)
    login(client, "alice")
    assert client.get(C).status_code == 403 and client.post(f"{C}/video/video_enable", json={"confirm": True}).status_code == 403
    login(client, "root")
    assert client.post(f"{C}/video/rm -rf", json={"confirm": True}).status_code in (404, 405)
    assert client.post(f"{C}/shell/video_enable", json={"confirm": True}).status_code == 404
    assert client.post(f"{C}/video/video_disable", json={"confirm": True}).status_code == 409, "выключить выключенное нельзя"
    r = client.post(f"{C}/video/video_enable", json={})
    assert r.status_code == 400 and any("LiveKit" in x for x in r.json()["detail"]["consequences"]), "без подтверждения — последствия, а не запуск"
    assert client.post(f"{C}/video/video_enable", json={"confirm": True}).status_code == 409, "помощник не работает — действие не принимается"
    helper_ok(d)
    r = client.post(f"{C}/video/video_enable", json={"confirm": True})
    assert r.status_code == 200, r.text
    req = (d / "request.txt").read_text()
    assert "action=component" in req and "op=video_enable" in req and f"id={r.json()['request_id']}" in req
    assert client.post(f"{C}/video/video_enable", json={"confirm": True}).status_code == 409, "запрос уже ждёт — второй не принимается"
    (d / "request.txt").unlink()
    helper_ok(d, state="component", action="component", repair_id="video_enable")
    assert client.post(f"{C}/video/video_enable", json={"confirm": True}).status_code == 409, "идёт действие — конфликт"
    info = client.get(C).json()
    assert info["helper"]["busy"] and info["helper"]["last"]["running"] and {i["id"] for i in info["items"]} == {"video", "sip", "livekit", "web"}
    acts = [a for a in client.get("/api/v1/admin/audit").json() if a["action"] == "system.component"]
    assert acts and acts[0]["target_id"] == "video:video_enable"


def test_video_checks_show_one_cause_when_the_subsystem_is_off(client):
    d = chan(client)
    facts(d, video=False)
    login(client, "root")
    st = client.get("/api/v1/admin/video/status").json()
    by = {c["id"]: c for c in st["checks"]}
    assert by["server"]["status"] == "off" and by["server"]["action"]["op"] == "video_enable"
    assert by["egress"]["status"] == "skipped", "зависимая проверка не выдаётся за ошибку"
    assert not any("Egress" in w or "записи подключена" in w for w in st["warnings"]), "выключенная подсистема — не ошибка (LiveKit в тестах не запущен: его проверка честно падает)"
    put_settings(client, "live", enabled=True)
    lv = client.get("/api/v1/admin/live/status").json()
    lby = {c["id"]: c for c in lv["checks"]}
    assert lby["server"]["status"] == "off" and lby["egress"]["status"] == "skipped"
    assert lby["health"]["status"] in ("info", "skipped") and lby["secret"]["status"] in ("ok", "error")


def test_failed_image_pull_shows_the_real_reason_not_a_guess_about_disk(client):
    d, s = chan(client), client.app_obj.state.settings
    ch = sc.channel_for(s)
    err = {"ts": 1793349775, "code": "timeout", "summary": "Реестр доступен, но передача слоёв образа оборвалась по таймауту.", "stage": "основной источник",
           "image": "livekit/egress:v1.15.0@sha256:" + "a" * 64, "tried": "основной (таймаут); mirror.gcr.io (нет образа)",
           "details": "net/http: timeout awaiting response headers\nhttps://cdn.example/x?X-Amz-Signature=SECRETSIG&a=1\nAuthorization: Bearer abcdefghijklmnop1234"}
    facts(d, video=True, image=False, image_errors={"egress": err, "sip": None}, free_gb=44)
    v = sc.describe("video", sc.facts(ch), s)
    assert v["state"] == "error" and "таймауту" in v["message"] and "диск" not in v["message"].lower()
    assert v["ops"][0]["id"] == "video_enable" and v["ops"][0]["label"] == "Повторить установку" and v["ops"][0]["primary"]
    f = v["failure"]
    assert f["code"] == "timeout" and f["stage"] == "основной источник" and "mirror.gcr.io" in f["tried"]
    assert "SECRETSIG" not in f["details"] and "abcdefghijklmnop1234" not in f["details"] and "a=1" in f["details"], "секреты скрываются повторно и на стороне backend"


def test_enabled_but_service_never_created_is_a_partial_install_with_a_safe_continue(client):
    d, s = chan(client), client.app_obj.state.settings
    ch = sc.channel_for(s)
    facts(d, video=True, egress=None)
    v = sc.describe("video", sc.facts(ch), s)
    assert v["state"] == "error" and "не завершена" in v["message"]
    assert [o["id"] for o in v["ops"]] == ["video_enable", "video_disable"] and v["ops"][0]["primary"] and v["ops"][0]["label"] == "Продолжить установку"


def test_image_from_the_mirror_is_reported_as_a_note_on_a_working_service(client):
    d, s = chan(client), client.app_obj.state.settings
    ch = sc.channel_for(s)
    facts(d, video=True, image_sources={"egress": "mirror", "sip": "primary"})
    v = sc.describe("video", sc.facts(ch), s)
    assert v["state"] == "ok" and "зеркала" in v["note"]
    facts(d, video=True, image_sources={"egress": "primary", "sip": "primary"})
    assert sc.describe("video", sc.facts(ch), s)["note"] is None
