"""Медиасоединения (ICE): режим и производные настройки LiveKit, STUN, признаки CGNAT/двойного NAT, правка .env, раздел администратора.
Живое испытание в модели сети (NAT, CGNAT, офис + интернет, TCP при закрытом UDP) — tests/live/medianet/run.sh."""
from __future__ import annotations

import importlib.util
import json
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

from app.services import medianet as M
from app.services import netrules as N

from .conftest import login

ROOT = Path(__file__).resolve().parents[2]
BASE = {"LIVEKIT_TCP_PORT": "17881", "LIVEKIT_UDP_PORT": "17882"}


def plan(env, probe=None):
    return M.plan({**BASE, **env}, probe)


def stun(ip):
    return {"ip": ip, "results": [{"server": "stun.example.org:3478", "ip": ip, "port": 40000}], "consistent": True, "symmetric": False}


def test_classify_ranges():
    assert M.classify("10.1.2.3") == "private" and M.classify("192.168.0.5") == "private" and M.classify("172.16.0.1") == "private"
    assert M.classify("100.64.0.1") == "cgnat"
    assert M.classify("192.0.2.10") == "public"           # документационные — как публичные (примеры и испытания)
    assert M.classify("127.0.0.1") == "loopback" and M.classify("169.254.1.1") == "link-local"
    assert M.classify("2001:db8::1") == "invalid" and M.classify("meet.example.org") == "invalid" and M.classify("") == "invalid"


def test_public_on_interface_is_announced_as_is_without_stun():
    p = plan({"LIVEKIT_NODE_IP": "192.0.2.10", "LIVEKIT_PUBLIC_IP": "auto"})
    assert p["mode"] == "direct" and p["advertised"] == ["192.0.2.10"] and p["forward"] == []
    assert p["env"] == {"LIVEKIT_MEDIA_MODE": "direct", "LIVEKIT_RTC_NODE_IP": "192.0.2.10", "LIVEKIT_RTC_USE_EXTERNAL_IP": "false",
                        "LIVEKIT_RTC_ADVERTISE_INTERNAL": "false", "LIVEKIT_RTC_LAN_IP": ""}


def test_nat_with_stun_announces_public_and_lan_and_lists_forwarding_with_same_ports():
    p = plan({"LIVEKIT_NODE_IP": "10.0.0.5"}, stun("192.0.2.2"))
    assert p["mode"] == "nat-stun" and p["advertised"] == ["10.0.0.5", "192.0.2.2"]
    assert p["env"]["LIVEKIT_RTC_USE_EXTERNAL_IP"] == "true" and p["env"]["LIVEKIT_RTC_ADVERTISE_INTERNAL"] == "true"
    assert p["env"]["LIVEKIT_RTC_LAN_IP"] == "10.0.0.5" and p["env"]["LIVEKIT_RTC_NODE_IP"] == "192.0.2.2"   # запасной адрес, если STUN не ответит
    fw = {(f["proto"], f["external"], f["target"]) for f in p["forward"]}
    assert ("UDP", "192.0.2.2:17882", "10.0.0.5:17882") in fw and ("TCP", "192.0.2.2:17881", "10.0.0.5:17881") in fw


def test_admin_address_wins_and_stun_is_not_needed():
    p = plan({"LIVEKIT_NODE_IP": "10.0.0.5", "LIVEKIT_PUBLIC_IP": "192.0.2.77"}, stun("192.0.2.2"))
    assert p["mode"] == "nat-manual" and p["public_ip"] == "192.0.2.77" and p["env"]["LIVEKIT_RTC_USE_EXTERNAL_IP"] == "false"
    assert p["env"]["LIVEKIT_RTC_ADVERTISE_INTERNAL"] == "true"


def test_off_is_lan_only_and_says_so():
    p = plan({"LIVEKIT_NODE_IP": "10.0.0.5", "LIVEKIT_PUBLIC_IP": "off"}, stun("192.0.2.2"))
    assert p["mode"] == "lan" and p["advertised"] == ["10.0.0.5"] and any("так задумано" in a for a in p["actions"])


