"""SIP-телефония через LiveKit SIP: профили (транки), синхронизация, проверка, тестовый вызов, звонки из комнаты, входящие, участники-абоненты, состояние.
LiveKit API подменён (FakeLK); адреса и номера вымышленные (example.local, 192.0.2.0/24, +7000…). Пароли не должны попадать никуда, кроме запроса к LiveKit."""
from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace

import httpx

from .conftest import OTHER_GROUP, login, make_room, make_settings, running_app
from .test_access_and_protocols import end_by_alice
from .test_transcripts import _feed_and_consume, _join, _segment

SECRET = "Sip-Pass-7x!"
PROFILE = {"name": "Asterisk (проверенный вариант)", "host": "pbx.example.local", "port": 5060, "transport": "udp", "direction": "both", "username": "peregovorka",
           "secret": SECRET, "caller_id": "+70001112233", "allowed_numbers": ["+7", "8", "2"], "allowed_addresses": ["192.0.2.0/24"], "codecs": ["PCMU", "PCMA"],
           "realm": "pbx.example.local", "inbound_numbers": ["100"]}
LEADERS = [{"subject_type": "group", "subject_ref": OTHER_GROUP, "display_name": "Руководители"}]


class FakeLK:
    """Подставной LiveKit API: запоминает вызовы, выдаёт идентификаторы."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.out: dict[str, object] = {}
        self.inn: dict[str, object] = {}
        self.rules: dict[str, dict] = {}
        self.dial_error: Exception | None = None
        self.list_error: Exception | None = None
        self.n = 0
        self.sip = self.Sip(self)
        self.room = self.Room(self)

    def factory(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _id(self, p: str) -> str:
        self.n += 1
        return f"{p}_{self.n}"

    class Sip:
        def __init__(self, o):
            self.o = o

        async def create_sip_outbound_trunk(self, req):
            i = self.o._id("ST_O"); self.o.out[i] = req.trunk; self.o.calls.append(("create_out", i, req.trunk)); return SimpleNamespace(sip_trunk_id=i)

        async def update_sip_outbound_trunk(self, i, info):
            if i not in self.o.out:
                raise RuntimeError("not found")
            self.o.out[i] = info; self.o.calls.append(("update_out", i, info)); return SimpleNamespace(sip_trunk_id=i)

        async def create_sip_inbound_trunk(self, req):
            i = self.o._id("ST_I"); self.o.inn[i] = req.trunk; self.o.calls.append(("create_in", i, req.trunk)); return SimpleNamespace(sip_trunk_id=i)

        async def update_sip_inbound_trunk(self, i, info):
            if i not in self.o.inn:
                raise RuntimeError("not found")
            self.o.inn[i] = info; self.o.calls.append(("update_in", i, info)); return SimpleNamespace(sip_trunk_id=i)

        async def delete_sip_trunk(self, req):
            self.o.out.pop(req.sip_trunk_id, None); self.o.inn.pop(req.sip_trunk_id, None); self.o.calls.append(("delete_trunk", req.sip_trunk_id))

        async def list_sip_outbound_trunk(self, req):
            if self.o.list_error:
                raise self.o.list_error
            return SimpleNamespace(items=[SimpleNamespace(sip_trunk_id=i) for i in self.o.out])

        async def list_sip_inbound_trunk(self, req):
            return SimpleNamespace(items=[SimpleNamespace(sip_trunk_id=i) for i in self.o.inn])

        async def create_sip_dispatch_rule(self, req):
            i = self.o._id("SDR"); info = req.dispatch_rule
            self.o.rules[i] = {"room": info.rule.dispatch_rule_direct.room_name, "numbers": list(info.inbound_numbers), "trunks": list(info.trunk_ids)}
            self.o.calls.append(("create_rule", i, self.o.rules[i])); return SimpleNamespace(sip_dispatch_rule_id=i)

        async def update_sip_dispatch_rule(self, i, info):
            if i not in self.o.rules:
                raise RuntimeError("not found")
            self.o.rules[i] = {"room": info.rule.dispatch_rule_direct.room_name, "numbers": list(info.inbound_numbers), "trunks": list(info.trunk_ids)}
            self.o.calls.append(("update_rule", i, self.o.rules[i])); return SimpleNamespace(sip_dispatch_rule_id=i)

        async def delete_sip_dispatch_rule(self, req):
            self.o.rules.pop(req.sip_dispatch_rule_id, None); self.o.calls.append(("delete_rule", req.sip_dispatch_rule_id))

        async def create_sip_participant(self, req, timeout=None):
            self.o.calls.append(("dial", req.sip_trunk_id, req.sip_call_to, req.room_name, req.participant_identity, req.participant_name, req.wait_until_answered))
            if self.o.dial_error:
                raise self.o.dial_error
            return SimpleNamespace(participant_identity=req.participant_identity)

    class Room:
        def __init__(self, o):
            self.o = o

        async def delete_room(self, req):
            self.o.calls.append(("delete_room", req.room))

        async def remove_participant(self, req):
            self.o.calls.append(("remove", req.room, req.identity))


class SipErr(Exception):
    sip_status_code = 486
    sip_status = "Busy Here"

    def __str__(self):
        return "SIP call failed: 486 Busy Here"


def app_sip(tmp_path, directory, lk: FakeLK, enabled: bool = True, health: int = 200):
    def health_h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(health, text="ok")

    app = running_app(make_settings(tmp_path, sip_enabled="yes" if enabled else "no", sip_allowed_cidrs="192.0.2.0/24"), directory, transports={"sip_health": httpx.MockTransport(health_h)})
    return app, lk


class Ctx:
    def __init__(self, tmp_path, directory, lk, **kw):
        self.cm, self.lk = app_sip(tmp_path, directory, lk, **kw)[0], lk

    def __enter__(self):
        c = self.cm.__enter__()
        c.app_obj.state.sip_gateway._factory = self.lk.factory
        return c

    def __exit__(self, *a):
        return self.cm.__exit__(*a)


def make_profile(c, **over):
    login(c, "root")
    r = c.post("/api/v1/admin/sip/profiles", json={**PROFILE, **over})
    assert r.status_code == 201, r.text
    return r.json()


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


# ------------------------------------------------------------------------------------------------ профили
def test_profile_crud_validation_and_secret_never_returned(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        login(c, "alice")
        assert c.get("/api/v1/admin/sip/profiles").status_code == 403
        p = make_profile(c)
        assert p["secret_set"] is True and "secret" not in p and SECRET not in json.dumps(p)
        assert p["is_default"] is True and p["synced"] is True and p["sync"]["ok"] is True          # первый профиль — по умолчанию
        out = next(t for k, t in lk.out.items())
        assert out.address == "pbx.example.local:5060" and out.auth_username == "peregovorka" and out.auth_password == SECRET and list(out.numbers) == ["+70001112233"]
        inb = next(t for k, t in lk.inn.items())
        assert list(inb.allowed_addresses) == ["192.0.2.0/24"] and list(inb.numbers) == ["100"] and inb.auth_realm == "pbx.example.local"
        assert [cd.name for cd in out.media.codecs] == ["PCMU", "PCMA"] and out.media.only_listed_codecs is True
        listing = c.get("/api/v1/admin/sip/profiles").json()["items"]
        assert SECRET not in json.dumps(listing) and listing[0]["allowed_numbers"] == ["+7", "8", "2"]
        # проверка значений
        for bad in ({"name": ""}, {"host": "pbx example"}, {"port": 70000}, {"transport": "ws"}, {"direction": "x"}, {"codecs": ["MP3"]}, {"allowed_addresses": ["не-адрес"]},
                    {"allowed_numbers": ["abc"]}, {"caller_id": "ab"}, {"ring_timeout_s": 1}, {"media_encryption": "maybe"}):
            r = c.post("/api/v1/admin/sip/profiles", json={**PROFILE, "name": "Другой", **bad})
            assert r.status_code == 422, (bad, r.text)
        assert c.post("/api/v1/admin/sip/profiles", json={**PROFILE}).status_code == 422                   # повтор названия
        # адрес вместе с портом и схемой нормализуется
        r = c.post("/api/v1/admin/sip/profiles", json={"name": "Второй", "host": "sip:pbx2.example.local:5070", "secret": "x"})
        assert r.status_code == 201 and r.json()["host"] == "pbx2.example.local" and r.json()["port"] == 5070 and r.json()["is_default"] is False
        # смена профиля по умолчанию — ровно один
        c.patch(f"/api/v1/admin/sip/profiles/{r.json()['id']}", json={"is_default": True})
        assert [x["name"] for x in c.get("/api/v1/admin/sip/profiles").json()["items"] if x["is_default"]] == ["Второй"]
        # пароль меняется только по явной просьбе
        n = len(lk.calls)
        c.patch(f"/api/v1/admin/sip/profiles/{p['id']}", json={"port": 5061})
        assert [t.auth_password for t in lk.out.values()].count(SECRET) >= 1 and len(lk.calls) > n
        c.patch(f"/api/v1/admin/sip/profiles/{p['id']}", json={"direction": "outbound"})            # входящий транк больше не нужен
        assert any(k[0] == "delete_trunk" for k in lk.calls)
        # аудит и журнал — без пароля
        c.portal.call(c.app_obj.state.journal.flush)
        audit = c.get("/api/v1/admin/audit").json()
        assert {"sip.profile.create", "sip.profile.update"} <= {a["action"] for a in audit} and SECRET not in json.dumps(audit)
        assert SECRET not in json.dumps(c.get("/api/v1/admin/journal", params={"limit": 200}).json())


def test_profile_saved_even_when_telephony_is_off_on_server_or_livekit_fails(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk, enabled=False) as c:
        p = make_profile(c)
        assert p["synced"] is False and p["sync"]["ok"] is None and "выключена на сервере" in p["sync"]["message"] and lk.calls == []
    lk2 = FakeLK()
    lk2.sip.create_sip_outbound_trunk = None  # type: ignore[assignment]  # LiveKit без SIP: любой вызов падает
    with Ctx(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, directory, lk2) as c:
        p = make_profile(c)
        assert p["synced"] is False and p["sync"]["ok"] is False and p["name"]


def test_delete_profile_removes_trunks_and_is_blocked_while_a_room_uses_it(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        p = make_profile(c)
        room = make_room(c, moderators=LEADERS)
        r = c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"sip": {"mode": "profile", "profile_id": p["id"], "allow_outbound": True}})
        assert r.status_code == 200, r.text
        login(c, "root")
        d = c.delete(f"/api/v1/admin/sip/profiles/{p['id']}")
        assert d.status_code == 409 and room["name"] in d.json()["detail"]
        c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"sip": {"mode": "off"}})
        assert c.delete(f"/api/v1/admin/sip/profiles/{p['id']}").status_code == 204
        assert not lk.out and not lk.inn


# ------------------------------------------------------------------------------------------------ проверка и тестовый вызов
def test_check_reports_each_stage_with_a_clear_reason(tmp_path, directory, monkeypatch):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        p = make_profile(c)
        monkeypatch.setattr("app.api.admin_sip.sip_options_probe", lambda h, pt, tr: {"ok": True, "message": "АТС отвечает: SIP 200 OK", "status": 200, "ms": 4})
        r = c.post(f"/api/v1/admin/sip/profiles/{p['id']}/check").json()
        assert r["ok"] is True and [s["stage"] for s in r["stages"]] == ["config", "pbx", "livekit", "service"]
        assert next(s for s in r["stages"] if s["stage"] == "livekit")["ok"] is True
        monkeypatch.setattr("app.api.admin_sip.sip_options_probe", lambda h, pt, tr: {"ok": False, "message": "АТС не ответила на SIP OPTIONS за 3 с", "status": None, "ms": 3000})
        lk.out.clear()                                        # транк удалили вручную в LiveKit
        r = c.post(f"/api/v1/admin/sip/profiles/{p['id']}/check").json()
        assert r["ok"] is False
        by = {s["stage"]: s for s in r["stages"]}
        assert by["pbx"]["ok"] is False and "не ответила" in by["pbx"]["message"] and by["livekit"]["ok"] is False and "Синхронизировать" in by["livekit"]["message"]
        assert c.get("/api/v1/admin/sip/profiles").json()["items"][0]["last_check"]["ok"] is False
        assert c.post(f"/api/v1/admin/sip/profiles/{p['id']}/sync").json()["ok"] is True          # синхронизация возвращает транки
        lk.list_error = RuntimeError("sip not enabled")
        by = {s["stage"]: s for s in c.post(f"/api/v1/admin/sip/profiles/{p['id']}/check").json()["stages"]}
        assert by["livekit"]["ok"] is False and "не запущена" in by["livekit"]["message"]


def test_sip_options_probe_classifies_replies_without_network(monkeypatch):
    from app.services import sip as s

    class Sock:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, t):
            pass

        def sendto(self, d, a):
            assert d.startswith(b"OPTIONS sip:") and b"CSeq: 1 OPTIONS" in d

        def recvfrom(self, n):
            return b"SIP/2.0 401 Unauthorized\r\nVia: x\r\n\r\n", ("192.0.2.1", 5060)

    monkeypatch.setattr(s.socket, "getaddrinfo", lambda *a, **k: [(2, 2, 17, "", ("192.0.2.1", 5060))])
    monkeypatch.setattr(s.socket, "socket", Sock)
    r = s.sip_options_probe("pbx.example.local", 5060, "udp")
    assert r["ok"] is True and r["status"] == 401 and "авторизацию" in r["message"]
    monkeypatch.setattr(Sock, "recvfrom", lambda self, n: (_ for _ in ()).throw(s.socket.timeout()))
    r = s.sip_options_probe("pbx.example.local", 5060, "udp")
    assert r["ok"] is False and "не ответила" in r["message"]
    monkeypatch.setattr(s.socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("dns")))
    assert "DNS" in s.sip_options_probe("nope.example.local", 5060, "udp")["message"]


def test_test_call_ok_and_failures_are_explained(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        p = make_profile(c)
        r = c.post(f"/api/v1/admin/sip/profiles/{p['id']}/test-call", json={"number": "+7 (000) 111-22-33"}).json()
        assert r["ok"] is True
        dial = next(k for k in lk.calls if k[0] == "dial")
        assert dial[2] == "+70001112233" and dial[3].startswith("sip-test-") and dial[6] is True
        assert any(k[0] == "delete_room" and k[1] == dial[3] for k in lk.calls), "временная комната удаляется"
        lk.dial_error = SipErr()
        r = c.post(f"/api/v1/admin/sip/profiles/{p['id']}/test-call", json={"number": "100"}).json()
        assert r["ok"] is False and "занят" in r["message"] and r["sip_status"] == 486 and "SIP 486" in r["message"]
        assert c.post(f"/api/v1/admin/sip/profiles/{p['id']}/test-call", json={"number": "abc"}).status_code == 422
        audit = json.dumps(c.get("/api/v1/admin/audit").json())
        assert "sip.profile.test_call" in audit and "+70001112233" not in audit and SECRET not in audit          # номер в аудите частично скрыт


# ------------------------------------------------------------------------------------------------ состояние
def test_status_when_off_when_running_and_when_livekit_is_unreachable(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk, enabled=False) as c:
        login(c, "root")
        st = c.get("/api/v1/admin/sip/status").json()
        assert st["enabled_on_server"] is False and "выключена" in st["service"]["detail"] and st["ports"]["signaling_port"] == 5060 and st["ports"]["rtp_start"] == 20000
    with Ctx(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, directory, FakeLK()) as c:
        p = make_profile(c)
        st = c.get("/api/v1/admin/sip/status").json()
        assert st["enabled_on_server"] is True and st["service"]["running"] is True and st["livekit"]["ok"] is True and st["redis"]["ok"] is True
        assert st["active_trunk"] == p["name"] and st["trunks"]["synced"] == 1 and st["ports"]["allowed_cidrs"] == ["192.0.2.0/24"]
        c.app_obj.state.sip_gateway._factory = lambda: (_ for _ in ()).throw(RuntimeError("down"))
        st = c.get("/api/v1/admin/sip/status").json()
        assert st["livekit"]["ok"] is False
    with Ctx(tmp_path / "y" if (tmp_path / "y").mkdir() is None else tmp_path, directory, FakeLK(), health=503) as c:
        login(c, "root")
        assert c.get("/api/v1/admin/sip/status").json()["service"]["running"] is False


def test_diagnostics_lists_sip_and_off_is_not_a_failure(tmp_path, directory):
    from app.services.diagnostics import build_report

    with Ctx(tmp_path, directory, FakeLK(), enabled=False) as c:
        rep = c.portal.call(lambda: build_report(c.app_obj))
        assert rep["checks"]["sip"]["ok"] is True and rep["checks"]["sip"]["configured"] is False


# ------------------------------------------------------------------------------------------------ настройки комнаты
def test_room_telephony_settings_are_validated(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        p = make_profile(c)
        a, b = make_room(c, moderators=LEADERS), make_room(c, moderators=LEADERS)
        login(c, "carol")
        got = c.get(f"/api/v1/rooms/{a['id']}/manage").json()
        assert got["sip"]["mode"] == "off" and got["sip_options"]["server_enabled"] is True and got["sip_options"]["profiles"][0]["name"] == p["name"] and got["sip_options"]["default"] == p["name"]
        ok = c.patch(f"/api/v1/rooms/{a['id']}/manage", json={"sip": {"mode": "default", "extension": "201", "allow_inbound": True, "allow_outbound": True,
                                                                          "contacts": [{"name": "Секретарь", "number": "+7 000 111-22-33"}]}})
        assert ok.status_code == 200 and ok.json()["sip"]["extension"] == "201" and ok.json()["sip"]["contacts"] == [{"name": "Секретарь", "number": "+70001112233"}]
        bad = lambda **s: c.patch(f"/api/v1/rooms/{b['id']}/manage", json={"sip": s})  # noqa: E731
        assert bad(mode="default", extension="201").status_code == 409                                     # номер занят другой комнатой
        assert bad(mode="default", allow_inbound=True).status_code == 422                                  # для входящих нужен внутренний номер
        assert bad(mode="default", extension="12ab").status_code == 422
        assert bad(mode="profile", profile_id=str(uuid.uuid4())).status_code == 422                        # профиль не существует
        assert bad(mode="telepathy").status_code == 422
        assert bad(mode="default", contacts=[{"name": "x", "number": "не номер"}]).status_code == 422
        login(c, "alice")
        assert c.patch(f"/api/v1/rooms/{a['id']}/manage", json={"sip": {"mode": "off"}}).status_code in (403, 404)


# ------------------------------------------------------------------------------------------------ звонок из комнаты
def room_ready(c, **sip):
    p = make_profile(c)
    room = make_room(c, moderators=LEADERS)
    login(c, "carol")
    r = c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"sip": {"mode": "default", "allow_outbound": True, **sip}})
    assert r.status_code == 200, r.text
    return p, room


def test_outbound_call_adds_a_phone_participant_visible_and_transcribed(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        _p, room = room_ready(c)
        mid = _join(c, "alice", room["id"])["meeting_id"]
        login(c, "carol")
        st = c.get(f"/api/v1/rooms/{room['id']}/phone").json()
        assert st["can_call"] is True and st["active_meeting_id"] == mid and st["allowed_prefixes"] == ["+7", "8", "2"]
        r = c.post(f"/api/v1/rooms/{room['id']}/phone/call", json={"number": "+7 (000) 111-22-33"})
        assert r.status_code == 200, r.text
        res = r.json()
        dial = next(k for k in lk.calls if k[0] == "dial")
        assert dial[2] == "+70001112233" and dial[3] == f"m-{uuid.UUID(mid).hex}" and dial[4] == res["identity"] and res["identity"].startswith("p-")
        assert dial[5] == "Телефон: +70001112233" and dial[1] in lk.out
        # участник виден в списке встречи как телефон, а не гость
        login(c, "root")
        parts = c.get(f"/api/v1/meetings/{mid}").json()["participants"]
        phone = [x for x in parts if x["participant_type"] == "phone"]
        assert phone and phone[0]["display_name"] == "Телефон: +70001112233" and "(гость)" not in phone[0]["display_name"] and phone[0]["online"] is True
        # его речь попадает в стенограмму под его именем
        _feed_and_consume(c, [_segment(mid, res["identity"], "Алло, я на связи")])
        row = next(s for s in c.get(f"/api/v1/meetings/{mid}/transcript").json()["segments"] if "на связи" in s["text"])
        assert row["display_name"] == "Телефон: +70001112233" and row["guest_id"] == res["guest_id"] and row["identity"] == res["identity"]
        # отключение абонента
        login(c, "carol")
        assert c.post(f"/api/v1/rooms/{room['id']}/phone/hangup", json={"guest_id": res["guest_id"]}).status_code == 200
        assert ("remove", f"m-{uuid.UUID(mid).hex}", res["identity"]) in lk.calls
        assert c.get(f"/api/v1/rooms/{room['id']}/phone").json()["phones"] == []
        # аудит и журнал: звонок и отключение записаны, номер скрыт частично, пароля нет
        c.portal.call(c.app_obj.state.journal.flush)
        login(c, "root")
        audit = json.dumps(c.get("/api/v1/admin/audit").json(), ensure_ascii=False)
        journal = json.dumps(c.get("/api/v1/admin/journal", params={"limit": 200, "category": "sip"}).json(), ensure_ascii=False)
        assert "room.phone.call" in audit and "room.phone.hangup" in audit and "+70001112233" not in audit and "+70001112233" not in journal and SECRET not in audit + journal


def test_outbound_call_rules_and_errors(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        _p, room = room_ready(c)
        login(c, "carol")
        assert c.post(f"/api/v1/rooms/{room['id']}/phone/call", json={"number": "+70001112233"}).status_code == 409        # встреча не идёт
        mid = _join(c, "alice", room["id"])["meeting_id"]                                                            # noqa: F841
        login(c, "carol")
        call = lambda n: c.post(f"/api/v1/rooms/{room['id']}/phone/call", json={"number": n})  # noqa: E731
        assert call("9999").status_code == 403 and "допустим" in call("9999").json()["detail"]                       # вне списка допустимых префиксов
        assert call("abc").status_code == 422 and c.post(f"/api/v1/rooms/{room['id']}/phone/call", json={}).status_code == 422
        lk.dial_error = SipErr()
        r = call("+70001112233")
        assert r.status_code == 502 and "Абонент занят" in r.json()["detail"] and "SIP 486" in r.json()["detail"]
        assert c.get(f"/api/v1/rooms/{room['id']}/phone").json()["phones"] == []                                      # неудавшийся вызов не оставляет участника
        lk.dial_error = None
        # сохранённый номер по индексу
        c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"sip": {"contacts": [{"name": "Офис", "number": "+70001110000"}]}})
        assert c.post(f"/api/v1/rooms/{room['id']}/phone/call", json={"contact": 0}).status_code == 200
        # запрещённые исходящие и отключённая телефония
        c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"sip": {"allow_outbound": False}})
        st = c.get(f"/api/v1/rooms/{room['id']}/phone").json()
        assert st["can_call"] is False and "не разрешены" in st["reason"]
        assert call("+70001112233").status_code == 403
        c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"sip": {"mode": "off"}})
        assert "отключена" in c.get(f"/api/v1/rooms/{room['id']}/phone").json()["reason"]
        login(c, "bob")
        assert c.get(f"/api/v1/rooms/{room['id']}/phone").status_code == 404                                          # не руководитель


# ------------------------------------------------------------------------------------------------ входящие
def test_inbound_rule_lives_while_the_meeting_is_active_and_phone_guest_is_registered(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        p, room = room_ready(c, extension="201", allow_inbound=True)
        mid = _join(c, "alice", room["id"])["meeting_id"]
        assert wait_for(lambda: bool(lk.rules)), "при начале встречи создаётся правило входящих"
        rid, rule = next(iter(lk.rules.items()))
        trunk = c.get("/api/v1/admin/sip/profiles").json()["items"][0]["lk_inbound_trunk_id"] if login(c, "root") is not None else None
        assert rule["room"] == f"m-{uuid.UUID(mid).hex}" and rule["numbers"] == ["201"] and rule["trunks"] == [trunk]
        # входящий звонок: LiveKit сам выдал identity sip_… — запись участника заводится по webhook
        async def inbound():
            async with c.app_obj.state.session_maker() as db:
                g = await c.app_obj.state.sip_routing.register_inbound(db, uuid.UUID(mid), "sip_+70009998877_AbC", {"sip.phoneNumber": "+70009998877"}, "")
                await db.commit()
                return str(g.id), g.display_name

        gid, name = c.portal.call(inbound)
        assert name == "Телефон: +70009998877"
        login(c, "root")
        parts = c.get(f"/api/v1/meetings/{mid}").json()["participants"]
        assert any(x["participant_type"] == "phone" and x["display_name"] == name for x in parts)
        _feed_and_consume(c, [_segment(mid, "sip_+70009998877_AbC", "Добрый день, это входящий")])
        text = json.dumps(c.get(f"/api/v1/meetings/{mid}/transcript").json(), ensure_ascii=False)
        assert "входящий" in text
        # после встречи правило снимается: звонок на номер комнаты без встречи не попадёт в «мёртвую» комнату
        end_by_alice(c, mid)
        assert wait_for(lambda: not lk.rules), "правило входящих снято"
        assert any(k[0] == "delete_rule" for k in lk.calls)


def test_disabling_inbound_or_telephony_drops_the_rule(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        _p, room = room_ready(c, extension="202", allow_inbound=True)
        _join(c, "alice", room["id"])
        assert wait_for(lambda: bool(lk.rules))
        login(c, "carol")
        assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"sip": {"allow_inbound": False}}).status_code == 200
        assert not lk.rules


def test_phone_participants_are_not_kicked_with_the_guest_link(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        _p, room = room_ready(c)
        mid = _join(c, "alice", room["id"])["meeting_id"]  # noqa: F841
        login(c, "carol")
        res = c.post(f"/api/v1/rooms/{room['id']}/phone/call", json={"number": "+70001112233"}).json()
        r = c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"guest_access_enabled": False})
        assert r.status_code == 200
        assert c.get(f"/api/v1/rooms/{room['id']}/phone").json()["phones"][0]["guest_id"] == res["guest_id"], "отзыв гостевой ссылки не отключает телефонных участников"


def test_numbers_helpers():
    from app.services.sip import explain_sip_status, mask_number, normalize_number, number_allowed

    assert normalize_number("+7 (000) 111-22-33") == "+70001112233" and normalize_number("*98") == "*98"
    for bad in ("", "abc", "+7;rm", "1" * 40):
        try:
            normalize_number(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert number_allowed([], "123") and number_allowed(["+7", "8*"], "+7000") and number_allowed(["+7", "8*"], "8123") and not number_allowed(["+7"], "9000")
    assert mask_number("+70001112233") == "+7••••••2233" and mask_number("123") == "123"
    assert "занят" in explain_sip_status(486) and "SIP 486" in explain_sip_status(486) and "кодек" in explain_sip_status(488).lower() and "авторизац" in explain_sip_status(401)
