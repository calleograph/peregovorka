"""Публичный API, этап 2: SSRF, подпись и защита от повторов, доставка событий (повторы, деградация, отключение, ручной повтор, ротация секрета), фоновые задачи,
Idempotency-Key, ссылки на скачивание, некорректный ввод, параллельные запросы, отказ БД, большие выборки. Без сети."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from sqlalchemy import insert, select

from app.models import (ApiClient, ApiJob, AuditLog, ConversationMap, Meeting, Recording, TranscriptSegment, WebhookDelivery, WebhookEndpoint, User, utcnow)
from app.publicapi import downloads, ids
from app.publicapi.ssrf import UrlRejected, parse_allow, pinned_url, validate_url
from app.publicapi.webhooks import backoff_s, sign, signature_header, verify

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_access_and_protocols import LLM_OK, end_by_alice, meeting_with_two
from .test_admin_features import anon_ok
from .test_public_api import ALL, enable, err, hdr, make_client, run, seed_meeting, uid, users_ready

P = "/api/public/v1"
ALL2 = ALL + ["protocols:generate", "summaries:generate", "maps:generate", "jobs:read", "recordings:download"]
HOOK = "https://hooks.example.com/in"


# ================================================================================================ SSRF (чистые проверки)
def resolver_for(mapping: dict):
    async def res(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError("not found")
        return list(mapping[host])
    return res


def check(url: str, mapping=None, **kw):
    return asyncio.run(validate_url(url, resolver=resolver_for(mapping or {"hooks.example.com": ["203.0.113.10"]}), **kw))


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/x", "https://localhost/x", "https://[::1]/x", "https://10.0.0.5/x", "https://192.168.1.10/x", "https://172.16.0.1/x", "https://172.31.255.255/x",
    "https://100.64.0.1/x", "https://169.254.169.254/latest/meta-data", "https://169.254.1.1/x", "https://[fe80::1]/x", "https://0.0.0.0/x", "https://224.0.0.1/x",
    "https://[fd00::1]/x", "https://[::ffff:10.0.0.1]/x", "https://[::ffff:127.0.0.1]/x", "https://[fd00:ec2::254]/x"])
def test_ssrf_blocks_internal_and_special_addresses_by_default(url):
    with pytest.raises(UrlRejected):
        check(url, {"localhost": ["127.0.0.1"]})


def test_ssrf_checks_every_resolved_address_and_unresolvable_names():
    assert check("https://hooks.example.com/in")[3] == ["203.0.113.10"]
    with pytest.raises(UrlRejected, match="внутренние"):
        check("https://rebind.example.com/in", {"rebind.example.com": ["203.0.113.10", "10.0.0.7"]})      # один внутренний адрес среди публичных — отказ целиком
    with pytest.raises(UrlRejected, match="DNS"):
        check("https://nowhere.example.com/in", {})
    with pytest.raises(UrlRejected):
        check("https://meta.example.com/in", {"meta.example.com": ["169.254.169.254"]})


@pytest.mark.parametrize("url", ["ftp://hooks.example.com/x", "file:///etc/passwd", "gopher://hooks.example.com/", "javascript:alert(1)", "//hooks.example.com/x", "hooks.example.com/x", "",
                                 "https://user:pw@hooks.example.com/x", "https://hooks.example.com/x#frag", "https://hooks.example.com:99999/x", "https://", "https://hooks.example.com/ x",
                                 "https://hooks.example.com/\n", "https://hooks.example.com/" + "a" * 600])
def test_ssrf_rejects_bad_schemes_credentials_fragments_and_junk(url):
    with pytest.raises(UrlRejected):
        check(url)


def test_http_only_with_the_explicit_policy():
    with pytest.raises(UrlRejected, match="https"):
        check("http://hooks.example.com/x")
    assert check("http://hooks.example.com/x", allow_http=True)[2] == "http"


def test_onprem_policy_allows_named_internal_targets_but_never_cloud_metadata():
    internal = {"crm.corp.local": ["10.20.1.5"], "other.corp.local": ["10.99.0.1"]}
    assert check("https://crm.corp.local/hook", internal, allow_text="crm.corp.local")[3] == ["10.20.1.5"]
    with pytest.raises(UrlRejected):
        check("https://other.corp.local/hook", internal, allow_text="crm.corp.local")                        # разрешён один узел, не вся сеть
    assert check("https://other.corp.local/hook", internal, allow_text="10.99.0.0/24")[3] == ["10.99.0.1"]
    assert check("https://127.0.0.1/x", allow_text="127.0.0.0/8")[3] == ["127.0.0.1"], "явная сеть loopback — осознанный выбор администратора"
    assert check("https://169.254.1.1/x", allow_text="169.254.0.0/16")[3] == ["169.254.1.1"], "link-local — только явной сетью"
    with pytest.raises(UrlRejected, match="метаданных"):
        check("https://169.254.169.254/x", allow_text="169.254.0.0/16, 0.0.0.0/0")                         # метаданные облака не разрешаются никогда


def test_policy_text_is_validated_not_silently_ignored():
    nets, hosts = parse_allow("crm.corp.local; 10.20.0.0/16,\n  Files.Corp.Local ")
    assert hosts == {"crm.corp.local", "files.corp.local"} and len(nets) == 1
    for bad in ("evil host", "a..b", "-x.corp", "http://crm", "10.0.0.1/99"):
        with pytest.raises(ValueError):
            parse_allow(bad)


def test_connection_goes_to_the_checked_ip_not_to_a_second_dns_answer():
    assert pinned_url("https://crm.example.com:8443/hook?x=1", "203.0.113.5") == "https://203.0.113.5:8443/hook?x=1"
    assert pinned_url("https://crm.example.com/hook", "2001:db8::5") == "https://[2001:db8::5]/hook"


# ================================================================================================ подпись и защита от повторов
def test_signature_verifies_and_rejects_tampering_wrong_secret_and_stale_timestamps():
    body, ts, secret = '{"id":"evt_1"}'.encode(), 1_800_000_000, "whsec_test"
    head = signature_header([secret], ts, body)
    assert head.startswith("v1=") and verify(head, [secret], ts, body, now=ts + 10)
    assert not verify(head, [secret], ts, body + b" ", now=ts + 10), "изменённое тело"
    assert not verify(head, ["whsec_other"], ts, body, now=ts + 10), "чужой секрет"
    assert not verify(head, [secret], ts + 1, body, now=ts + 10), "подмена метки времени (она входит в подпись)"
    assert not verify(head, [secret], ts, body, now=ts + 301), "повтор перехваченного запроса позже 5 минут"
    assert not verify(head, [secret], ts, body, now=ts - 301), "метка из будущего"
    assert verify(head, [secret], ts, body, now=ts + 299)
    for junk in ("", "v1=", "v2=" + head[3:], "garbage", "v1=zz"):
        assert not verify(junk, [secret], ts, body, now=ts)


def test_rotation_window_header_has_two_signatures_either_verifies():
    body, ts = b"{}", 1_800_000_000
    head = signature_header(["whsec_new", "whsec_old"], ts, body)
    assert head.count("v1=") == 2
    assert verify(head, ["whsec_old"], ts, body, now=ts) and verify(head, ["whsec_new"], ts, body, now=ts)
    assert not verify(head, ["whsec_third"], ts, body, now=ts)
    assert sign("a", ts, body) != sign("b", ts, body)


def test_backoff_grows_exponentially_with_jitter_and_is_capped():
    import random
    r = random.Random(7)
    vals = [backoff_s(n, r) for n in range(1, 12)]
    assert 8 <= vals[0] <= 12 and 16 <= vals[1] <= 24 and 32 <= vals[2] <= 48
    assert all(v <= 3600 * 1.2 + 0.1 for v in vals) and vals[-1] > 2800, "ограничение сверху — около часа"
    assert all(vals[i] < vals[i + 1] * 1.6 for i in range(len(vals) - 1))


# ================================================================================================ получатели событий
class Hook:
    """Подставной получатель: запоминает запросы, статус ответа задаётся тестом."""

    def __init__(self, status: int = 200, headers: dict | None = None):
        self.status, self.headers, self.calls = status, headers or {}, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        return httpx.Response(self.status, headers=self.headers, content=b"ok")


def hook_app(tmp_path, directory, hook: Hook, *, llm_seen: list | None = None):
    def llm(req: httpx.Request) -> httpx.Response:
        if llm_seen is not None:
            llm_seen.append(json.loads(req.content))
        return LLM_OK(req)
    cm = running_app(make_settings(tmp_path), directory, transports={"webhook": httpx.MockTransport(hook), "anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm)})
    return cm


class Ctx:
    """Контекст приложения с подставным DNS: адрес получателя разрешается в публичный IP."""

    def __init__(self, c, resolver):
        self.c, self.resolver = c, resolver


def prepare(c, mapping=None):
    c.app_obj.state.webhooks._resolver = resolver_for(mapping or {"hooks.example.com": ["203.0.113.10"]})


def add_hook(c, *, url=HOOK, events=None, rooms=None, name="crm-hook", enabled=True) -> dict:
    login(c, "root")
    r = c.post("/api/v1/admin/public-api/webhooks", json={"name": name, "url": url, "events": events or [], "rooms": rooms, "enabled": enabled})
    assert r.status_code == 201, r.text
    c.cookies.clear()
    c.headers.pop("X-CSRF-Token", None)
    return r.json()


def deliveries(c) -> list[WebhookDelivery]:
    async def go():
        async with c.app_obj.state.session_maker() as db:
            return (await db.execute(select(WebhookDelivery).order_by(WebhookDelivery.created_at, WebhookDelivery.id))).scalars().all()
    return run(c, go)


def endpoint(c, eid: str) -> WebhookEndpoint:
    async def go():
        async with c.app_obj.state.session_maker() as db:
            return await db.get(WebhookEndpoint, uuid.UUID(eid))
    return run(c, go)


def deliver(c, now=None) -> int:
    svc = c.app_obj.state.webhooks

    async def go():
        await svc.drain()
        return await svc.deliver_due(now=now)
    return run(c, go)


def later(hours=3):
    return utcnow() + dt.timedelta(hours=hours)


def test_creating_a_receiver_validates_the_address_and_shows_the_secret_once(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        prepare(c, {"hooks.example.com": ["203.0.113.10"], "intranet.example.com": ["10.1.2.3"]})
        login(c, "root")
        for bad in ("http://hooks.example.com/in", "https://127.0.0.1/in", "https://169.254.169.254/in", "https://intranet.example.com/in", "https://u:p@hooks.example.com/in", "ftp://x/y"):
            r = c.post("/api/v1/admin/public-api/webhooks", json={"name": "bad", "url": bad})
            assert r.status_code == 422, (bad, r.status_code)
        r = c.post("/api/v1/admin/public-api/webhooks", json={"name": "good", "url": HOOK, "events": ["meeting.ended"]})
        assert r.status_code == 201 and r.json()["secret"].startswith("whsec_") and r.json()["status"] == "active"
        secret = r.json()["secret"]
        listing = c.get("/api/v1/admin/public-api/webhooks")
        assert secret not in listing.text and listing.json()[0]["secret_set"] is True
        assert c.post("/api/v1/admin/public-api/webhooks", json={"name": "x", "url": HOOK, "events": ["nope"]}).status_code == 422
        assert c.post("/api/v1/admin/public-api/webhooks", json={"name": "x", "url": HOOK, "events": ["webhook.test"]}).status_code == 422
        assert c.post("/api/v1/admin/public-api/webhooks", json={"name": "x", "url": HOOK, "rooms": [str(uuid.uuid4())]}).status_code == 422
        put_settings(c, "api", webhook_allow_hosts="intranet.example.com")                  # политика on-prem для конкретного узла
        assert c.post("/api/v1/admin/public-api/webhooks", json={"name": "internal", "url": "https://intranet.example.com/in"}).status_code == 201
        assert c.put("/api/v1/admin/settings/api", json={"webhook_allow_hosts": "bad host!"}).status_code == 422
        assert c.put("/api/v1/admin/settings/api", json={"webhook_degraded_after": 10, "webhook_disable_after": 5}).status_code == 422
        actions = [a["action"] for a in c.get("/api/v1/admin/audit").json()]
        assert actions.count("webhook.create") == 2
        login(c, "alice")
        assert c.get("/api/v1/admin/public-api/webhooks").status_code == 403


def test_event_is_signed_pinned_to_the_checked_ip_and_free_of_personal_data(tmp_path, directory):
    hook = Hook()
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        wh = add_hook(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        assert deliver(c) == 2, "встреча началась и завершилась — два события"
        assert [h.headers["x-peregovorka-event"] for h in hook.calls] == ["meeting.started", "meeting.ended"] or sorted(h.headers["x-peregovorka-event"] for h in hook.calls) == ["meeting.ended", "meeting.started"]
        call = next(h for h in hook.calls if h.headers["x-peregovorka-event"] == "meeting.ended")
        body, ts = call.content, int(call.headers["x-peregovorka-timestamp"])
        assert verify(call.headers["x-peregovorka-signature"], [wh["secret"]], ts, body)
        assert call.url.host == "203.0.113.10" and call.headers["host"] == "hooks.example.com", "соединение идёт на проверенный IP, имя — в Host"
        assert call.extensions.get("sni_hostname") == "hooks.example.com"
        ev = json.loads(body)
        assert ev["id"].startswith("evt_") and ev["id"] == call.headers["x-peregovorka-event-id"] and ev["type"] == "meeting.ended" and ev["api_version"] == "1"
        assert ev["data"]["meeting_id"] == ids.pub("meeting", uuid.UUID(mid)) and ev["data"]["room"]["slug"] == room["slug"] and ev["data"]["end_reason"] == "manual"
        text = body.decode()
        assert "@" not in text and "Alice" not in text and "Bob" not in text and "бюджет" not in text, "в событии нет персональных данных и текстов"
        assert call.headers["content-type"].startswith("application/json") and call.headers["x-peregovorka-attempt"] == "1" and call.headers["user-agent"].startswith("Peregovorka-Webhook")
        assert {d.status for d in deliveries(c)} == {"delivered"} and endpoint(c, wh["id"]).last_success_at is not None


def test_event_filters_by_type_and_by_room(tmp_path, directory):
    hook = Hook()
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        other = make_room(c)
        room, mid = meeting_with_two(c)
        only_ended = add_hook(c, name="ended", events=["meeting.ended"])
        docs = add_hook(c, name="docs", events=["document.ready"])
        elsewhere = add_hook(c, name="other-room", rooms=[other["id"]])
        this_room = add_hook(c, name="this-room", rooms=[room["id"]], events=["meeting.ended"])
        end_by_alice(c, mid)
        deliver(c)
        by = {}
        for d in deliveries(c):
            by.setdefault(str(d.endpoint_id), []).append(d.event_type)
        assert by == {only_ended["id"]: ["meeting.ended"], this_room["id"]: ["meeting.ended"]}, by
        assert docs["id"] not in by and elsewhere["id"] not in by


def test_events_are_not_queued_while_the_api_is_off_or_the_receiver_is_disabled(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        prepare(c)
        wh = add_hook(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        deliver(c)
        assert deliveries(c) == [], "API выключен — событий нет"
        enable(c)
        login(c, "root")
        assert c.patch(f"/api/v1/admin/public-api/webhooks/{wh['id']}", json={"name": "crm-hook", "url": HOOK, "enabled": False}).status_code == 200
        room2, mid2 = meeting_with_two(c)
        end_by_alice(c, mid2)
        deliver(c)
        assert deliveries(c) == [], "получатель выключен — событий нет"


def test_failed_delivery_is_retried_with_growing_pauses_then_marked_failed_and_manual_retry_works(tmp_path, directory):
    hook = Hook(status=500)
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        put_settings(c, "api", webhook_max_attempts=3)
        wh = add_hook(c, events=["meeting.ended"])
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        t0 = utcnow() + dt.timedelta(seconds=2)          # строка очереди создаётся фоновой задачей чуть позже конца встречи
        assert deliver(c, now=t0) == 1
        d = deliveries(c)[0]
        pause1 = (d.next_attempt_at - t0).total_seconds() if d.next_attempt_at.tzinfo else (d.next_attempt_at - t0.replace(tzinfo=None)).total_seconds()
        assert d.status == "pending" and d.attempts == 1 and 8 <= pause1 <= 12 and d.last_status == 500 and "HTTP 500" in d.last_error
        assert deliver(c, now=t0 + dt.timedelta(seconds=3)) == 0, "до срока повтор не отправляется"
        assert deliver(c, now=t0 + dt.timedelta(minutes=5)) == 1
        d = deliveries(c)[0]
        assert d.attempts == 2 and d.status == "pending"
        assert deliver(c, now=t0 + dt.timedelta(hours=2)) == 1
        d = deliveries(c)[0]
        assert d.attempts == 3 and d.status == "failed", "после webhook_max_attempts — «не доставлено»"
        assert len(d.attempt_log) == 3 and all(a["status"] == 500 for a in d.attempt_log)
        assert deliver(c, now=later(10)) == 0
        first_event = d.event_id
        hook.status = 200
        login(c, "root")
        r = c.post(f"/api/v1/admin/public-api/webhooks/deliveries/{d.id}/retry")
        assert r.status_code == 200
        assert deliver(c, now=later(10)) == 1
        d2 = deliveries(c)[0]
        last = hook.calls[-1]
        assert d2.status == "delivered" and d2.manual_retries == 1 and last.headers["x-peregovorka-retry"] == "manual"
        assert last.headers["x-peregovorka-event-id"] == first_event, "при повторе идентификатор события тот же — получатель отбрасывает дубль"
        e = endpoint(c, wh["id"])
        assert e.consecutive_failures == 0 and e.status == "active"
        assert "webhook.retry" in [a["action"] for a in c.get("/api/v1/admin/audit").json()]


def test_replay_is_detectable_same_event_id_but_fresh_timestamp_and_signature(tmp_path, directory):
    hook = Hook(status=500)
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        wh = add_hook(c, events=["meeting.ended"])
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        deliver(c, now=utcnow() + dt.timedelta(seconds=2))
        deliver(c, now=later(1))
        a, b = hook.calls[0], hook.calls[1]
        assert a.headers["x-peregovorka-event-id"] == b.headers["x-peregovorka-event-id"] and a.headers["x-peregovorka-delivery"] == b.headers["x-peregovorka-delivery"]
        assert a.headers["x-peregovorka-timestamp"] != b.headers["x-peregovorka-timestamp"] and a.headers["x-peregovorka-signature"] != b.headers["x-peregovorka-signature"]
        # получатель: принимает событие один раз и только со свежей меткой
        seen: set[str] = set()

        def receive(call, now):
            ts = int(call.headers["x-peregovorka-timestamp"])
            if not verify(call.headers["x-peregovorka-signature"], [wh["secret"]], ts, call.content, now=now):
                return "reject"
            eid = call.headers["x-peregovorka-event-id"]
            if eid in seen:
                return "duplicate"
            seen.add(eid)
            return "accepted"
        assert receive(a, int(a.headers["x-peregovorka-timestamp"])) == "accepted"
        assert receive(b, int(b.headers["x-peregovorka-timestamp"])) == "duplicate"
        assert receive(a, int(a.headers["x-peregovorka-timestamp"]) + 600) == "reject", "перехваченный запрос через 10 минут"


def test_receiver_becomes_degraded_then_disabled_and_recovers_when_re_enabled(tmp_path, directory):
    hook = Hook(status=503)
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        put_settings(c, "api", webhook_degraded_after=2, webhook_disable_after=4, webhook_max_attempts=20)
        wh = add_hook(c, events=["meeting.ended"])
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        n = 0
        statuses = []
        for _ in range(6):
            n += deliver(c, now=later(50 + len(statuses)))
            statuses.append(endpoint(c, wh["id"]).status)
        assert statuses[:2] == ["active", "degraded"] and statuses[3:] == ["disabled", "disabled", "disabled"], statuses
        assert n == 4, "после отключения попытки прекращаются"
        e = endpoint(c, wh["id"])
        assert "4 неудачных" in e.disabled_reason and e.consecutive_failures == 4
        login(c, "root")
        assert "webhook.auto_disable" in [a["action"] for a in c.get("/api/v1/admin/audit").json()]
        assert c.get("/api/v1/admin/public-api/webhooks").json()[0]["status"] == "disabled"
        r = c.post(f"/api/v1/admin/public-api/webhooks/deliveries/{deliveries(c)[0].id}/retry")
        assert r.status_code == 409, "отключённому ручной повтор не отправляется: сначала включить"
        hook.status = 200
        assert c.post(f"/api/v1/admin/public-api/webhooks/{wh['id']}/enable").json()["status"] == "active"
        assert deliver(c, now=later(300)) == 1
        assert deliveries(c)[0].status == "delivered" and endpoint(c, wh["id"]).consecutive_failures == 0


def test_410_gone_disables_immediately_and_redirects_are_never_followed(tmp_path, directory):
    hook = Hook(status=410)
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        wh = add_hook(c, events=["meeting.ended"])
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        deliver(c)
        e = endpoint(c, wh["id"])
        assert e.status == "disabled" and "410" in e.disabled_reason
    redirect = Hook(status=302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    with hook_app(tmp_path / "r", directory, redirect) if (tmp_path / "r").mkdir() is None else None as c:
        prepare(c)
        enable(c)
        add_hook(c, events=["meeting.ended"])
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        deliver(c)
        assert len(redirect.calls) == 1, "редирект не выполняется"
        d = deliveries(c)[0]
        assert d.status == "pending" and d.last_status == 302 and "редиректы не выполняются" in d.last_error


def test_dns_rebinding_is_caught_at_send_time_and_policy_can_allow_a_named_internal_host(tmp_path, directory):
    hook = Hook()
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        add_hook(c, events=["meeting.ended"])
        prepare(c, {"hooks.example.com": ["10.0.0.7"]})                       # после создания имя «переехало» на внутренний адрес
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        deliver(c)
        d = deliveries(c)[0]
        assert hook.calls == [] and d.status == "pending" and "запрещён политикой" in d.last_error, "запрос на внутренний адрес не отправляется"
        put_settings(c, "api", webhook_allow_hosts="hooks.example.com")
        deliver(c, now=later(2))
        assert len(hook.calls) == 1 and hook.calls[0].url.host == "10.0.0.7", "политика on-prem разрешила именно этот узел"


def test_secret_rotation_signs_with_both_secrets_during_the_window_then_only_the_new(tmp_path, directory):
    hook = Hook()
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        wh = add_hook(c, events=["meeting.ended"])
        old = wh["secret"]
        login(c, "root")
        r = c.post(f"/api/v1/admin/public-api/webhooks/{wh['id']}/rotate-secret", json={"grace_hours": 24})
        assert r.status_code == 201
        new = r.json()["secret"]
        assert new != old and old not in c.get("/api/v1/admin/public-api/webhooks").text and new not in c.get("/api/v1/admin/public-api/webhooks").text
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        deliver(c)
        call = hook.calls[-1]
        head, ts = call.headers["x-peregovorka-signature"], int(call.headers["x-peregovorka-timestamp"])
        assert head.count("v1=") == 2 and verify(head, [old], ts, call.content) and verify(head, [new], ts, call.content)
        login(c, "root")
        r2 = c.post(f"/api/v1/admin/public-api/webhooks/{wh['id']}/rotate-secret", json={"grace_hours": 0})
        newest = r2.json()["secret"]
        room2, mid2 = meeting_with_two(c)
        end_by_alice(c, mid2)
        deliver(c)
        call = hook.calls[-1]
        head, ts = call.headers["x-peregovorka-signature"], int(call.headers["x-peregovorka-timestamp"])
        assert head.count("v1=") == 1 and verify(head, [newest], ts, call.content) and not verify(head, [old, new], ts, call.content)
        login(c, "root")
        assert [a["action"] for a in c.get("/api/v1/admin/audit").json()].count("webhook.rotate_secret") == 2


def test_manual_test_event_and_delivery_history_with_paging(tmp_path, directory):
    hook = Hook()
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        enable(c)
        wh = add_hook(c)
        login(c, "root")
        res = c.post(f"/api/v1/admin/public-api/webhooks/{wh['id']}/test").json()
        assert res["ok"] is True and res["status"] == 200 and hook.calls[0].headers["x-peregovorka-event"] == "webhook.test"
        hook.status = 500
        bad = c.post(f"/api/v1/admin/public-api/webhooks/{wh['id']}/test").json()
        assert bad["ok"] is False and bad["status"] == 500 and "HTTP 500" in bad["error"]
        hist = c.get(f"/api/v1/admin/public-api/webhooks/{wh['id']}/deliveries").json()
        assert [h["event_type"] for h in hist] == ["webhook.test", "webhook.test"] and {h["status"] for h in hist} == {"delivered", "failed"}
        assert hist[0]["attempt_log"] and hist[0]["event_id"].startswith("evt_")
        assert len(c.get(f"/api/v1/admin/public-api/webhooks/{wh['id']}/deliveries", params={"status": "failed"}).json()) == 1
        assert c.get(f"/api/v1/admin/public-api/webhooks/{wh['id']}/deliveries", params={"before": "not-a-date"}).status_code == 422
        assert c.get(f"/api/v1/admin/public-api/webhooks/{wh['id']}/deliveries", params={"status": "weird"}).status_code == 422
        assert deliver(c, now=later(5)) == 0, "проверочное событие не повторяется само"


def test_disabled_url_policy_is_enforced_even_for_manual_tests(tmp_path, directory):
    hook = Hook()
    with hook_app(tmp_path, directory, hook) as c:
        prepare(c)
        wh = add_hook(c)
        prepare(c, {"hooks.example.com": ["127.0.0.1"]})
        login(c, "root")
        res = c.post(f"/api/v1/admin/public-api/webhooks/{wh['id']}/test").json()
        assert res["ok"] is False and "запрещён политикой" in res["error"] and hook.calls == []


# ================================================================================================ фоновые задачи
def gen_client(c, scopes):
    cl, key = make_client(c, name=f"gen-{uuid.uuid4().hex[:6]}", scopes=scopes)
    return cl, key


def configure_llm(c):
    put_settings(c, "anonymizer", enabled=True, base_url="https://anon.test", token="t")
    put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")
    c.cookies.clear()
    c.headers.pop("X-CSRF-Token", None)


def drain_jobs(c):
    run(c, c.app_obj.state.jobs.drain)


def test_generation_is_a_202_job_that_completes_and_yields_a_readable_document(tmp_path, directory):
    seen: list = []
    hook = Hook()
    with hook_app(tmp_path, directory, hook, llm_seen=seen) as c:
        prepare(c)
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl, key = gen_client(c, ["protocols:generate", "jobs:read", "protocols:read"])
        enable(c)
        add_hook(c, events=["job.completed", "document.ready"])
        pm = ids.pub("meeting", uuid.UUID(mid))
        r = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol", "instruction": "Только решения"}, headers=hdr(key))
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["status"] == "queued" and job["kind"] == "protocol" and job["id"].startswith("job_") and r.headers["location"] == f"{P}/jobs/{job['id']}"
        assert c.get(f"{P}/jobs/{job['id']}", headers=hdr(key)).json()["status"] == "queued"
        drain_jobs(c)
        done = c.get(f"{P}/jobs/{job['id']}", headers=hdr(key)).json()
        assert done["status"] == "completed" and done["result"]["document_id"].startswith("pro_") and done["finished_at"] and done["error"] is None
        doc = c.get(f"{P}/meetings/{pm}/documents/{done['result']['document_id']}", headers=hdr(key)).json()
        assert doc["status"] == "ready" and "Протокол" in doc["content"] and doc["instruction"] == "Только решения"
        assert "Только решения" in json.dumps(seen[-1], ensure_ascii=False), "инструкция интеграции дошла до модели"
        run(c, c.app_obj.state.webhooks.drain)
        types = sorted(d.event_type for d in deliveries(c))
        assert types == ["document.ready", "job.completed"], types
        assert deliver(c) == 2
        evs = {json.loads(h.content)["type"]: json.loads(h.content)["data"] for h in hook.calls}
        assert evs["job.completed"]["job_id"] == job["id"] and evs["job.completed"]["document_id"] == done["result"]["document_id"]
        login(c, "root")
        actions = [a["action"] for a in c.get("/api/v1/admin/audit").json()]
        assert "api.job.create" in actions


def test_job_preconditions_are_reported_with_clear_codes(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        room, mid = meeting_with_two(c)
        cl, key = gen_client(c, ["protocols:generate", "summaries:generate", "maps:generate", "jobs:read"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        r = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(key))
        assert r.status_code == 409 and err(r)["code"] == "meeting_not_ended"
        end_by_alice(c, mid)
        r = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(key))
        assert r.status_code == 409 and err(r)["code"] == "llm_not_configured"
        r = c.post(f"{P}/meetings/{pm}/map", headers=hdr(key))
        assert r.status_code == 409 and err(r)["code"] == "llm_not_configured"
        assert c.get(f"{P}/jobs", headers=hdr(key)).json()["items"] == [], "отклонённые запросы задач не создают"


def test_generate_scopes_are_per_kind_and_foreign_meetings_are_404(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        other = make_room(c)
        cl, key = gen_client(c, ["protocols:generate", "jobs:read"])
        scoped = make_client(c, name="room-limited", scopes=["protocols:generate", "jobs:read"], rooms=[other["id"]])[1]
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        r = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "summary"}, headers=hdr(key))
        assert r.status_code == 403 and err(r)["required_scope"] == "summaries:generate"
        r = c.post(f"{P}/meetings/{pm}/map", headers=hdr(key))
        assert r.status_code == 403 and err(r)["required_scope"] == "maps:generate"
        foreign = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(scoped))
        missing = c.post(f"{P}/meetings/{ids.pub('meeting', uuid.uuid4())}/documents", json={"kind": "protocol"}, headers=hdr(scoped))
        assert foreign.status_code == missing.status_code == 404 and err(foreign) == {**err(missing), "request_id": err(foreign)["request_id"]} or err(foreign)["code"] == err(missing)["code"]
        assert c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(key)).status_code == 202


def test_jobs_belong_to_their_client_and_queued_ones_can_be_cancelled(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl1, k1 = gen_client(c, ["protocols:generate", "jobs:read"])
        cl2, k2 = gen_client(c, ["protocols:generate", "jobs:read"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        j1 = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(k1)).json()
        assert c.get(f"{P}/jobs/{j1['id']}", headers=hdr(k2)).status_code == 404 and c.post(f"{P}/jobs/{j1['id']}/cancel", headers=hdr(k2)).status_code == 404
        assert c.get(f"{P}/jobs", headers=hdr(k2)).json()["items"] == [] and len(c.get(f"{P}/jobs", headers=hdr(k1)).json()["items"]) == 1
        r = c.post(f"{P}/jobs/{j1['id']}/cancel", headers=hdr(k1))
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert c.post(f"{P}/jobs/{j1['id']}/cancel", headers=hdr(k1)).json()["status"] == "cancelled", "повторная отмена безопасна"
        drain_jobs(c)
        assert c.get(f"{P}/jobs/{j1['id']}", headers=hdr(k1)).json()["status"] == "cancelled", "отменённая задача не выполняется"
        j2 = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(k1)).json()
        drain_jobs(c)
        r = c.post(f"{P}/jobs/{j2['id']}/cancel", headers=hdr(k1))
        assert r.status_code == 409 and err(r)["code"] == "not_cancellable"
        assert [j["status"] for j in c.get(f"{P}/jobs", params={"status": "completed"}, headers=hdr(k1)).json()["items"]] == ["completed"]
        assert c.get(f"{P}/jobs", params={"status": "weird"}, headers=hdr(k1)).status_code == 422
        assert c.get(f"{P}/jobs/job_zzz", headers=hdr(k1)).status_code == 404


def test_queue_per_client_is_capped_and_pagination_covers_all_jobs(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl, key = gen_client(c, ["protocols:generate", "jobs:read"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        codes = [c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol", "instruction": f"i{i}"}, headers=hdr(key)).status_code for i in range(7)]
        assert codes == [202] * 5 + [429] * 2, codes
        r = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(key))
        assert err(r)["code"] == "too_many_jobs" and r.headers["retry-after"] == "30"
        seen, cur = [], None
        for _ in range(10):
            page = c.get(f"{P}/jobs", params={"limit": 2, **({"after": cur} if cur else {})}, headers=hdr(key)).json()
            seen += [j["id"] for j in page["items"]]
            cur = page["next_cursor"]
            if not cur:
                break
        assert len(seen) == 5 == len(set(seen))
        drain_jobs(c)
        assert c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(key)).status_code == 202, "после выполнения очередь снова свободна"


def test_map_job_runs_through_the_same_queue(tmp_path, directory, monkeypatch):
    with hook_app(tmp_path, directory, Hook()) as c:
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl, key = gen_client(c, ["maps:generate", "jobs:read", "maps:read"])
        enable(c)
        maps = c.app_obj.state.protocols.maps

        async def fake_run(map_id):
            async with c.app_obj.state.session_maker() as db:
                rec = await db.get(ConversationMap, map_id)
                rec.status, rec.data = "ready", {"topics": []}
                await db.commit()
        monkeypatch.setattr(maps, "run", fake_run)
        pm = ids.pub("meeting", uuid.UUID(mid))
        r = c.post(f"{P}/meetings/{pm}/map", headers=hdr(key))
        assert r.status_code == 202 and r.json()["kind"] == "map"
        drain_jobs(c)
        done = c.get(f"{P}/jobs/{r.json()['id']}", headers=hdr(key)).json()
        assert done["status"] == "completed" and done["result"]["map_id"].startswith("map_")
        assert c.get(f"{P}/meetings/{pm}/map", headers=hdr(key)).json()["status"] == "ready"


def test_failed_generation_is_reported_as_a_failed_job_with_a_reason(tmp_path, directory):
    def llm_down(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})
    with running_app(make_settings(tmp_path), directory, transports={"anonymizer": httpx.MockTransport(anon_ok), "llm": httpx.MockTransport(llm_down)}) as c:
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl, key = gen_client(c, ["protocols:generate", "jobs:read"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        j = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=hdr(key)).json()
        drain_jobs(c)
        done = c.get(f"{P}/jobs/{j['id']}", headers=hdr(key)).json()
        assert done["status"] == "failed" and done["error"] and done["result"] is None


def test_interrupted_jobs_are_failed_after_restart_and_queued_ones_survive(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl, key = gen_client(c, ["protocols:generate", "jobs:read"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        a = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol", "instruction": "a"}, headers=hdr(key)).json()
        b = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol", "instruction": "b"}, headers=hdr(key)).json()
        job_a = uuid.UUID(a["id"][4:])

        async def stuck():
            async with c.app_obj.state.session_maker() as db:
                j = await db.get(ApiJob, job_a)
                j.status = "processing"
                await db.commit()
            return await c.app_obj.state.jobs.recover()
        assert run(c, stuck) == 1
        assert c.get(f"{P}/jobs/{a['id']}", headers=hdr(key)).json()["status"] == "failed" and "перезапуск" in c.get(f"{P}/jobs/{a['id']}", headers=hdr(key)).json()["error"]
        drain_jobs(c)
        assert c.get(f"{P}/jobs/{b['id']}", headers=hdr(key)).json()["status"] == "completed"


# ================================================================================================ идемпотентность
def test_idempotency_key_replays_the_same_response_and_detects_conflicts(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl, key = gen_client(c, ["protocols:generate", "summaries:generate", "jobs:read"])
        cl2, key2 = gen_client(c, ["protocols:generate", "jobs:read"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        url = f"{P}/meetings/{pm}/documents"
        h = {**hdr(key), "Idempotency-Key": "order-42"}
        r1 = c.post(url, json={"kind": "protocol"}, headers=h)
        r2 = c.post(url, json={"kind": "protocol"}, headers=h)
        assert r1.status_code == r2.status_code == 202 and r1.json() == r2.json() and r2.headers["idempotent-replay"] == "true" and "idempotent-replay" not in r1.headers
        assert r2.headers["location"] == r1.headers["location"]
        assert len(c.get(f"{P}/jobs", headers=hdr(key)).json()["items"]) == 1, "повтор не создал вторую задачу"
        conflict = c.post(url, json={"kind": "summary"}, headers=h)
        assert conflict.status_code == 409 and err(conflict)["code"] == "idempotency_conflict"
        other = c.post(url, json={"kind": "protocol"}, headers={**hdr(key2), "Idempotency-Key": "order-42"})
        assert other.status_code == 202 and other.json()["id"] != r1.json()["id"], "ключ принадлежит интеграции: чужой тот же ключ не пересекается"
        for bad in ("", "x" * 65, "a b", "a/b", "k;rm"):
            r = c.post(url, json={"kind": "protocol"}, headers={**hdr(key), "Idempotency-Key": bad})
            assert r.status_code == 400 and err(r)["code"] == "invalid_idempotency_key", bad
        drain_jobs(c)


def test_a_failed_request_does_not_burn_the_idempotency_key(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        room, mid = meeting_with_two(c)
        cl, key = gen_client(c, ["protocols:generate", "jobs:read"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))
        h = {**hdr(key), "Idempotency-Key": "retry-me"}
        assert c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=h).status_code == 409        # встреча ещё идёт
        end_by_alice(c, mid)
        configure_llm(c)
        r = c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers=h)
        assert r.status_code == 202 and "idempotent-replay" not in r.headers


def test_parallel_requests_with_one_key_create_exactly_one_job(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        configure_llm(c)
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl, key = gen_client(c, ["protocols:generate", "jobs:read"])
        enable(c)
        pm = ids.pub("meeting", uuid.UUID(mid))

        def fire(_):
            return c.post(f"{P}/meetings/{pm}/documents", json={"kind": "protocol"}, headers={**hdr(key), "Idempotency-Key": "same-key"})
        with ThreadPoolExecutor(8) as ex:
            rs = list(ex.map(fire, range(8)))
        codes = sorted(r.status_code for r in rs)
        assert 202 in codes and set(codes) <= {202, 409}, codes
        assert [r for r in rs if r.status_code == 409 and err(r)["code"] != "idempotency_in_progress"] == []
        assert len(c.get(f"{P}/jobs", headers=hdr(key)).json()["items"]) == 1


# ================================================================================================ скачивание записей
def seed_recording(c, meeting_id: str, content: bytes = b"RIFF....WAVEdata" + b"\x00" * 64) -> uuid.UUID:
    root = Path(c.app_obj.state.settings.recordings_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / "r1.wav").write_bytes(content)

    async def go():
        async with c.app_obj.state.session_maker() as db:
            m = await db.get(Meeting, uuid.UUID(meeting_id))
            rec = Recording(meeting_id=m.id, room_id=m.room_id, participant_identity="u-x", path="r1.wav", size_bytes=len(content), export_status="local", file_state="ok")
            db.add(rec)
            await db.commit()
            return rec.id
    return run(c, go)


def test_download_link_is_short_lived_signed_keyless_and_rechecked_on_use(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        data = b"RIFF1234WAVEfmt " + bytes(range(64))
        rid = seed_recording(c, mid, data)
        other = make_room(c)
        cl, key = make_client(c, name="dl", scopes=["meetings:read", "recordings:read", "recordings:download"])
        weak = make_client(c, name="weak", scopes=["meetings:read", "recordings:read"])[1]
        enable(c)
        pm, pr = ids.pub("meeting", uuid.UUID(mid)), ids.pub("recording", rid)
        assert c.post(f"{P}/meetings/{pm}/recordings/{pr}/download-url", headers=hdr(weak)).status_code == 403
        r = c.post(f"{P}/meetings/{pm}/recordings/{pr}/download-url", headers=hdr(key))
        assert r.status_code == 200
        out = r.json()
        assert out["expires_in_s"] == 300 and out["size_bytes"] == len(data) and key not in r.text and key.split("_")[1] not in out["url"], "ключа API в ссылке нет"
        got = c.get(out["url"])                                         # без Authorization
        assert got.status_code == 200 and got.content == data and got.headers["content-type"] == "audio/wav" and "no-store" in got.headers["cache-control"]
        assert c.get(out["url"][:-3] + "abc").status_code == 404, "подпись подделана"
        assert c.get(f"{P}/downloads/{out['url'].rsplit('/', 1)[1].split('.')[0]}.{'0' * 64}").status_code == 404
        assert c.get(f"{P}/downloads/garbage").status_code == 404 and c.get(f"{P}/downloads/a.b.c").status_code == 404
        s = c.app_obj.state.settings
        expired, _ = downloads.issue(s.app_master_key, s.internal_api_token, client_id=cl["id"], recording_id=str(rid), ttl_s=10, now=time.time() - 100)
        r410 = c.get(f"{P}/downloads/{expired}")
        assert r410.status_code == 410 and err(r410)["code"] == "link_expired"
        # права проверяются заново при каждом использовании
        login(c, "root")
        c.patch(f"/api/v1/admin/public-api/clients/{cl['id']}", json={"name": "dl", "scopes": ["meetings:read"], "enabled": True})
        assert c.get(out["url"]).status_code == 403
        c.patch(f"/api/v1/admin/public-api/clients/{cl['id']}", json={"name": "dl", "scopes": ["recordings:download"], "enabled": True, "rooms": [other["id"]]})
        assert c.get(out["url"]).status_code == 404, "область комнат сузили — ссылка перестала открывать запись"
        c.patch(f"/api/v1/admin/public-api/clients/{cl['id']}", json={"name": "dl", "scopes": ["recordings:download"], "enabled": False})
        assert c.get(out["url"]).status_code == 403
        c.patch(f"/api/v1/admin/public-api/clients/{cl['id']}", json={"name": "dl", "scopes": ["recordings:download"], "enabled": True})
        assert c.get(out["url"]).status_code == 200
        c.delete(f"/api/v1/admin/public-api/clients/{cl['id']}")
        assert c.get(out["url"]).status_code == 404, "интеграцию удалили — ссылки мертвы"
        actions = [a["action"] for a in c.get("/api/v1/admin/audit").json()]
        assert "api.recording.download_url" in actions and "api.recording.download" in actions


def test_download_link_is_bound_to_its_recording_meeting_and_missing_files_are_gone(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        rid = seed_recording(c, mid)
        room2, mid2 = meeting_with_two(c)
        cl, key = make_client(c, name="dl", scopes=["recordings:download", "meetings:read"])
        enable(c)
        pm2, pr = ids.pub("meeting", uuid.UUID(mid2)), ids.pub("recording", rid)
        assert c.post(f"{P}/meetings/{pm2}/recordings/{pr}/download-url", headers=hdr(key)).status_code == 404, "запись чужой встречи через другую встречу не выдаётся"
        assert c.post(f"{P}/meetings/{ids.pub('meeting', uuid.UUID(mid))}/recordings/{ids.pub('recording', uuid.uuid4())}/download-url", headers=hdr(key)).status_code == 404

        async def gone():
            async with c.app_obj.state.session_maker() as db:
                rec = await db.get(Recording, rid)
                rec.file_state = "missing"
                await db.commit()
        run(c, gone)
        r = c.post(f"{P}/meetings/{ids.pub('meeting', uuid.UUID(mid))}/recordings/{pr}/download-url", headers=hdr(key))
        assert r.status_code == 410 and err(r)["code"] == "file_missing"


def test_download_class_has_its_own_limit(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        rid = seed_recording(c, mid)
        cl, key = make_client(c, name="dl", scopes=["recordings:download", "meetings:read"])
        put_settings(c, "api", enabled=True, rate_download=2)
        c.cookies.clear()
        c.app_obj.state.api_cfg_cache = None
        url = f"{P}/meetings/{ids.pub('meeting', uuid.UUID(mid))}/recordings/{ids.pub('recording', rid)}/download-url"
        codes = [c.post(url, headers=hdr(key)).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        assert c.get(f"{P}/meetings/{ids.pub('meeting', uuid.UUID(mid))}", headers=hdr(key)).status_code == 200, "чтение считается в своём классе"


# ================================================================================================ некорректный ввод
def test_malformed_input_never_causes_a_500(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        cl, key = gen_client(c, ["protocols:generate", "jobs:read", "meetings:read", "transcripts:read"])
        enable(c)
        H = hdr(key)
        pm = ids.pub("meeting", uuid.UUID(mid))
        url = f"{P}/meetings/{pm}/documents"
        cases = [
            dict(content=b"{not json", headers={**H, "content-type": "application/json"}),
            dict(content=b"[1,2,3]", headers={**H, "content-type": "application/json"}),
            dict(content=b'{"kind": "protocol", "instruction": 5}', headers={**H, "content-type": "application/json"}),
            dict(content=b'{"kind": "nuke"}', headers={**H, "content-type": "application/json"}),
            dict(content=b'{"kind": "protocol", "instruction": "' + b"x" * 20001 + b'"}', headers={**H, "content-type": "application/json"}),
            dict(content=b"\xff\xfe\xfa", headers={**H, "content-type": "application/json"}),
            dict(content=b'{"kind": "protocol"}', headers={**H, "content-type": "text/plain"}),
            dict(content=b"", headers=H),
            dict(content=b'{"kind": null}', headers={**H, "content-type": "application/json"}),
            dict(content=b"null", headers={**H, "content-type": "application/json"}),
            dict(content=('{"kind": "protocol", "instruction": "' + "\\u0000" * 50 + '"}').encode(), headers={**H, "content-type": "application/json"}),
        ]
        for case in cases:
            r = c.post(url, **case)
            assert r.status_code in (202, 400, 409, 415, 422), (case["content"][:30], r.status_code, r.text[:120])
            if r.status_code >= 400:
                assert "error" in r.json() and "Traceback" not in r.text
        weird_paths = [f"{P}/meetings/{'a' * 5000}", f"{P}/meetings/%00", f"{P}/meetings/mtg_%E2%82%AC", f"{P}/jobs/{'9' * 3000}", f"{P}/meetings/mtg_{'0' * 32}/transcript?limit=-5",
                       f"{P}/meetings?started_after=2026-13-45", f"{P}/meetings?limit=abc"]
        for pth in weird_paths:
            r = c.get(pth, headers=H)
            assert r.status_code in (400, 404, 422), (pth[:60], r.status_code)
            assert "error" in r.json()


# ================================================================================================ параллельные запросы, нагрузка, отказы
def test_parallel_requests_are_counted_exactly_per_client(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        _, ka = make_client(c, name="a", scopes=["rooms:read"])
        _, kb = make_client(c, name="b", scopes=["rooms:read"])
        put_settings(c, "api", enabled=True, rate_read=10)
        c.cookies.clear()
        c.app_obj.state.api_cfg_cache = None

        def hit(k):
            return c.get(f"{P}/me", headers=hdr(k)).status_code
        with ThreadPoolExecutor(16) as ex:
            ra = list(ex.map(hit, [ka] * 40))
            rb = list(ex.map(hit, [kb] * 5))
        assert ra.count(200) == 10 and ra.count(429) == 30, "ровно лимит, без «лишних» при параллельных запросах"
        assert rb == [200] * 5, "лимит одной интеграции не затрагивает другую"


def test_large_meeting_list_and_oversized_transcript(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        room = make_room(c)
        users_ready(c)
        owner = uid(c, "alice")

        async def bulk():
            async with c.app_obj.state.session_maker() as db:
                base = utcnow() - dt.timedelta(days=400)
                rows = [{"id": uuid.uuid4(), "room_id": uuid.UUID(room["id"]), "livekit_room": f"m-{uuid.uuid4().hex}", "started_at": base + dt.timedelta(minutes=i),
                         "ended_at": base + dt.timedelta(minutes=i, seconds=30), "end_reason": "manual", "transcription_enabled": True, "record_audio": False, "created_at": base} for i in range(1200)]
                await db.execute(insert(Meeting), rows)
                big = rows[0]["id"]
                segs = [{"segment_uid": uuid.uuid4(), "meeting_id": big, "room_id": uuid.UUID(room["id"]), "user_id": owner, "participant_identity": "x",
                         "started_at": base + dt.timedelta(seconds=i), "ended_at": base + dt.timedelta(seconds=i, milliseconds=900), "text": f"r{i}", "created_at": base} for i in range(20001)]
                await db.execute(insert(TranscriptSegment), segs)
                await db.commit()
                return big
        big = run(c, bulk)
        cl, key = make_client(c, scopes=ALL2)
        put_settings(c, "api", enabled=True, rate_read=100000, max_page_size=500)
        c.cookies.clear()
        c.app_obj.state.api_cfg_cache = None
        t0, seen, cur, pages = time.perf_counter(), [], None, 0
        while True:
            page = c.get(f"{P}/meetings", params={"limit": 500, **({"after": cur} if cur else {})}, headers=hdr(key)).json()
            seen += [m["id"] for m in page["items"]]
            cur, pages = page["next_cursor"], pages + 1
            if not cur:
                break
        walk = time.perf_counter() - t0
        assert len(seen) == 1200 == len(set(seen)) and pages == 3 and walk < 20, f"обход 1200 встреч: {walk:.1f} с"
        pbig = ids.pub("meeting", big)
        t1 = time.perf_counter()
        first = c.get(f"{P}/meetings/{pbig}/transcript", params={"limit": 500}, headers=hdr(key)).json()
        assert len(first["items"]) == 500 and first["next_cursor"] and time.perf_counter() - t1 < 5
        r = c.get(f"{P}/meetings/{pbig}/transcript", params={"format": "srt"}, headers=hdr(key))
        assert r.status_code == 413 and err(r)["code"] == "too_large", "текстовая выгрузка ограничена 20 000 реплик"
        cnt, cur = 0, None
        for _ in range(60):
            page = c.get(f"{P}/meetings/{pbig}/transcript", params={"limit": 500, **({"after": cur} if cur else {})}, headers=hdr(key)).json()
            cnt += len(page["items"])
            cur = page["next_cursor"]
            if not cur:
                break
        assert cnt == 20001, "курсором можно забрать всю стенограмму"


def test_database_outage_gives_a_clean_500_and_does_not_break_event_queueing(tmp_path, directory):
    from sqlalchemy.exc import OperationalError
    with hook_app(tmp_path, directory, Hook()) as c:
        _, key = make_client(c, scopes=["rooms:read"])
        enable(c)
        assert c.get(f"{P}/me", headers=hdr(key)).status_code == 200
        real = c.app_obj.state.session_maker

        def broken():
            raise OperationalError("SELECT 1", {}, Exception("database is locked secret-detail"))
        c.app_obj.state.session_maker = broken
        c.app_obj.state.api_cfg_cache = None
        r = c.get(f"{P}/me", headers=hdr(key))
        assert r.status_code == 500 and err(r)["code"] == "internal_error" and "secret-detail" not in r.text and "Traceback" not in r.text
        svc = c.app_obj.state.webhooks
        svc._sm = broken
        svc.emit_safe("meeting.ended", {"x": 1})                  # сбой БД при постановке события не должен бросать исключение
        run(c, svc.drain)
        c.app_obj.state.session_maker = real
        svc._sm = real
        c.app_obj.state.api_cfg_cache = None
        assert c.get(f"{P}/me", headers=hdr(key)).status_code == 200, "после восстановления БД API работает"


def test_wrong_method_and_head_options_do_not_leak_or_crash(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        _, key = make_client(c, scopes=ALL2)
        enable(c)
        H = hdr(key)
        for method, path in (("PUT", "/me"), ("DELETE", "/jobs"), ("PATCH", "/meetings/mtg_" + "0" * 32), ("POST", "/me"), ("GET", "/downloads/")):
            r = c.request(method, f"{P}{path}", headers=H)
            assert r.status_code in (404, 405) and "Traceback" not in r.text
        assert c.options(f"{P}/me", headers={"Origin": "https://evil.example.net", "Access-Control-Request-Method": "GET"}).headers.get("access-control-allow-origin") is None


def test_openapi_describes_stage2_and_every_scope_is_used(tmp_path, directory):
    with hook_app(tmp_path, directory, Hook()) as c:
        spec = c.get(f"{P}/openapi.json").json()
        paths = spec["paths"]
        for p in ("/meetings/{meeting_id}/documents", "/meetings/{meeting_id}/map", "/jobs", "/jobs/{job_id}", "/jobs/{job_id}/cancel", "/downloads/{token}",
                  "/meetings/{meeting_id}/recordings/{recording_id}/download-url"):
            assert f"{P}{p}" in paths, p
        assert "202" in paths[f"{P}/meetings/{{meeting_id}}/documents"]["post"]["responses"]
        used = set()
        for item in paths.values():
            for op in item.values():
                if op.get("x-required-scope"):
                    used.add(op["x-required-scope"])
                used |= set(op.get("x-required-scopes") or [])
        from app.publicapi.scopes import SCOPES
        assert set(SCOPES) - used - {"protocols:read", "summaries:read"} == set(), "каждое объявленное право проверяется маршрутом"
        assert "webhooks" in spec and "meeting.ended" in json.dumps(spec["webhooks"]), "в OpenAPI описаны события webhook"