def test_stun_unavailable_keeps_last_known_or_falls_back_to_lan_with_action():
    down = {"ip": None, "results": [{"server": "stun.example.org:3478", "ip": None, "port": None}], "consistent": True, "symmetric": False}
    kept = plan({"LIVEKIT_NODE_IP": "10.0.0.5", "LIVEKIT_RTC_NODE_IP": "192.0.2.2"}, down)
    assert kept["mode"] == "nat-stun" and kept["public_ip"] == "192.0.2.2" and any("не отвечает" in w for w in kept["warnings"])
    none = plan({"LIVEKIT_NODE_IP": "10.0.0.5"}, down)
    assert none["mode"] == "lan" and any("LIVEKIT_PUBLIC_IP" in a for a in none["actions"])
    quiet = plan({"LIVEKIT_NODE_IP": "10.0.0.5", "LIVEKIT_RTC_NODE_IP": "192.0.2.2"}, None)      # STUN не опрашивался (веб без проверки)
    assert quiet["public_source"] == "configured" and not quiet["warnings"]


def test_cgnat_is_explained():
    p = plan({"LIVEKIT_NODE_IP": "100.64.0.1"}, stun("192.0.2.2"))
    assert any("CGNAT" in w for w in p["warnings"])
    q = plan({"LIVEKIT_NODE_IP": "10.0.0.5"}, stun("100.64.0.1"))
    assert q["mode"] == "lan" and any("CGNAT" in w for w in q["warnings"])
    assert M.nat_layers(["10.0.0.1", "100.64.0.1", "192.0.2.3"])["cgnat"]
    assert M.nat_layers(["10.0.0.1", "192.168.0.5", "192.0.2.3"])["private_hops"] == 2
    assert M.nat_layers(["172.18.0.1", "10.0.0.1", "192.0.2.3"], skip=1)["private_hops"] == 1      # шлюз Docker не считается
    d = M.diagnose({**BASE, "LIVEKIT_NODE_IP": "10.0.0.5"}, probe=stun("192.0.2.2"), hops=["10.0.0.1", "100.64.0.1", "192.0.2.3"])
    assert {c["id"]: c["status"] for c in d["checks"]}["nat"] == "fail"


def test_invalid_admin_value_is_not_applied():
    p = plan({"LIVEKIT_NODE_IP": "10.0.0.5", "LIVEKIT_PUBLIC_IP": "meet.example.org"}, stun("192.0.2.2"))
    assert p["mode"] == "nat-stun" and any("не применено" in w for w in p["warnings"])


def test_stale_settings_are_reported_and_fresh_are_not():
    env = {**BASE, "LIVEKIT_NODE_IP": "10.0.0.5"}
    p = M.plan(env, stun("192.0.2.2"))
    assert M.stale(env, p)                                           # ничего не записано — устарело
    env2 = {**env, **p["env"]}
    assert M.stale(env2, M.plan(env2, stun("192.0.2.9"))) == []     # nat-stun: сменившийся публичный адрес LiveKit возьмёт сам


def test_clients_check_flags_addresses_not_announced_and_tcp_only():
    env = {**BASE, "LIVEKIT_NODE_IP": "10.0.0.5", "LIVEKIT_PUBLIC_IP": "192.0.2.2"}
    env.update(M.plan(env)["env"])
    ok = M.diagnose(env, probe=None, clients=[{"remote": "udp 192.0.2.2:17882"}, {"remote": "udp 10.0.0.5:17882"}])
    assert {c["id"]: c["status"] for c in ok["checks"]}["clients"] == "ok"
    bad = M.diagnose(env, probe=None, clients=[{"remote": "udp 172.18.0.4:17882"}])
    assert {c["id"]: c["status"] for c in bad["checks"]}["clients"] == "warn"
    tcp = M.diagnose(env, probe=None, clients=[{"remote": "tcp 192.0.2.2:17881"}])
    assert "UDP" in {c["id"]: c for c in tcp["checks"]}["clients"]["detail"]


def test_stun_query_against_a_local_stun_responder():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))

    def answer():
        data, (ip, port) = srv.recvfrom(2048)
        xip = struct.unpack("!I", socket.inet_aton("192.0.2.44"))[0] ^ 0x2112A442
        attr = struct.pack("!HHBBHI", 0x0020, 8, 0, 1, 50000 ^ 0x2112, xip)
        srv.sendto(struct.pack("!HHI", 0x0101, len(attr), 0x2112A442) + data[8:20] + attr, (ip, port))
    t = threading.Thread(target=answer, daemon=True)
    t.start()
    try:
        assert M.stun_query("127.0.0.1", srv.getsockname()[1], timeout=2) == ("192.0.2.44", 50000)
    finally:
        srv.close()
    assert M.stun_query("127.0.0.1", 9, timeout=0.3) is None


