"""Сетевая доступность: перечень портов строится по фактической конфигурации, а не по зашитой таблице; доступ только администратору; проверка честно называет свой предел."""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

from app.services import netrules as N

from .conftest import login

ENV = {
    "APP_PUBLIC_URL": "https://meet.example.org", "LIVEKIT_PUBLIC_URL": "wss://meet.example.org/livekit", "LIVEKIT_NODE_IP": "203.0.113.5",
    "LIVEKIT_TCP_PORT": "17881", "LIVEKIT_UDP_PORT": "17882", "INSTALL_PROFILE": "standalone",
}


def build(env=None, **kw):
    return N.build(N.from_env({**ENV, **(env or {})}, **kw))


def ports(r, direction="in"):
    return {(x["proto"], x["port"]) for x in r["rules"] if x["direction"] == direction}


def test_ports_follow_the_configuration_not_a_fixed_table():
    a = build()
    b = build({"LIVEKIT_TCP_PORT": "7881", "LIVEKIT_UDP_PORT": "50000", "APP_PUBLIC_URL": "https://meet.example.org:8443"})
    assert {("UDP", "17882"), ("TCP", "17881"), ("TCP", "443")} <= ports(a)
    assert {("UDP", "50000"), ("TCP", "7881"), ("TCP", "8443")} <= ports(b)
    assert ("UDP", "17882") not in ports(b)


def test_public_node_ip_gives_no_nat_warning_and_private_does():
    assert build()["warnings"] == []
    w = build({"LIVEKIT_NODE_IP": "10.1.2.3"})["warnings"]
    assert any("частный" in x for x in w)
    assert any("LIVEKIT_NODE_IP не задан" in x for x in build({"LIVEKIT_NODE_IP": ""})["warnings"])


def test_sip_ports_appear_only_when_telephony_enabled():
    off = build()
    on = build({"SIP_ENABLED": "yes", "SIP_SIGNALING_PORT": "5070", "SIP_RTP_START": "30000", "SIP_RTP_END": "30050", "SIP_ALLOWED_CIDRS": "198.51.100.0/24"})
    assert not any(x["id"].startswith("sip-") for x in off["rules"])
    assert {("UDP", "5070"), ("TCP", "5070"), ("UDP", "30000-30050")} <= ports(on)
    assert all("198.51.100.0/24" in x["where"] for x in on["rules"] if x["id"].startswith("sip-"))


def test_separate_signalling_address_gets_its_own_rule_and_same_host_does_not():
    assert not any(x["id"] == "livekit-signal" for x in build()["rules"])
    r = build({"LIVEKIT_PUBLIC_URL": "wss://rtc.example.org:7443/livekit"})
    assert ("TCP", "7443") in ports(r)


def test_tls_mode_changes_variant():
    own = build({"NGINX_TLS_CERT": "/etc/ssl/x.pem"})
    assert any("nginx, установленный вместе" in v for v in own["variant"])
    assert any("внешний reverse proxy" in v for v in build()["variant"])


def test_directory_uris_become_outbound_rules():
    r = build({"LDAP_URIS": "ldaps://dc1.example.org,ldaps://dc2.example.org:3269"})
    assert {("TCP", "636"), ("TCP", "3269")} <= ports(r, "out")


def test_http_and_loopback_urls_are_flagged():
    assert any("HTTPS" in x for x in build({"APP_PUBLIC_URL": "http://meet.example.org"})["warnings"])
    assert any("localhost" in x for x in build({"APP_PUBLIC_URL": "https://localhost"})["warnings"])


def test_internal_services_are_never_listed_as_public():
    r = build({"WEB_PORT": "18480", "LIVEKIT_HTTP_PORT": "17880", "NGINX_LISTEN_PORT": "18400"})
    pub = {p for _, p in ports(r)}
    assert not pub & {"18480", "17880", "18400", "6789", "5432", "6379"}
    assert {x["port"] for x in r["local_only"]} >= {"18480", "17880", "18400", "6789"}


def test_every_format_renders_and_exports_keep_the_limits_text():
    r = build()
    for name, fn in N.FORMATS.items():
        out = fn(r)
        assert "17882" in out, name
    assert "ОГРАНИЧЕНИЯ" in N.to_text(r) and "Ограничения" in N.to_markdown(r)
    assert N.to_csv(r).startswith("﻿")
    assert "Прошу открыть" in N.firewall_request(r)


def test_dotenv_parser_ignores_comments_and_strips_quotes():
    env = N.parse_dotenv('# c\nA=1\nB="two"\n C = x \nbad line\n=z\n')
    assert env == {"A": "1", "B": "two", "C": "x"}


def test_module_is_standard_library_only_so_the_host_cli_can_load_it():
    tree = ast.parse(Path(N.__file__).read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    assert names <= set(sys.stdlib_module_names) | {"__future__"}
    assert not any(isinstance(n, ast.ImportFrom) and n.level > 0 for n in ast.walk(tree))


def test_host_script_prints_the_same_list_from_dotenv_without_docker(tmp_path):
    root = Path(N.__file__).resolve().parents[3]
    env = tmp_path / ".env"
    env.write_text("\n".join(f"{k}={v}" for k, v in ENV.items()) + "\n", encoding="utf-8")
    res = subprocess.run(["bash", str(root / "scripts/ports.sh"), "--env", str(env), "--format", "csv"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "17882" in res.stdout and "203.0.113.5" in res.stdout
    spec = importlib.util.spec_from_file_location("nr", N.__file__)
    assert spec is not None
    missing = subprocess.run(["bash", str(root / "scripts/ports.sh"), "--env", str(tmp_path / "nope")], capture_output=True, text=True, timeout=60)
    assert missing.returncode == 2


# ----------------------------------------------------------------------------------------------------- API
def test_network_api_is_admin_only_and_reflects_settings(client):
    login(client, "alice")
    assert client.get("/api/v1/admin/network").status_code == 403
    assert client.get("/api/v1/admin/network/export?format=csv").status_code == 403
    assert client.post("/api/v1/admin/network/probe").status_code == 403
    login(client, "root")
    r = client.get("/api/v1/admin/network")
    assert r.status_code == 200
    body = r.json()
    assert body["public_host"] == "testserver" and any(x["proto"] == "TCP" and x["port"] == "80" for x in body["rules"])
    assert all("secret" not in str(x).lower() or "secret" in x["purpose"].lower() for x in body["rules"])


def test_network_export_formats_and_audit(client):
    login(client, "root")
    for fmt, mime in (("text", "text/plain"), ("md", "text/markdown"), ("csv", "text/csv"), ("json", "application/json"), ("request", "text/plain")):
        r = client.get(f"/api/v1/admin/network/export?format={fmt}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(mime) and "attachment" in r.headers["content-disposition"], fmt
    assert client.get("/api/v1/admin/network/export?format=exe").status_code == 422
    rows = client.get("/api/v1/admin/audit?limit=50").json()
    assert "network.export" in [a["action"] for a in rows]



def test_probe_reports_closed_port_and_never_claims_internet_reachability(client):
    login(client, "root")
    r = client.post("/api/v1/admin/network/probe")
    assert r.status_code == 200
    body = r.json()
    assert "не подтверждает доступ из интернета" in body["disclaimer"]
    assert all(c["ok"] is False and c["error"] for c in body["checks"] if c["id"] == "livekit-internal")    # LiveKit в тесте заведомо недоступен
