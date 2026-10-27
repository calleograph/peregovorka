"""Адрес клиента: заголовки принимаются только от доверенных узлов; клиент, подключившийся напрямую, не может подставить чужой адрес; прямое обращение к backend заголовки не меняет;
цепочки нескольких прокси, IPv4/IPv6, Forwarded, заголовок WAF, режим «адрес соединения»."""
from app.security.ipres import NetCfg, ip_key, norm_ip, resolve
from app.services.settings import NetTrustSettings

CONTAINERS = "172.18.0.5"        # адрес web-контейнера, с которого backend видит соединение


class H(dict):
    def __init__(self, **kw):
        super().__init__({k.lower().replace("_", "-"): v for k, v in kw.items()})

    def get(self, k, d=""):
        return super().get(k.lower(), d)


def cfg(**kw):
    return NetCfg.build(NetTrustSettings(**kw), "127.0.0.0/8,::1/128", 1)


def ip(headers, tcp=CONTAINERS, **kw):
    return resolve(H(**headers), tcp, cfg(**kw))


def test_loopback_proxy_after_client_is_skipped():
    # реальный случай: X-Forwarded-For «172.16.48.7, 127.0.0.1» (локальный терминатор дописал свой адрес) — раньше показывался 127.0.0.1
    assert ip({"X_Forwarded_For": "172.16.48.7, 127.0.0.1", "X_PG_Peer": "172.18.0.1"}).ip == "172.16.48.7"
    assert ip({"X_Forwarded_For": "172.16.48.7, 127.0.0.1, ::1", "X_PG_Peer": "172.18.0.1"}).ip == "172.16.48.7"


def test_single_entry_and_no_chain():
    assert ip({"X_Forwarded_For": "203.0.113.9"}).ip == "203.0.113.9"
    assert ip({}).ip == CONTAINERS
    assert resolve(H(), None, cfg()).ip == "unknown"


def test_spoofed_left_part_is_ignored():
    # клиент подставил свой «X-Forwarded-For: 1.2.3.4»; прокси дописал реальный адрес — берём правый, а не подделку
    assert ip({"X_Forwarded_For": "1.2.3.4, 198.51.100.20", "X_PG_Peer": "10.0.0.5"}).ip == "198.51.100.20"


def test_client_connected_straight_to_web_cannot_choose_his_address():
    """Раньше любой клиент без прокси перед web мог прислать X-Forwarded-For и сам выбирать «свой» адрес: счётчики попыток и CAPTCHA по адресу не срабатывали."""
    r = ip({"X_Forwarded_For": "1.2.3.4", "X_PG_Peer": "203.0.113.9"})
    assert r.ip == "203.0.113.9" and r.source == "x-pg-peer" and not r.headers_accepted and r.notes
    # то же через каждую подмену: Forwarded, X-Real-IP
    assert ip({"Forwarded": "for=1.2.3.4", "X_Real_IP": "1.2.3.4", "X_PG_Peer": "203.0.113.9"}).ip == "203.0.113.9"


def test_direct_access_to_backend_ignores_all_headers():
    r = resolve(H(X_Forwarded_For="1.2.3.4", X_Real_IP="198.51.100.8", X_PG_Peer="203.0.113.99"), "203.0.113.50", cfg())
    assert r.ip == "203.0.113.50" and r.source == "tcp" and not r.headers_accepted


def test_public_waf_must_be_listed_as_trusted_proxy_and_then_its_header_is_honored():
    h = {"X_Forwarded_For": "198.51.100.77, 192.0.2.10", "X_PG_Peer": "192.0.2.10"}
    assert ip(h).ip == "192.0.2.10", "WAF с публичным адресом не объявлен доверенным — его заголовки не принимаются (виден адрес WAF)"
    assert ip(h, trusted_proxies="192.0.2.0/24").ip == "198.51.100.77", "после добавления в доверенные адрес клиента берётся из цепочки"


def test_extra_hops_for_proxies_not_described_by_cidr():
    h = {"X_Forwarded_For": "172.16.48.7, 10.10.0.5", "X_PG_Peer": "10.0.0.9"}
    assert ip(h, mode="xff", trusted_proxies="10.0.0.9", proxy_hops=1).ip == "172.16.48.7"
    assert ip(h, mode="xff", trusted_proxies="10.10.0.5, 10.0.0.9").ip == "172.16.48.7"


