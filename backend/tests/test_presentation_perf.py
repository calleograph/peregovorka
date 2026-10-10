"""Презентационная комната и производительность: рассылка событий, общий подписчик Redis, нагрузка хоста, очередь тяжёлых задач,
проверка дорожек при отзыве слова, права зрителя при прямых вызовах API, страница «Производительность»."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.services import events, livekit, perf

from .conftest import OTHER_GROUP, login, make_room
from .test_roles_floor import LEADERS, grants, lk_calls, presentation  # noqa: F401 — фикстура lk_calls
from .test_transcripts import _join

API = "/api/v1"


# ------------------------------------------------------------------------------------------ события входа/выхода зрителей
@pytest.fixture
def published(monkeypatch):
    seen: list[dict] = []
    real = events.publish

    async def spy(redis, meeting_id, event):
        seen.append(event)
        await real(redis, meeting_id, event)

    monkeypatch.setattr(events, "publish", spy)
    return seen


def test_presentation_viewer_join_and_leave_are_not_broadcast_but_leader_and_regular_room_are(client, lk_calls, published):  # noqa: F811
    room = presentation(client)
    carol = _join(client, "carol", room["id"])                       # руководитель
    alice = _join(client, "alice", room["id"])                       # зритель
    joined = [e for e in published if e["type"] == "participant_joined"]
    assert [e["user_id"] for e in joined] == [carol["identity"][2:].replace("-", "")] or len(joined) == 1, "приход зрителя не рассылается, руководителя — рассылается"
    assert "display_name" in joined[0] and joined[0]["display_name"] != "Alice A"
    login(client, "alice")
    assert client.post(f"{API}/meetings/{alice['meeting_id']}/leave").status_code in (200, 204)
    assert not [e for e in published if e["type"] == "participant_left"], "уход зрителя не рассылается"
    login(client, "carol")
    client.post(f"{API}/meetings/{carol['meeting_id']}/leave")
    assert [e for e in published if e["type"] == "participant_left"], "уход руководителя рассылается"
    published.clear()
    reg = make_room(client)
    _join(client, "alice", reg["id"])
    assert [e for e in published if e["type"] == "participant_joined"], "в обычной комнате всё как раньше"


def test_typing_is_silent_in_presentation_and_hand_events_are_capped(client, lk_calls, published, monkeypatch):  # noqa: F811
    from app.api import collab

    room = presentation(client)
    _join(client, "carol", room["id"])
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    login(client, "alice")
    assert client.post(f"{API}/meetings/{mid}/chat/typing", json={"typing": True}).status_code == 204
    assert not [e for e in published if e["type"] == "chat_typing"], "в презентации «печатает…» не рассылается"
    monkeypatch.setattr(collab, "HAND_EVENT_QUEUE_MAX", 1)
    _join(client, "bob", room["id"])
    login(client, "alice")
    client.post(f"{API}/meetings/{mid}/hand", json={"raised": True})
    login(client, "bob")
    client.post(f"{API}/meetings/{mid}/hand", json={"raised": True})
    hands = [e for e in published if e["type"] == "hand_changed"]
    assert hands[0]["queue"] is not None and len(hands[0]["queue"]) == 1, "малая очередь уходит целиком — как раньше"
    assert hands[1]["queue"] is None and hands[1]["total"] == 2 and hands[1]["at"] > 0, "большая очередь — только изменение, а не весь список всем"


# ------------------------------------------------------------------------------------------ права зрителя: прямые вызовы API
def test_viewer_cannot_do_leader_things_through_the_api_directly(client, lk_calls):  # noqa: F811
    room = presentation(client)
    carol = _join(client, "carol", room["id"])
    alice = _join(client, "alice", room["id"])
    bob = _join(client, "bob", room["id"])
    mid = alice["meeting_id"]
    login(client, "alice")
    assert client.post(f"{API}/meetings/{mid}/moderation/floor", json={"identity": bob["identity"], "granted": True}).status_code == 403, "зритель не даёт слово"
    assert client.post(f"{API}/meetings/{mid}/moderation/floor", json={"identity": alice["identity"], "granted": True}).status_code == 403, "и себе тоже"
    assert client.post(f"{API}/meetings/{mid}/moderation/kick", json={"identity": bob["identity"]}).status_code == 403
    assert client.post(f"{API}/meetings/{mid}/moderation/mute-all", json={}).status_code in (403, 404)
    assert client.post(f"{API}/meetings/{mid}/end").status_code == 403, "завершить встречу для всех"
    assert client.post(f"{API}/meetings/{mid}/recording", json={"enabled": True}).status_code == 403, "управление записью"
    assert client.post(f"{API}/meetings/{mid}/transcription", json={"enabled": False}).status_code == 403
    assert client.post(f"{API}/meetings/{mid}/whiteboard/patch", json={"patch": {"x": 1}}).status_code == 403, "доска"
    assert client.put(f"{API}/meetings/{mid}/stage", json={"items": [{"type": "board"}]}).status_code in (403, 404, 405)
    assert not lk_calls["perm"], "ни одно право в звонке не менялось"
    g = grants(alice["token"])
    assert g.can_publish is False and list(g.can_publish_sources) == [] and g.can_subscribe is True, "токен зрителя: только просмотр"
    login(client, "carol")
    assert carol["client"]["can_manage"] is True


def test_floor_holder_is_not_a_leader(client, lk_calls):  # noqa: F811
    room = presentation(client)
    _join(client, "carol", room["id"])
    a = _join(client, "alice", room["id"])
    b = _join(client, "bob", room["id"])
    mid = a["meeting_id"]
    login(client, "carol")
    assert client.post(f"{API}/meetings/{mid}/moderation/floor", json={"identity": a["identity"], "granted": True}).status_code == 200
    again = _join(client, "alice", room["id"])
    assert again["client"]["floor"] is True and again["client"]["can_manage"] is False and again["client"]["can_control"] is False
    login(client, "alice")
    assert client.post(f"{API}/meetings/{mid}/moderation/floor", json={"identity": b["identity"], "granted": True}).status_code == 403, "слово не даёт управлять словом"
    assert client.post(f"{API}/meetings/{mid}/end").status_code == 403
    assert client.post(f"{API}/meetings/{mid}/recording", json={"enabled": True}).status_code == 403
    assert client.post(f"{API}/meetings/{mid}/moderation/kick", json={"identity": b["identity"]}).status_code == 403
    login(client, "carol")      # слово забрали — старый токен и новый вход прав не возвращают
    client.post(f"{API}/meetings/{mid}/moderation/floor", json={"identity": a["identity"], "granted": False})
    after = _join(client, "alice", room["id"])
    assert after["client"]["floor"] is False and after["client"]["sources"] == [] and grants(after["token"]).can_publish is False


# ------------------------------------------------------------------------------------------ проверка дорожек после отзыва слова
class _Track(SimpleNamespace):
    pass


class _FakeRoomApi:
    def __init__(self, script):
        self.script, self.calls = script, []

    async def get_participant(self, req):
        self.calls.append(("get", req.identity))
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if step is None:
            raise RuntimeError("not_found")
        return SimpleNamespace(tracks=step)

    async def mute_published_track(self, req):
        self.calls.append(("mute", req.track_sid))

    async def remove_participant(self, req):
        self.calls.append(("remove", req.identity))


def _patch_lk(monkeypatch, script):
    room = _FakeRoomApi(script)

    class FakeLk:
        def __init__(self, *a, **k):
            self.room = room

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(livekit.lkapi, "LiveKitAPI", FakeLk)
    return room


@pytest.fixture
def lk_settings(settings):
    return settings


def test_enforce_sources_clean_participant_is_left_alone(monkeypatch, settings):
    room = _patch_lk(monkeypatch, [[]])
    res = asyncio.run(livekit.enforce_sources(settings, "m-1", "u-1", [], pause=0))
    assert res == {"stopped": [], "removed": False, "checked": True} and [c[0] for c in room.calls] == ["get"]


def test_enforce_sources_waits_for_the_client_then_removes_stubborn_participant(monkeypatch, settings):
    from livekit.protocol import models as m

    shared = _Track(source=m.TrackSource.SCREEN_SHARE, muted=False, sid="TR_1")
    gone = _patch_lk(monkeypatch, [[shared], []])
    res = asyncio.run(livekit.enforce_sources(settings, "m-1", "u-1", [], pause=0))
    assert res["removed"] is False and res["stopped"] == ["screen_share"], "дорожка исчезла сама (клиент выключил) — отключать не нужно"
    assert ("mute", "TR_1") in gone.calls and not any(c[0] == "remove" for c in gone.calls)
    stubborn = _patch_lk(monkeypatch, [[shared]])
    res = asyncio.run(livekit.enforce_sources(settings, "m-1", "u-1", [], tries=3, pause=0))
    assert res["removed"] is True and ("remove", "u-1") in stubborn.calls, "изменённый клиент не снял дорожку — участник отключается"
    allowed = _patch_lk(monkeypatch, [[_Track(source=m.TrackSource.MICROPHONE, muted=False, sid="TR_2")]])
    res = asyncio.run(livekit.enforce_sources(settings, "m-1", "u-1", ["microphone"], pause=0))
    assert res["removed"] is False and res["stopped"] == [] and not any(c[0] in ("mute", "remove") for c in allowed.calls), "разрешённое не трогаем"
    absent = _patch_lk(monkeypatch, [None])
    assert asyncio.run(livekit.enforce_sources(settings, "m-1", "u-1", [], pause=0))["checked"] is True and absent.calls == [("get", "u-1")]


# ------------------------------------------------------------------------------------------ общий подписчик событий
class _FakePubSub:
    def __init__(self):
        self.subscribed, self.channels = False, set()

    async def subscribe(self, ch):
        self.channels.add(ch)
        self.subscribed = True

    async def unsubscribe(self, ch):
        self.channels.discard(ch)
        self.subscribed = bool(self.channels)


def test_event_hub_fans_out_filters_leaders_only_and_drops_slow_receivers():
    async def run():
        hub = events.EventHub(None, queue_max=3)
        hub._pubsub = _FakePubSub()
        ch = "meeting:1:events"
        leader, viewer, slow = hub.new_subscriber(), hub.new_subscriber(), hub.new_subscriber()
        await hub.subscribe(leader, ch, leader=True)
        await hub.subscribe(viewer, ch)
        await hub.subscribe(slow, ch)
        assert hub._pubsub.channels == {ch}, "канал Redis подписывается один раз на всех"
        hub._deliver(ch, '{"type":"a"}')
        hub._deliver(ch, '{"type":"board","leaders_only":true}')
        assert leader.queue.qsize() == 2 and viewer.queue.qsize() == 1, "закрытая доска — только руководителям"
        for _ in range(3):
            await viewer.queue.get() if not viewer.queue.empty() else None
            await leader.queue.get() if not leader.queue.empty() else None
        for i in range(5):
            hub._deliver(ch, f'{{"type":"x{i}"}}')
            if not viewer.queue.empty():
                viewer.queue.get_nowait()
            if not leader.queue.empty():
                leader.queue.get_nowait()
        assert slow.dropped is True and slow.queue.qsize() == 1 and slow.queue.get_nowait() is None, "медленный получатель отключён, очередь не растёт"
        assert not viewer.dropped and not leader.dropped
        assert hub.snapshot()["slow_dropped"] == 1
        await hub.release(leader)
        await hub.release(viewer)
        assert hub._pubsub.channels == {ch}
        await hub.release(slow)
        assert hub._pubsub.channels == set() and hub.snapshot()["channels"] == 0, "последний ушёл — подписка Redis снята"

    asyncio.run(run())


def test_ws_events_flow_through_the_shared_hub(client, lk_calls):  # noqa: F811
    room = presentation(client)
    carol = _join(client, "carol", room["id"])
    alice = _join(client, "alice", room["id"])
    mid = carol["meeting_id"]
    login(client, "alice")
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"type": "subscribe", "meeting_id": mid})
        assert ws.receive_json()["type"] == "subscribed"
        login(client, "carol")
        client.post(f"{API}/meetings/{mid}/moderation/floor", json={"identity": alice["identity"], "granted": True})
        ev = ws.receive_json()
        assert ev["type"] == "floor_changed" and ev["granted"] is True
    assert client.app.state.event_hub.snapshot()["messages"] >= 1


# ------------------------------------------------------------------------------------------ нагрузка хоста и очередь тяжёлых задач
def _proc(tmp_path, busy, idle, load="0.10 0.10 0.10 1/100 1", avail=800, total=1000):
    (tmp_path / "stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\n")
    (tmp_path / "loadavg").write_text(load)
    (tmp_path / "meminfo").write_text(f"MemTotal: {total} kB\nMemAvailable: {avail} kB\n")


def test_host_monitor_reads_proc_and_reports_unavailable_as_none(tmp_path):
    _proc(tmp_path, 100, 900)
    mon = perf.HostMonitor(str(tmp_path), str(tmp_path / "nocg"), min_interval=0)
    first = mon.sample()
    assert first.cpu_pct is None, "CPU считается по разности двух чтений"
    _proc(tmp_path, 400, 1200)
    second = mon.sample()
    assert second.cpu_pct == 50.0 and second.mem_available_pct == 80.0 and second.container_cpu_pct is None and second.container_mem is None
    empty = perf.HostMonitor(str(tmp_path / "none"), str(tmp_path / "none"), min_interval=0).sample()
    assert empty.cpu_pct is None and empty.mem_total is None and empty.load1 is None, "нет /proc — «Нет данных», а не ошибка"


def test_assess_levels_and_reasons():
    def host(**kw):
        base = dict(cpu_pct=20.0, cpus=8, load1=1.0, load5=1.0, load15=1.0, mem_total=1000, mem_available=700, container_cpu_pct=None, container_mem=None, container_mem_limit=None)
        return perf.HostSample(**{**base, **kw})

    assert perf.assess(host())["level"] == "ok"
    assert perf.assess(host(load1=7.5))["level"] == "busy"
    r = perf.assess(host(load1=12.0, cpu_pct=95.0), active_meetings=2)
    assert r["level"] == "overloaded" and r["active_meetings"] == 2 and any("процессор" in x for x in r["reasons"])
    assert perf.assess(host(mem_available=40))["level"] == "overloaded"
    assert perf.assess(host(), asr_queue=10)["level"] == "busy" and perf.assess(host(), asr_queue=30)["level"] == "overloaded"
    assert perf.assess(host(cpu_pct=None, load1=None, mem_total=None, mem_available=None))["level"] == "ok", "нет данных — не выдумываем перегрузку"


def test_heavy_gate_defers_only_when_a_meeting_is_running_and_never_starves():
    async def run():
        state = {"level": "overloaded", "active_meetings": 0}

        async def status():
            return dict(state)

        gate = perf.HeavyGate(status, concurrency=1, poll=0.01, max_defer=0.1)
        t0 = time.monotonic()
        async with gate.slot("mix"):
            pass
        assert time.monotonic() - t0 < 0.05 and gate.stats.deferred == 0, "встреч нет — без задержки даже при перегрузке"
        state["active_meetings"] = 1
        t0 = time.monotonic()
        async with gate.slot("mix"):
            pass
        assert time.monotonic() - t0 >= 0.1 and gate.stats.forced == 1 and gate.stats.deferred == 1, "идёт встреча и перегрузка — ждёт, но не бесконечно"
        state["level"] = "busy"
        t0 = time.monotonic()
        async with gate.slot("protocol", defer_at="overloaded"):
            pass
        assert time.monotonic() - t0 < 0.05, "протокол уступает только при перегрузке, не при «занят»"
        state["level"] = "ok"
        running, peak = 0, 0
        gate2 = perf.HeavyGate(status, concurrency=2, poll=0.01)

        async def work():
            nonlocal running, peak
            async with gate2.slot("waveform"):
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.02)
                running -= 1

        await asyncio.gather(*[work() for _ in range(6)])
        assert peak == 2 and gate2.snapshot()["started"] == 6 and gate2.snapshot()["running"] == 0, "одновременно — не больше заданного"

    asyncio.run(run())


def test_prometheus_parser_and_rate_tracker():
    text = '# HELP x\nlivekit_packet_bytes{direction="incoming",node="a"} 1000\nlivekit_packet_bytes{direction="outgoing"} 5000\nother 1\nlivekit_room_total{a="b"} 2\n'
    m = perf.parse_prometheus(text, {"livekit_packet_bytes": "bytes", "livekit_room_total": "rooms"})
    assert m["bytes"] == [({"direction": "incoming", "node": "a"}, 1000.0), ({"direction": "outgoing"}, 5000.0)] and m["rooms"][0][1] == 2.0
    rt = perf.RateTracker()
    assert rt.rate("k", 1000, now=10.0) is None
    assert rt.rate("k", 3000, now=12.0) == 1000.0
    assert rt.rate("k", 10, now=14.0) is None, "сброс счётчика (перезапуск LiveKit) — не отрицательная скорость"
    assert rt.rate("k", None) is None


# ------------------------------------------------------------------------------------------ страница «Производительность»
def test_performance_endpoint_is_admin_only_and_degrades_to_nulls(client):
    login(client, "alice")
    assert client.get(f"{API}/admin/performance").status_code == 403
    login(client, "root")
    r = client.get(f"{API}/admin/performance")
    assert r.status_code == 200
    body = r.json()
    for key in ("assessment", "host", "rooms", "livekit", "asr", "jobs", "events", "webrtc", "kernel", "containers"):
        assert key in body, key
    assert body["assessment"]["level"] in ("ok", "busy", "overloaded")
    assert body["livekit"]["available"] is False and body["livekit"]["in_kbps"] is None, "звонковый сервер недоступен — «Нет данных», а не ошибка 500"
    assert body["jobs"]["gate"]["concurrency"] >= 1 and body["rooms"]["active_meetings"] == 0
    room = presentation(client)
    _join(client, "carol", room["id"])
    _join(client, "alice", room["id"])
    login(client, "root")
    rooms = client.get(f"{API}/admin/performance").json()["rooms"]
    assert rooms["active_meetings"] == 1 and rooms["participants"] == 2 and rooms["presentations"] == 1 and rooms["top"][0]["participants"] == 2
