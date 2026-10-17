"""SIP: журнал вызовов, политика (запрещённые направления, лимиты, защита от перебора, кто звонит), предельная длительность, инструкция для инженера АТС и сводка проверок.
LiveKit подменён (FakeLK из test_sip); адреса и номера вымышленные. Пароль профиля не должен попадать ни в инструкцию, ни в журнал вызовов."""
from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

from app.models import SipCall, utcnow
from app.services.sip import CHECK_LABELS, connection_summary, number_blocked
from app.services.sip_guide import build_guide, server_address

from .conftest import login
from .test_access_and_protocols import end_by_alice  # noqa: F401
from .test_sip import SECRET, Ctx, FakeLK, SipErr, make_profile, room_ready
from .test_transcripts import _join


def put_policy(c, **kw):
    login(c, "root")
    r = c.put("/api/v1/admin/settings/sip_policy", json=kw)
    assert r.status_code == 200, r.text


def calls(c, **params):
    login(c, "root")
    return c.get("/api/v1/admin/sip/calls", params=params).json()["items"]


def test_number_blocked_prefixes():
    assert number_blocked("+1900, 810,00", "+19005550100") == "+1900"
    assert number_blocked("810", "81012345") == "810" and number_blocked("810", "8123") is None
    assert number_blocked("", "+7000") is None and number_blocked(None, "+7000") is None


def test_policy_validation(tmp_path, directory):
    with Ctx(tmp_path, directory, FakeLK()) as c:
        login(c, "root")
        for bad in ({"blocked_prefixes": "abc"}, {"outbound_scope": "everyone"}, {"max_concurrent": -1}, {"fail_window_minutes": 0}):
            assert c.put("/api/v1/admin/settings/sip_policy", json=bad).status_code == 422, bad
        put_policy(c, blocked_prefixes="810 ; +1900", outbound_scope="admins")
        assert c.get("/api/v1/admin/settings/sip_policy").json()["blocked_prefixes"] == "810,+1900"


