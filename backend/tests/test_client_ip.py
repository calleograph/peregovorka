"""Адрес клиента за цепочкой прокси: первый не доверенный адрес справа, loopback-терминатор не подменяет клиента."""
from app.auth.deps import parse_networks, pick_client_ip

LOCAL = parse_networks("127.0.0.0/8,::1/128")


def test_loopback_proxy_after_client_is_skipped():
    # реальный случай: X-Forwarded-For «172.16.48.7, 127.0.0.1» (локальный терминатор дописал свой адрес) — раньше показывался 127.0.0.1
    assert pick_client_ip("172.16.48.7, 127.0.0.1", "172.18.0.1", LOCAL) == "172.16.48.7"
    assert pick_client_ip("172.16.48.7, 127.0.0.1, ::1", "172.18.0.1", LOCAL) == "172.16.48.7"


def test_single_entry_and_no_chain():
    assert pick_client_ip("203.0.113.9", "172.18.0.1", LOCAL) == "203.0.113.9"
    assert pick_client_ip("", "172.18.0.1", LOCAL) == "172.18.0.1"
    assert pick_client_ip("", None, LOCAL) == "unknown"


def test_spoofed_left_part_is_ignored():
    # клиент подставил свой «X-Forwarded-For: 1.2.3.4»; наш прокси дописал реальный адрес — берём правый, а не подделку
    assert pick_client_ip("1.2.3.4, 198.51.100.20", "172.18.0.1", LOCAL) == "198.51.100.20"


def test_extra_hops_for_proxies_not_described_by_cidr():
    assert pick_client_ip("172.16.48.7, 10.10.0.5", "172.18.0.1", LOCAL, hops=2) == "172.16.48.7"
    assert pick_client_ip("172.16.48.7, 10.10.0.5, 127.0.0.1", "172.18.0.1", LOCAL, hops=2) == "172.16.48.7"
    assert pick_client_ip("172.16.48.7, 10.10.0.5", "172.18.0.1", parse_networks("127.0.0.0/8,10.10.0.5"), hops=1) == "172.16.48.7"


def test_garbage_is_ignored_and_all_trusted_returns_leftmost():
    assert pick_client_ip("evil<script>, 198.51.100.20", "172.18.0.1", LOCAL) == "198.51.100.20"
    assert pick_client_ip("not-an-ip", "172.18.0.1", LOCAL) == "172.18.0.1"
    assert pick_client_ip("127.0.0.1, ::1", "172.18.0.1", LOCAL) == "127.0.0.1"
    assert parse_networks("bad, 10.0.0.0/8 ,,") == parse_networks("10.0.0.0/8")


def test_ipv6_client():
    assert pick_client_ip("2001:db8::7, 127.0.0.1", "172.18.0.1", LOCAL) == "2001:db8::7"


def test_client_events_never_store_access_tokens_or_join_requests():
    from app.api.client import scrub

    url = "wss://m.test/livekit/rtc/v1?access_token=eyJhbGciOiJIUzI1NiJ9.eyJ2IjoxfQ.signaturepart&join_request=QUJD" + "A" * 500 + "&sdk=js"
    out = scrub(f"WebSocket connection to '{url}' failed")
    assert "eyJ" not in out and "QUJD" not in out and "access_token=<скрыто>" in out and "join_request=<скрыто>" in out and "sdk=js" in out
    assert "<jwt скрыт>" in scrub("x eyJhbGciOiJIUzI1NiJ9.eyJ2IjoxfQ.signaturepart y")
    assert "Bearer <скрыто>" in scrub("Authorization: Bearer abc.def.ghi")