def test_set_env_keys_replaces_in_place_and_appends_new():
    text = "# c\nLIVEKIT_NODE_IP=10.0.0.5\nLIVEKIT_MEDIA_MODE=\nOTHER=x\n"
    out = M.set_env_keys(text, {"LIVEKIT_MEDIA_MODE": "nat-stun", "LIVEKIT_RTC_LAN_IP": "10.0.0.5"})
    assert "LIVEKIT_MEDIA_MODE=nat-stun\nOTHER=x" in out and out.startswith("# c\nLIVEKIT_NODE_IP=10.0.0.5") and out.rstrip().endswith("LIVEKIT_RTC_LAN_IP=10.0.0.5")
    assert M.set_env_keys(out, {"LIVEKIT_MEDIA_MODE": "nat-stun"}) == out


def test_host_cli_configure_offline_writes_derived_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LIVEKIT_NODE_IP=192.0.2.10\nLIVEKIT_TCP_PORT=17881\nLIVEKIT_UDP_PORT=17882\n")
    env.chmod(0o600)
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "medianet.py"), "configure", "--env", str(env), "--write", "--offline"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    e = M.parse_env(env.read_text())
    assert e["LIVEKIT_MEDIA_MODE"] == "direct" and e["LIVEKIT_PUBLIC_IP"] == "auto" and (env.stat().st_mode & 0o777) == 0o600
    bad = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "medianet.py"), "configure", "--env", str(env), "--public-ip", "nonsense"],
                         capture_output=True, text=True, timeout=60)
    assert bad.returncode == 2


def test_compose_renders_livekit_config_from_derived_keys(tmp_path):
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        import pytest
        pytest.skip("нет docker compose")
    env = tmp_path / ".env"
    text = (ROOT / ".env.example").read_text()
    p = M.plan({**BASE, "LIVEKIT_NODE_IP": "10.0.0.5"}, stun("192.0.2.2"))
    env.write_text(M.set_env_keys(text.replace("LIVEKIT_NODE_IP=192.0.2.10", "LIVEKIT_NODE_IP=10.0.0.5"), p["env"]))
    r = subprocess.run(["docker", "compose", "--env-file", str(env), "-f", str(ROOT / "deployment" / "compose.yml"), "config", "--format", "json"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    lk = json.loads(r.stdout)["services"]["livekit"]
    cfg = lk["environment"]["LIVEKIT_CONFIG"]
    assert "use_external_ip: true" in cfg and "advertise_internal_ip: true" in cfg and "node_ip: 192.0.2.2" in cfg
    assert "tcp_port: 17881" in cfg and "udp_port: 17882" in cfg and "stun_servers: [" in cfg
    assert lk["environment"]["LIVEKIT_LAN_IP"] == "10.0.0.5" and "NET_ADMIN" in lk["cap_add"]
    pub = {(x["target"], x["published"], x["protocol"]) for x in lk["ports"]}
    assert (17881, "17881", "tcp") in pub and (17882, "17882", "udp") in pub      # номер внутри = снаружи


def test_netrules_describe_nat_mode_without_the_old_advice():
    r = N.build(N.from_env({"LIVEKIT_NODE_IP": "10.0.0.5", "LIVEKIT_MEDIA_MODE": "nat-stun", "LIVEKIT_RTC_NODE_IP": "192.0.2.2", **BASE}))
    assert not any("частный" in w for w in r["warnings"]) and any("за NAT" in v for v in r["variant"])
    assert any(x["id"] == "stun" for x in r["rules"])
    old = N.build(N.from_env({"LIVEKIT_NODE_IP": "10.0.0.5", **BASE}))
    assert any("LIVEKIT_PUBLIC_IP" in w for w in old["warnings"])


def test_media_endpoints_admin_only_and_no_secrets(client, monkeypatch):
    monkeypatch.setenv("LIVEKIT_NODE_IP", "10.0.0.5")
    monkeypatch.setenv("LIVEKIT_PUBLIC_IP", "192.0.2.2")
    monkeypatch.setenv("LIVEKIT_RTC_TCP_PORT", "17881")
    monkeypatch.setenv("LIVEKIT_RTC_UDP_PORT", "17882")
    login(client, "alice")
    assert client.get("/api/v1/admin/network/media").status_code == 403
    assert client.post("/api/v1/admin/network/media/probe").status_code == 403
    login(client, "root")
    r = client.get("/api/v1/admin/network/media")
    assert r.status_code == 200
    j = r.json()
    assert j["plan"]["mode"] == "nat-manual" and j["plan"]["advertised"] == ["10.0.0.5", "192.0.2.2"] and j["live"] is False
    assert "SECRET" not in r.text and "API_KEY" not in r.text