def test_call_log_records_answered_failed_and_hangup(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        _p, room = room_ready(c)
        mid = _join(c, "alice", room["id"])["meeting_id"]  # noqa: F841
        login(c, "carol")
        url = f"/api/v1/rooms/{room['id']}/phone/call"
        lk.dial_error = SipErr()
        assert c.post(url, json={"number": "+70001112233"}).status_code == 502
        lk.dial_error = None
        res = c.post(url, json={"number": "+70001112244"}).json()
        c.post(f"/api/v1/rooms/{room['id']}/phone/hangup", json={"guest_id": res["guest_id"]})
        items = calls(c)
        failed = next(i for i in items if i["result"] == "failed")
        ok = next(i for i in items if i["result"] == "answered")
        assert failed["sip_status"] == 486 and "занят" in failed["reason"] and failed["direction"] == "out" and failed["duration_s"] == 0
        assert ok["end_reason"] == "hangup" and ok["ended"] is True and ok["tech_id"] == res["identity"] and ok["initiator"] and ok["room"] == room["name"]
        assert "+70001112244" not in json.dumps(items) and ok["number"].startswith("+7") and ok["number"].endswith("2244")       # номер скрыт по политике
        put_policy(c, mask_numbers=False)
        login(c, "carol")
        res2 = c.post(url, json={"number": "+70001112255"}).json()
        assert res2["guest_id"]
        assert any(i["number"] == "+70001112255" for i in calls(c)) and SECRET not in json.dumps(calls(c))
        assert c.get("/api/v1/admin/sip/calls").status_code == 200
        login(c, "carol")
        assert c.get("/api/v1/admin/sip/calls").status_code == 403


def test_policy_blocks_directions_scope_failures_and_concurrency(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        _p, room = room_ready(c, )
        _join(c, "alice", room["id"])
        url = f"/api/v1/rooms/{room['id']}/phone/call"
        put_policy(c, blocked_prefixes="+7000", fail_limit=0)
        login(c, "carol")
        r = c.post(url, json={"number": "+70001112233"})
        assert r.status_code == 403 and "запрещено" in r.json()["detail"] and not any(k[0] == "dial" for k in lk.calls)
        denied = [i for i in calls(c) if i["result"] == "denied"]
        assert denied and "+7000" in denied[0]["reason"]
        # только администраторы
        put_policy(c, blocked_prefixes="", outbound_scope="admins")
        login(c, "carol")
        r = c.post(url, json={"number": "+70001112233"})
        assert r.status_code == 403 and "администратор" in r.json()["detail"]
        # перебор: подряд неудачные вызовы блокируют инициатора
        put_policy(c, outbound_scope="leaders", fail_limit=4, fail_window_minutes=10)    # два отказа политики уже в счёте
        login(c, "carol")
        lk.dial_error = SipErr()
        assert [c.post(url, json={"number": f"+7000111220{i}"}).status_code for i in range(2)] == [502, 502]
        r = c.post(url, json={"number": "+70001112299"})
        assert r.status_code == 429 and "много неудачных" in r.json()["detail"]
        lk.dial_error = None
        put_policy(c, fail_limit=0, max_concurrent=1)          # предел одновременных
        login(c, "carol")
        first = c.post(url, json={"number": "+70001112201"})
        assert first.status_code == 200
        second = c.post(url, json={"number": "+70001112202"})
        assert second.status_code == 429 and "предел" in second.json()["detail"]
        put_policy(c, max_concurrent=0)
        login(c, "carol")
        assert c.post(url, json={"number": "+70001112203"}).status_code == 200


def test_sweep_closes_left_calls_enforces_max_duration_and_purges(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        _p, room = room_ready(c)
        _join(c, "alice", room["id"])
        login(c, "carol")
        res = c.post(f"/api/v1/rooms/{room['id']}/phone/call", json={"number": "+70001112233"}).json()
        put_policy(c, max_call_minutes=1, call_log_days=30)
        st = c.app_obj.state

        async def old_call_and_sweep():
            async with st.session_maker() as db:
                from sqlalchemy import select
                row = (await db.execute(select(SipCall).where(SipCall.result == "answered"))).scalars().one()
                row.answered_at = row.started_at = utcnow() - timedelta(minutes=5)
                db.add(SipCall(direction="out", number="x", result="failed", started_at=utcnow() - timedelta(days=60), ended_at=utcnow() - timedelta(days=60)))
                await db.commit()
            async with st.session_maker() as db:
                return await st.sip_routing.sweep(db)

        assert c.portal.call(old_call_and_sweep) >= 1
        assert ("remove", f"m-{__import__('uuid').UUID(_join_mid(c, room)).hex}", res["identity"]) in lk.calls
        items = calls(c)
        assert next(i for i in items if i["tech_id"] == res["identity"])["end_reason"] == "limit"
        assert not any(i["number"] == "x" for i in items)                               # старше срока хранения — удалено


def _join_mid(c, room):
    login(c, "root")
    return c.get(f"/api/v1/rooms/{room['id']}/phone").json()["active_meeting_id"]


def test_test_call_is_logged_and_summary_has_five_labelled_results(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        p = make_profile(c)

        async def audio(room_name, identity):
            return True

        c.app_obj.state.sip_gateway.audio_from_remote = audio
        r = c.post(f"/api/v1/admin/sip/profiles/{p['id']}/test-call", json={"number": "+70001112233"}).json()
        assert r["ok"] is True and r["audio"]["ok"] is True
        assert next(s for s in r["summary"] if s["key"] == "test_call")["text"] == "Тестовый вызов успешен"
        assert next(s for s in r["summary"] if s["key"] == "audio_in")["ok"] is True
        assert next(s for s in r["summary"] if s["key"] == "audio_confirmed")["ok"] is None            # само не подтверждается
        assert any(i["direction"] == "test" and i["result"] == "answered" for i in calls(c))
        login(c, "root")
        s = c.post(f"/api/v1/admin/sip/profiles/{p['id']}/confirm-audio", json={"heard": True}).json()["summary"]
        assert next(x for x in s if x["key"] == "audio_confirmed")["text"].startswith("Двусторонний звук подтверждён")
        # проверка настроек не стирает итоги тестового вызова и добавляет этапы dns / listen
        chk = c.post(f"/api/v1/admin/sip/profiles/{p['id']}/check").json()
        stages = {x["stage"] for x in chk["stages"]}
        assert {"config", "dns", "pbx", "livekit", "service", "listen"} <= stages
        assert next(x for x in chk["summary"] if x["key"] == "audio_confirmed")["ok"] is True
        assert [x["key"] for x in chk["summary"]] == list(CHECK_LABELS)
        # новый тестовый вызов снимает старое подтверждение; неудачный не оставляет «звук идёт»
        lk.dial_error = SipErr()
        r2 = c.post(f"/api/v1/admin/sip/profiles/{p['id']}/test-call", json={"number": "+70001112233"}).json()
        assert r2["ok"] is False and next(x for x in r2["summary"] if x["key"] == "audio_confirmed")["ok"] is None and next(x for x in r2["summary"] if x["key"] == "audio_in")["ok"] is None
        assert c.post(f"/api/v1/admin/sip/profiles/{p['id']}/confirm-audio", json={"heard": True}).status_code == 409


def test_connection_summary_without_checks():
    assert [s["ok"] for s in connection_summary(None)] == [None] * 6


def test_guide_uses_real_settings_and_never_contains_secret(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        p = make_profile(c)
        login(c, "root")
        g = c.get("/api/v1/admin/sip/guide", params={"profile_id": p["id"]}).json()
        text = json.dumps(g, ensure_ascii=False)
        assert SECRET not in text and "pbx.example.local" in g["text"] and "5060" in g["text"] and "20000–20100" in g["text"] and "192.0.2.0/24" in g["text"]
        rows = {(r["src"], r["dst"], r["proto"], r["port"]) for r in g["network"]}
        assert ("АТС", "Peregovorka SIP", "UDP/TCP", "5060") in rows and ("АТС", "Peregovorka SIP", "UDP", "20000–20100") in rows
        assert any(n["firewall"] for n in g["diagram"]["links"]) and len(g["ask"]) >= 10
        login(c, "carol")
        assert c.get("/api/v1/admin/sip/guide").status_code == 403


def test_guide_follows_direction_and_port_changes():
    s = SimpleNamespace(sip_signaling_port=5080, sip_rtp_start=30000, sip_rtp_end=30040, sip_media_ip="198.51.100.7", livekit_node_ip="203.0.113.1", app_public_url="https://meet.example.org",
                        sip_allowed_cidrs="")
    out = build_guide(s, {"name": "Т", "direction": "outbound", "host": "pbx.example.local", "port": 5061, "transport": "tls", "allowed_addresses": [], "codecs": ["PCMA"], "username": "", "caller_id": ""}, [])
    pbx = [r for r in out["network"] if r["scope"] == "pbx"]
    assert all(r["port"] != "5080" or r["dst"] == "Peregovorka SIP" for r in pbx)                      # только исходящий: SIP к АТС идёт на порт профиля
    assert any(r["src"] == "Peregovorka SIP" and r["port"] == "5061" and r["proto"] == "TLS" for r in pbx)
    assert not any(r["src"] == "АТС" and r["port"] == "5080" for r in pbx)                             # входящей сигнализации нет
    assert "198.51.100.7" in out["text"] and "30000–30040" in out["text"] and "TLS" in out["tls_note"].upper()
    assert server_address(s) == ("198.51.100.7", "SIP_MEDIA_IP")
    s.sip_media_ip = ""
    assert server_address(s) == ("203.0.113.1", "LIVEKIT_NODE_IP")
    s.livekit_node_ip = ""
    assert server_address(s) == ("meet.example.org", "публичный адрес приложения")


def test_inbound_over_limit_is_rejected_and_logged(tmp_path, directory):
    lk = FakeLK()
    with Ctx(tmp_path, directory, lk) as c:
        p, room = room_ready(c)
        mid = _join(c, "alice", room["id"])["meeting_id"]
        put_policy(c, max_concurrent=1)
        st = c.app_obj.state

        async def inbound(ident):
            import uuid
            async with st.session_maker() as db:
                g = await st.sip_routing.register_inbound(db, uuid.UUID(mid), ident, {"sip.phoneNumber": "+70005550101"})
                await db.commit()
                return g

        assert c.portal.call(inbound, "sip_a1") is not None
        assert c.portal.call(inbound, "sip_a2") is None
        assert any(k[0] == "remove" and k[2] == "sip_a2" for k in lk.calls)
        items = calls(c, direction="in")
        assert {i["result"] for i in items} == {"answered", "denied"} and "+70005550101" not in json.dumps(items)
