"""Схемы развёртывания и разбор причин недоступности API (раздел «Автоматические проверки»): без привязки к конкретным именам и адресам."""
import asyncio
import socket
import ssl

import httpx
import pytest

from app.config import Settings
from app.services import outlook_diag as od


def topo(headers=None, local=False, manual="auto"):
    return od.detect_topology(headers or {}, local, manual)


def test_variant_standalone_server_with_local_https():
    t = topo({}, local=True)                                   # сертификат задан установщиком, внешних узлов в заголовках нет
    assert t["mode"] == "local" and not t["needs_choice"] and "NGINX_TLS_CERT" in " ".join(t["evidence"])


def test_variant_external_reverse_proxy_over_http_to_app():
    t = topo({"x-forwarded-proto": "https", "x-forwarded-for": "198.51.100.7"})
    assert t["mode"] == "external" and not t["needs_choice"] and t["auto"]


def test_variant_waf_or_balancer_chain_is_external_and_can_be_refined_manually():
    h = {"x-forwarded-proto": "https", "x-forwarded-for": "198.51.100.7, 192.0.2.10", "via": "1.1 waf"}
    assert topo(h)["mode"] == "external" and topo(h, manual="gateway")["mode"] == "gateway" and topo(h, manual="proxy")["mode"] == "proxy"


def test_variant_local_certificate_plus_external_node_is_not_declared_local():
    t = topo({"x-forwarded-proto": "https", "x-forwarded-for": "198.51.100.7, 192.0.2.10"}, local=True)
    assert t["mode"] == "external" and t["hint"]               # есть и свой сертификат, и внешний узел: схему должен подтвердить администратор


def test_unknown_when_no_reliable_signs_and_never_guessed_from_tls_outcome():
    t = topo({})
    assert t["mode"] == "unknown" and t["needs_choice"] and "Требуется уточнение" in t["label"]


def test_manual_choice_wins_and_legacy_value_is_mapped():
    assert topo({}, manual="local")["mode"] == "local" and topo({}, manual="direct")["mode"] == "local" and not topo({}, manual="proxy")["auto"]


def test_empty_compose_value_means_not_configured():
    assert Settings._tls_local("") is False and Settings._tls_local("true") == "true"


@pytest.mark.parametrize("status,ok,code", [(200, True, "ok"), (401, True, "auth"), (403, None, "forbidden"), (404, False, "not_found"), (502, False, "gateway"), (503, False, "gateway")])
def test_api_status_table(status, ok, code):
    assert od.http_verdict(status)[:2] == (ok, code)


def _fail(exc):
    def handler(request):
        raise exc
    return httpx.MockTransport(handler)


def _wrapped(inner):
    e = httpx.ConnectError("x")
    e.__cause__ = inner
    return e


def test_container_cannot_reach_public_name_is_reported_as_routing_not_as_connect_error():
    r = asyncio.run(od.probe_json("https://meet.example.org/api/v1/outlook/config", transport=_fail(_wrapped(socket.gaierror(-2, "Name or service not known")))))
    assert r["code"] == "dns" and r["ok"] is None and "split DNS" in r["message"] and "ConnectError" not in r["message"]


def test_untrusted_tls_is_reported_as_trust_error():
    err = ssl.SSLCertVerificationError(20, "unable to get local issuer certificate")
    r = asyncio.run(od.probe_json("https://meet.example.org/x", transport=_fail(_wrapped(err))))
    assert r["code"] == "tls_untrusted" and "доверия TLS" in r["message"] and "unable to get local issuer certificate" in r["message"]


def test_refused_and_timeout_and_redirect():
    assert asyncio.run(od.probe_json("https://x.example.org/", transport=_fail(_wrapped(ConnectionRefusedError()))))["code"] == "refused"
    assert asyncio.run(od.probe_json("https://x.example.org/", transport=_fail(httpx.ConnectTimeout("t"))))["code"] == "timeout"
    redirect = httpx.MockTransport(lambda r: httpx.Response(301, headers={"location": "https://other.example.org/"}))
    r = asyncio.run(od.probe_json("https://x.example.org/m.xml", transport=redirect))
    assert r["code"] == "redirect" and r["ok"] is False and "Exchange 2019" in r["message"]