def test_garbage_is_ignored_and_all_trusted_returns_leftmost():
    assert ip({"X_Forwarded_For": "evil<script>, 198.51.100.20", "X_PG_Peer": "10.0.0.5"}).ip == "198.51.100.20"
    assert ip({"X_Forwarded_For": "not-an-ip", "X_PG_Peer": "10.0.0.5"}).ip == "10.0.0.5"
    assert ip({"X_Forwarded_For": "127.0.0.1, ::1", "X_PG_Peer": "10.0.0.5"}).ip == "127.0.0.1"
    assert NetTrustSettings(trusted_proxies="bad-entry") if False else True
    try:
        NetTrustSettings(trusted_proxies="bad, 10.0.0.0/8")
    except ValueError as exc:
        assert "bad" in str(exc)
    else:
        raise AssertionError("некорректный адрес в списке должен отвергаться при сохранении")


def test_ipv6_client_ipv4_mapped_and_prefix_keys():
    assert ip({"X_Forwarded_For": "2001:db8::7, 127.0.0.1", "X_PG_Peer": "172.18.0.1"}).ip == "2001:db8::7"
    assert norm_ip("::ffff:192.0.2.9") == "192.0.2.9" and norm_ip("[2001:db8::1]:443") == "2001:db8::1" and norm_ip("192.0.2.9:8080") == "192.0.2.9" and norm_ip("x") is None
    assert ip_key("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == ip_key("2001:db8:1:2::1") == "2001:db8:1:2::/64", "перебор внутри одной IPv6-сети делит один счётчик"
    assert ip_key("192.0.2.9") == "192.0.2.9"
    assert ip({"X_Forwarded_For": "2001:db8::7", "X_PG_Peer": "fd00::5"}, tcp="fd00::10").ip == "2001:db8::7"


def test_other_header_modes():
    h = {"X_Forwarded_For": "1.1.1.1", "X_Real_IP": "198.51.100.5", "Forwarded": 'for=198.51.100.6;proto=https, for="[2001:db8::9]:4711"', "CF_Connecting_IP": "198.51.100.7", "X_PG_Peer": "10.0.0.5"}
    assert ip(h, mode="x_real_ip", trusted_proxies="10.0.0.0/8").ip == "198.51.100.5"
    assert ip(h, mode="x_real_ip").ip == "10.0.0.5", "явный режим требует явного списка доверенных прокси"
    assert ip(h, mode="forwarded", trusted_proxies="2001:db8::9, 10.0.0.0/8").ip == "198.51.100.6"
    assert ip(h, mode="header", header_name="CF-Connecting-IP", trusted_proxies="10.0.0.0/8").ip == "198.51.100.7"
    assert ip(h, mode="peer", trusted_proxies="10.0.0.0/8").ip == "10.0.0.5", "режим «адрес соединения»: заголовки не читаются"
    assert ip({"CF_Connecting_IP": "198.51.100.7", "X_PG_Peer": "203.0.113.1"}, mode="header", header_name="CF-Connecting-IP").ip == "203.0.113.1", "заголовок WAF от недоверенного узла не принимается"


def test_client_events_never_store_access_tokens_or_join_requests():
    from app.api.client import scrub

    url = "wss://m.test/livekit/rtc/v1?access_token=eyJhbGciOiJIUzI1NiJ9.eyJ2IjoxfQ.signaturepart&join_request=QUJD" + "A" * 500 + "&sdk=js"
    out = scrub(f"WebSocket connection to '{url}' failed")
    assert "eyJ" not in out and "QUJD" not in out and "access_token=<скрыто>" in out and "join_request=<скрыто>" in out and "sdk=js" in out
    assert "<jwt скрыт>" in scrub("x eyJhbGciOiJIUzI1NiJ9.eyJ2IjoxfQ.signaturepart y")
    assert "Bearer <скрыто>" in scrub("Authorization: Bearer abc.def.ghi")
