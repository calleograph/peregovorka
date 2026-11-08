"""Диагностика эфира: разбор источников, выявление узких мест, очистка от секретов, права, журнал и выгрузки. Настоящая служба — tests/live/egress/run_diag.py."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models import LiveDiagEvent, LiveDiagSample
from app.services import live_diag as ld
from app.services.live_diag import aggregate_clients, clean, clean_client, clean_text, hls_stats, parse_playlist, parse_probe, read_cgroup_cpu, read_cores, read_disk, read_netdev, rules, stage_view, summarize, viewer_verdict

from .conftest import login, make_room
from .test_live import access, broadcasts, lv, room_live, start, viewer, vw  # noqa: F401
from .test_mail import LEADERS
from .test_transcripts import _join

KEY = "live_sk_0123456789abcdefSECRET"
CFG = {"max_cpu_load": 85, "min_free_gb": 10}


def ok_sample(**over) -> dict:
    s = {"t": time.time(), "status": "live", "quality": "1080p", "host": {"cpu": 30.0, "cpus": 8, "mem": {"avail_pct": 60.0, "used_pct": 40.0}, "disk": {"free": 500 * 1024**3, "util": 5.0, "await_ms": 1.0}, "net": {"tx_bps": 1e7, "cap_bps": None}},
         "svc": {"egress": {"st": "ok", "load": 30.0, "cores": 2.0}, "livekit": {"st": "ok", "loss_in": 0.0, "loss_out": 0.0}, "redis": {"st": "ok", "ms": 1.0}},
         "hls": {"age_s": 1.0, "dur_s": 1.5, "interval_s": 1.5, "ready_s": 1.6}, "media": {"fps": 30.0, "res": "1920x1080"}, "viewers": {"reporting": 10, "bad": 0, "worst": []}, "source": {"known": True, "video": 1, "max_w": 1920, "max_h": 1080}, "rtmp": []}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k] = {**s[k], **v}
        else:
            s[k] = v
    return s


def codes(s: dict) -> dict:
    return {f.code: f for f in rules(s, CFG)}


# ------------------------------------------------------------------------------------------------ источники
def test_host_readers_parse_kernel_formats(tmp_path):
    (tmp_path / "stat").write_text("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 50 0 50 350 50 0 0 0 0 0\ncpu1 50 0 50 350 50 0 0 0 0 0\nintr 1\n")
    (tmp_path / "diskstats").write_text("   8       0 sda 10 0 200 30 40 0 800 90 0 77 120 0 0 0 0 0 0\n")
    c = read_cores(str(tmp_path))
    assert c["cpu"] == (200, 1000) and set(c) == {"cpu", "cpu0", "cpu1"}, "ожидание ввода-вывода — не занятость"
    assert read_disk((8, 0), str(tmp_path)) == {"r": 10, "rs": 200, "rms": 30, "w": 40, "ws": 800, "wms": 90, "busy": 77}
    assert read_disk((8, 1), str(tmp_path)) is None and read_disk(None, str(tmp_path)) is None
    head = "Inter-|   Receive\n face |bytes\n"
    (tmp_path / "h").write_text(head + "  lo: 9 9 0 0 0 0 0 0 9 9 0 0 0 0 0 0\n eth0: 100 1 2 3 0 0 0 0 200 1 4 5 0 0 0 0\n veth1a: 7 7 0 0 0 0 0 0 7 7 0 0 0 0 0 0\n")
    (tmp_path / "c").write_text(head + " eth0: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0\n")
    assert read_netdev((str(tmp_path / "h"), str(tmp_path / "c"))) == ("host", {"eth0": [100, 2, 3, 200, 4, 5]})
    assert read_netdev((str(tmp_path / "нет"), str(tmp_path / "c")))[0] == "container", "нет файла хоста — честно видна только сеть контейнера"


def test_playlist_gets_missing_timestamps_from_the_previous_segment(tmp_path):
    txt = "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:3\n#EXT-X-PROGRAM-DATE-TIME:2026-11-05T09:30:36.000Z\n#EXTINF:1.5,\nseg_00003.ts\n#EXTINF:1.5,\nseg_00004.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:1.0,\nseg_00005.ts\n"
    p = parse_playlist(txt)
    t0 = datetime(2026, 11, 5, 9, 30, 36, tzinfo=timezone.utc).timestamp()
    assert [i["seq"] for i in p["items"]] == [3, 4, 5] and p["items"][1]["pdt"] == t0 + 1.5 and p["items"][2]["pdt"] == t0 + 3.0 and p["items"][2]["disc"] and not p["ended"]
    (tmp_path / "index.m3u8").write_text(txt)
    for n, name in enumerate(("seg_00003.ts", "seg_00004.ts", "seg_00005.ts")):
        f = tmp_path / name
        f.write_bytes(b"x" * 1000)
        import os
        os.utime(f, (t0 + 3 + n * 2, t0 + 3 + n * 2))
    h = hls_stats(tmp_path, now=t0 + 8)
    assert h["seq"] == 5 and h["age_s"] == 1.0 and h["interval_s"] == 2.0 and h["ready_s"] == 3.0 and h["disc"] == 1 and h["bytes"] == 1000
    assert hls_stats(tmp_path / "нет") is None


def test_probe_counts_frames_bitrate_gaps_and_audio_offset():
    v = [{"stream_index": 0, "pts_time": str(10 + i / 30), "size": 1000, "flags": "K_" if i == 0 else "__"} for i in range(30)]
    v[20:] = [{**p, "pts_time": str(float(p["pts_time"]) + 0.1)} for p in v[20:]]            # пропуск кадров
    a = [{"stream_index": 1, "pts_time": str(10.02 + i * 0.021), "size": 20, "flags": "K_"} for i in range(40)]
    r = parse_probe({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}, {"index": 1, "codec_type": "audio", "codec_name": "aac"}], "packets": v + a})
    assert r["res"] == "1920x1080" and r["frames"] == 30 and r["keyframes"] == 1 and r["gaps"] == 1 and r["av_ms"] == 20 and r["vcodec"] == "h264" and r["fps"] < 30
    assert parse_probe({"streams": [], "packets": []}) is None


def test_egress_cpu_file_is_ignored_when_stale(tmp_path):
    import os
    f = tmp_path / "egress.cpu"
    f.write_text("usage_usec 123\nuser_usec 1\nthrottled_usec 7\n")
    assert read_cgroup_cpu(f)[0] == 123 and read_cgroup_cpu(f)[2] == 7
    os.utime(f, (time.time() - 120, time.time() - 120))
    assert read_cgroup_cpu(f) is None and read_cgroup_cpu(tmp_path / "нет") is None


# ------------------------------------------------------------------------------------------------ отчёты клиентов
def test_client_report_is_untrusted_only_known_fields_and_sane_values():
    c = clean_client({"kbps": 5000, "fps": 30, "lat": -1, "buf": "x", "stalls": True, "seg_ms": 1e12, "res": "1920x1080", "eng": "hlsjs", "node": "<script>", "url": "https://example.org/?t=1", "password": "x"})
    assert c == {"kbps": 5000.0, "fps": 30.0, "res": "1920x1080", "eng": "hlsjs"}
    assert clean_client("x") is None and clean_client({"zzz": 1}) is None


def test_verdict_separates_channel_from_other_causes():
    assert viewer_verdict({"stalls": 0, "seg_ms": 300, "est": 50000}, {"stalls": 0}, 1.5, 5000) == (0, "")
    assert viewer_verdict({"stalls": 3, "seg_ms": 1400}, {"stalls": 0}, 1.5, 5000)[0] == 2
    assert "канал" in viewer_verdict({"stalls": 3, "seg_ms": 1400}, {"stalls": 0}, 1.5, 5000)[1]
    assert viewer_verdict({"stalls": 5, "seg_ms": 300}, {"stalls": 5}, 1.5, 5000)[0] == 0, "старые остановки не считаются новой проблемой"
    assert viewer_verdict({"reconn": 1}, {"reconn": 0}, 1.5, 5000)[0] == 2


def test_long_stall_counts_even_though_the_stall_counter_stands_still():
    assert viewer_verdict({"stalls": 1, "stall_ms": 30000}, {"stalls": 1, "stall_ms": 2000}, 1.5, 5000)[0] == 2
    assert viewer_verdict({"stalls": 1, "stall_ms": 2000}, {"stalls": 1, "stall_ms": 2000}, 1.5, 5000)[0] == 0


def test_verdict_lives_until_the_next_report_of_this_viewer():
    now = time.time()
    rep = {"v:1": {"t": now, "stalls": 3, "stall_ms": 9000}}
    memo: dict = {}
    first = aggregate_clients(rep, {"v:1": {"t": now - 30, "stalls": 0, "stall_ms": 0}}, 1.5, 5000, now, memo)
    assert first["bad"] == 1 and first["stalls"] == 3
    same = aggregate_clients(rep, dict(rep), 1.5, 5000, now + 6, memo)
    assert same["bad"] == 1 and same["stalls"] == 0, "отчёт тот же: зритель всё ещё с проблемой, новых остановок нет"
    fine = {"v:1": {"t": now + 30, "stalls": 3, "stall_ms": 9000}}
    assert aggregate_clients(fine, rep, 1.5, 5000, now + 30, memo)["bad"] == 0
    assert aggregate_clients({}, fine, 1.5, 5000, now + 40, memo)["bad"] == 0 and memo == {}, "ушедшие зрители не копятся"


def test_recent_stream_problem_is_not_blamed_on_delivery():
    s = ok_sample(viewers={"reporting": 5, "bad": 5, "worst": [{"why": "x"}]}, recent_issue=True)
    assert "доставка" not in codes(s)["viewers_common"].cause


def test_failed_rtmp_platform_does_not_explain_hls_viewers():
    s = ok_sample(rtmp=[{"id": "abcdef12", "name": "Канал", "status": "failed"}], viewers={"reporting": 5, "bad": 1, "worst": [{"why": "x"}]})
    assert "Сервер работает нормально" in codes(s)["viewer_single"].cause


def test_egress_cpu_limit_is_a_confirmed_cause():
    s = ok_sample(hls={"ready_s": 9.0}, svc={"egress": {"st": "ok", "load": 5.0, "cores": 0.4, "throttled": 0.6}, "livekit": {"st": "ok"}})
    f = codes(s)["hls_slow"]
    assert f.kind == "confirmed" and "предел" in f.cause


def test_aggregate_uses_only_fresh_reports_and_hides_identity():
    now = time.time()
    rows = {f"v:{i}": {"t": now, "stalls": 0, "seg_ms": 200 + i, "lat": 6.0 + i, "buf": 2.0, "est": 50000, "ua": "Chrome 130 · Linux"} for i in range(9)}
    rows["v:bad"] = {"t": now, "stalls": 4, "seg_ms": 1500, "lat": 20.0, "buf": 0.1, "ua": "Firefox 130 · Windows 10/11"}
    rows["v:old"] = {"t": now - 500, "stalls": 9}
    rows["p:x"] = {"t": now, "src": "screen", "res": "1920x1080", "limit": "bandwidth"}
    a = aggregate_clients(rows, {"v:bad": {"stalls": 1}}, 1.5, 5000, now)
    assert a["reporting"] == 10 and a["bad"] == 1 and a["stalls"] == 3 and a["worst"][0]["ua"].startswith("Firefox") and a["publishers"][0]["limit"] == "bandwidth"
    assert a["lat_p95"] >= a["lat"]


# ------------------------------------------------------------------------------------------------ правила
def test_healthy_broadcast_has_no_findings():
    assert rules(ok_sample(), CFG) == []


def test_cpu_cause_is_confirmed_only_when_egress_really_takes_the_load():
    s = ok_sample(host={"cpu": 96.0}, svc={"egress": {"st": "ok", "load": 90.0, "cores": 6.0}, "livekit": {"st": "ok"}})
    f = codes(s)["cpu_high"]
    assert f.level == 2 and f.kind == "confirmed" and "Egress" in f.cause
    s = ok_sample(host={"cpu": 90.0}, svc={"egress": {"st": "ok", "load": 30.0, "cores": 1.0}, "livekit": {"st": "ok"}})
    assert codes(s)["cpu_high"].kind == "assumed"
    s = ok_sample(host={"cpu": 90.0}, svc={"egress": {"st": "ok", "load": None, "cores": None}, "livekit": {"st": "ok"}})
    f = codes(s)["cpu_high"]
    assert f.kind == "unknown" and "дополнительная диагностика" in f.cause, "данных нет — так и сказано"


def test_slow_hls_names_the_stage_and_egress_load():
    s = ok_sample(hls={"ready_s": 9.0}, svc={"egress": {"st": "ok", "load": 92.0, "cores": 2.0}, "livekit": {"st": "ok"}})
    f = codes(s)["hls_slow"]
    assert "формирования HLS" in f.text and "92" in f.cause and f.kind == "assumed" and f.stage == "output"
    s = ok_sample(hls={"age_s": 20.0})
    assert codes(s)["hls_stall"].level == 2 and codes(s)["hls_stall"].kind == "unknown"
    s = ok_sample(hls={"age_s": 20.0, "ended": True})
    assert "hls_stall" not in codes(s), "конец эфира — не остановка"


def test_platforms_attached_directly_to_egress_are_flagged_when_the_relay_is_not_running():
    on = [{"enabled": True, "name": "Площадка"}]
    assert "relay_direct" in codes(ok_sample(rtmp=on, relay=False))
    assert "relay_direct" not in codes(ok_sample(rtmp=on, relay=True)), "через службу ретрансляции — замечания нет"
    assert "relay_direct" not in codes(ok_sample(rtmp=[], relay=False)), "площадок нет — замечать нечего"


def test_stalled_hls_names_the_broken_link_instead_of_asking_for_more_data():
    idle = {"egress": {"st": "ok", "load": 8.0, "cores": 0.2}, "livekit": {"st": "ok"}}
    f = codes(ok_sample(hls={"age_s": 34.0}, svc=idle, egress_seg_grow=False))["hls_stall"]
    assert "не выдаёт видео" in f.cause and "logs.sh livekit-egress" in f.cause, "Egress стоит без нагрузки: кодировщик не получает кадры"
    f = codes(ok_sample(hls={"age_s": 34.0}, svc=idle, egress_seg_grow=True))["hls_stall"]
    assert "не попадают" in f.cause and f.kind == "confirmed", "Egress режет сегменты, а файлов нет: путь записи"
    f = codes(ok_sample(hls={"age_s": 34.0}, svc=idle, egress_status="EGRESS_FAILED", egress_error="pipeline frozen"))["hls_stall"]
    assert "pipeline frozen" in f.cause
    f = codes(ok_sample(hls={"age_s": 34.0}, svc=idle, host={"disk": {"free": 1024**3}}))["hls_stall"]
    assert "свободно" in f.cause


def test_one_bad_viewer_is_a_client_problem_many_is_delivery():
    one = ok_sample(viewers={"reporting": 10, "bad": 1, "worst": [{"why": "канал зрителя на пределе"}]})
    f = codes(one)["viewer_single"]
    assert f.component == "viewer" and "Сервер работает нормально" in f.cause
    many = ok_sample(viewers={"reporting": 10, "bad": 7, "worst": [{"why": "x"}]})
    f = codes(many)["viewers_common"]
    assert f.level == 2 and f.stage == "delivery" and "доставка" in f.cause
    few = ok_sample(viewers={"reporting": 1, "bad": 1, "worst": [{"why": "x"}]})
    assert codes(few)["viewer_single"].kind == "unknown", "один отчёт — нечем разделить сервер и зрителя"


@pytest.mark.parametrize("over,code", [
    ({"host": {"mem": {"avail_pct": 4.0}}}, "mem_low"), ({"host": {"disk": {"free": 12 * 1024**3}}}, "disk_low"), ({"host": {"disk": {"free": 500 * 1024**3, "util": 99.0, "await_ms": 120.0}}}, "disk_busy"),
    ({"host": {"net": {"tx_bps": 950e6, "cap_bps": 1e9}}}, "net_saturated"), ({"host": {"net": {"err_s": 2.0}}}, "net_errors"), ({"svc": {"egress": {"st": "down"}, "livekit": {"st": "ok"}}}, "egress_down"),
    ({"svc": {"egress": {"st": "ok", "queue": 5}, "livekit": {"st": "ok"}}}, "egress_queue"), ({"svc": {"egress": {"st": "ok"}, "livekit": {"st": "ok", "loss_in": 12.0}}}, "lk_loss_in"),
    ({"media": {"fps": 12.0}}, "fps_low"), ({"source": {"known": True, "video": 0}}, "no_video"), ({"source": {"known": True, "video": 1, "max_w": 640, "max_h": 360}}, "source_low"),
    ({"viewers": {"reporting": 1, "publishers": [{"limit": "cpu"}], "worst": []}}, "pub_limit_cpu"),
])
def test_rules_cover_every_listed_situation(over, code):
    assert code in codes(ok_sample(**over))


def test_rtmp_failure_names_the_platform_and_never_the_key():
    s = ok_sample(rtmp=[{"id": "abcdef12-0000", "name": "Канал", "status": "failed", "error": clean_text(f"connect rtmp://live.example.org/app/{KEY} refused")}])
    f = codes(s)["rtmp_abcdef12"]
    assert "Канал" in f.text and KEY not in f.cause and f.kind == "confirmed"


def test_source_tile_shows_what_the_speaker_publishes_and_the_ordered_output_quality():
    st = {x["id"]: x for x in stage_view(ok_sample(source={"known": True, "max_w": 1920, "max_h": 1080}, quality="720p"), [])}
    assert st["source"]["val"] == "1920×1080 → 720p"


def test_stage_view_marks_missing_data_as_unknown_not_as_ok():
    st = {x["id"]: x for x in stage_view({"svc": {}, "viewer_count": 0}, [])}
    assert st["egress"]["lv"] == -1 and st["delivery"]["lv"] == -1
    st = {x["id"]: x for x in stage_view(ok_sample(), [ld.Finding("x", 2, "hls", "output", "t")])}
    assert st["output"]["lv"] == 2 and st["egress"]["lv"] == 0


# ------------------------------------------------------------------------------------------------ очистка
def test_clean_removes_rtmp_keys_tokens_and_signed_paths():
    raw = {"url": f"rtmps://live.example.org/app/{KEY}", "password": "s3cret", "token": "abc", "msg": f"ошибка rtmp://a.example.org/x/{KEY} /hls/0123456789abcdef01234567/1793872936/AbCdEfGhIjKlMnOpQrStUv/index.m3u8", "n": 5,
           "nested": [{"api_key": "x", "note": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"}]}
    out = json.dumps(clean(raw), ensure_ascii=False)
    for bad in (KEY, "s3cret", "AbCdEfGhIjKlMnOpQrStUv", "eyJhbGci"):
        assert bad not in out
    assert '"n": 5' in out


# ------------------------------------------------------------------------------------------------ сервис
def _bid(client, mid):
    return broadcasts(client, mid)[0].id


def _seed(client, bid):
    async def go():
        svc = client.app_obj.state.live_diag
        await svc.event(bid, "egress", "warn", "egress_load", "Нагрузка узла Egress 92 %", cause=f"адрес rtmp://x.example.org/app/{KEY}", kind="assumed", details={"snapshot": {"cpu": 91}, "url": f"rtmp://x.example.org/app/{KEY}"})
        await svc.event(bid, "broadcast", "info", "started", "Эфир начат")
        async with client.app_obj.state.session_maker() as db:
            db.add(LiveDiagSample(broadcast_id=bid, at=datetime.now(timezone.utc), data=ok_sample()))
            await db.commit()
    client.portal.call(go)


def test_server_data_is_only_for_those_who_control_the_broadcast(lv):  # noqa: F811
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    bid = _bid(lv, mid)
    _seed(lv, bid)
    login(lv, "carol")
    r = lv.get(f"/api/v1/meetings/{mid}/live/diag")
    assert r.status_code == 200 and r.json()["bid"] == str(bid) and r.json()["status"] in ("starting", "live")
    assert lv.get(f"/api/v1/live-diag/{bid}/events").status_code == 200
    login(lv, "alice")
    assert lv.get(f"/api/v1/meetings/{mid}/live/diag").status_code == 403, "участник без прав управления сервера не видит"
    assert lv.get(f"/api/v1/live-diag/{bid}/events").status_code == 404 and lv.get(f"/api/v1/live-diag/{bid}/export").status_code == 404
    assert lv.get(f"/api/v1/rooms/{room['id']}/live/diag/history").status_code == 404
    assert lv.get("/api/v1/admin/live/diag/history").status_code == 403
    lv.cookies.clear()
    lv.headers.pop("X-CSRF-Token", None)
    assert lv.get(f"/api/v1/live-diag/{bid}/events").status_code == 401
    token = get_view(lv, mid)
    r = lv.get(f"/api/v1/live/{token}/diag")
    assert r.status_code == 200 and r.json() == {"allowed": False}, "зрителю без входа — только его собственная диагностика"
    login(lv, "carol")
    r = lv.get(f"/api/v1/live/{token}/diag")
    assert r.json() == {"allowed": True, "meeting_id": mid}


def get_view(client, mid):
    return broadcasts(client, mid)[0].view_token


def test_exports_have_ids_events_and_no_secrets(lv):  # noqa: F811
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    bid = _bid(lv, mid)
    _seed(lv, bid)
    login(lv, "carol")
    j = lv.get(f"/api/v1/live-diag/{bid}/export?format=json")
    assert j.status_code == 200 and "attachment" in j.headers["content-disposition"]
    d = j.json()
    assert d["broadcast"]["broadcast_id"] == str(bid) and len([e for e in d["events"] if not e["code"].startswith("stage_")]) == 2 and len(d["samples"]) == 1 and d["samples"][0]["host"]["cpu"] == 30.0
    txt = lv.get(f"/api/v1/live-diag/{bid}/export?format=txt")
    rep = lv.get(f"/api/v1/live-diag/{bid}/export?format=report")
    assert "Нагрузка узла Egress 92 %" in txt.text and "предположение" in txt.text and "ДИАГНОСТИЧЕСКИЙ ОТЧЁТ" in rep.text and "ПРОБЛЕМЫ (1)" in rep.text
    for body in (j.text, txt.text, rep.text):
        assert KEY not in body
    assert lv.get(f"/api/v1/live-diag/{bid}/export?format=exe").status_code == 422
    r = lv.get(f"/api/v1/live-diag/{bid}/events?level=warn,error").json()["items"]
    assert [e["code"] for e in r] == ["egress_load"] and r[0]["details"]["snapshot"]["cpu"] == 91
    assert lv.get(f"/api/v1/live-diag/{bid}/series").json()["points"][0][1] == 30.0


def test_ping_stores_viewer_report_and_participant_report_goes_to_the_same_place(lv):  # noqa: F811
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    bid = _bid(lv, mid)
    token = get_view(lv, mid)
    lv.cookies.clear()
    lv.headers.pop("X-CSRF-Token", None)
    assert lv.post(f"/api/v1/live/{token}/ping", json={"vid": "abc123", "stats": {"buf": 2.5, "stalls": 1, "evil": "x"}}).status_code == 200
    assert lv.post(f"/api/v1/live/{token}/ping", json={"vid": "abc124"}).status_code == 200, "старые страницы без показателей работают как раньше"
    assert lv.post(f"/api/v1/live/{token}/ping", json={"vid": "abc125", "stats": "мусор"}).status_code == 200

    async def read():
        return await lv.app_obj.state.redis.hgetall(f"ld:{bid}:cl")
    rows = lv.portal.call(read)
    keys = {(k.decode() if isinstance(k, bytes) else k): v for k, v in rows.items()}
    assert set(keys) == {"v:abc123"} and "evil" not in str(keys) and json.loads(keys["v:abc123"])["buf"] == 2.5
    login(lv, "alice")
    assert lv.post(f"/api/v1/meetings/{mid}/live/diag/client", json={"stats": {"src": "screen", "res": "1280x720", "limit": "cpu"}}).status_code == 204
    assert len(lv.portal.call(read)) == 2


def test_summary_and_retention_keep_the_result_but_remove_details(lv):  # noqa: F811
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    start(lv, mid)
    bid = _bid(lv, mid)
    _seed(lv, bid)
    from app.models import Broadcast

    async def go():
        st = lv.app_obj.state
        async with st.session_maker() as db:
            b = await db.get(Broadcast, bid)
            b.status, b.ended_at = "ended", datetime.now(timezone.utc)
            await db.commit()
        await st.live_diag.finish(bid)
        async with st.session_maker() as db:
            b = await db.get(Broadcast, bid)
            old = datetime.now(timezone.utc) - timedelta(days=90)
            for row in (await db.execute(ld.select(LiveDiagSample))).scalars():
                row.at = old
            for row in (await db.execute(ld.select(LiveDiagEvent))).scalars():
                row.at = old
            await db.commit()
            s = dict(b.diag_summary)
        await st.live_diag.retention({"diag_keep_days": 30})
        async with st.session_maker() as db:
            n = (await db.execute(ld.select(ld.func.count()).select_from(LiveDiagSample))).scalar_one()
            e = (await db.execute(ld.select(ld.func.count()).select_from(LiveDiagEvent))).scalar_one()
            b = await db.get(Broadcast, bid)
            return s, n, e, b.diag_summary
    s, n, e, kept = lv.portal.call(go)
    assert s["cpu"]["avg"] == 30.0 and s["events"]["warn"] == 1 and s["mode"]["hls"] is True
    assert n == 0 and e == 0 and kept == s, "подробности удалены по сроку, итог для сравнения остался"
    login(lv, "carol")
    h = lv.get(f"/api/v1/rooms/{room['id']}/live/diag/history").json()["items"]
    assert h[0]["id"] == str(bid) and h[0]["summary"]["cpu"]["avg"] == 30.0


def test_summarize_without_samples_has_no_invented_numbers():
    class B:
        quality, status, error, peak_viewers, hls_dir, ended_at = "1080p", "ended", None, 0, "x", None
        started_at = datetime.now(timezone.utc)
    s = summarize([], [], B())
    assert s["cpu"] is None and s["samples"] == 0 and s["stalls"] == 0


def test_room_without_broadcasts_answers_idle(lv):  # noqa: F811
    room = room_live(lv)
    mid = _join(lv, "alice", room["id"])["meeting_id"]
    login(lv, "carol")
    r = lv.get(f"/api/v1/meetings/{mid}/live/diag").json()
    assert r["bid"] is None and r["status"] == "idle" and r["now"] is None


_ = (make_room, LEADERS, access, viewer, vw, uuid)
