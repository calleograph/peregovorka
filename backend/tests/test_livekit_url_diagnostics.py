"""Публичный адрес LiveKit: диагностика называет этап сбоя (DNS, TCP, TLS, авторизация, прокси) и замечает неудачный адрес. TLS проверяется настоящим сервером."""
from __future__ import annotations

import asyncio
import datetime
import ipaddress
import socket
import ssl

import pytest

from app.config import Settings
from app.services import diagnostics


def _self_signed_context(tmp_path) -> ssl.SSLContext:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False).sign(key, hashes.SHA256()))
    cf, kf = tmp_path / "c.pem", tmp_path / "k.pem"
    cf.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kf.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cf, kf)
    return ctx


async def _handler(ws):
    await asyncio.sleep(0.05)


async def test_self_signed_certificate_is_reported_as_a_tls_problem_with_consequences(tmp_path):
    from websockets.asyncio.server import serve

    async with serve(_handler, "127.0.0.1", 0, ssl=_self_signed_context(tmp_path)) as server:
        port = server.sockets[0].getsockname()[1]
        res = await diagnostics.ws_handshake(f"wss://127.0.0.1:{port}/rtc?access_token=SECRETTOKEN", timeout=5)
    assert res["ok"] is False and res["stage"] == "tls"
    assert "самоподписанный" in res["hint"] and "не подключатся" in res["hint"] and "SECRETTOKEN" not in str(res)


async def test_plain_http_behind_wss_is_reported_with_the_missing_tls_hint():
    from websockets.asyncio.server import serve

    async with serve(_handler, "127.0.0.1", 0) as server:                # TLS нет, а адрес начинается с wss://
        port = server.sockets[0].getsockname()[1]
        res = await diagnostics.ws_handshake(f"wss://127.0.0.1:{port}/rtc", timeout=5)
    assert res["ok"] is False and res["stage"] == "tcp" and "нет TLS" in res["hint"]      # сервер просто сбрасывает соединение: отличить от файрвола нельзя, но подсказка названа


async def test_status_codes_are_named_by_stage():
    from websockets.asyncio.server import serve

    def answer(code):
        return lambda conn, request: conn.respond(code, "x\n")

    for code, stage in ((401, "auth"), (403, "auth"), (502, "proxy"), (404, "route")):
        async with serve(_handler, "127.0.0.1", 0, process_request=answer(code)) as server:
            port = server.sockets[0].getsockname()[1]
            res = await diagnostics.ws_handshake(f"ws://127.0.0.1:{port}/rtc?access_token=T", timeout=5)
        assert res["ok"] is False and res["status"] == code and res["stage"] == stage, (code, res)
        if stage == "auth":
            assert "LIVEKIT_API_KEY" in res["hint"]                     # 401 при корректном токене — не «норма»: названа причина


async def test_closed_port_is_a_tcp_problem():
    with socket.socket() as s:                                           # свободный порт, на котором никто не слушает
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    res = await diagnostics.ws_handshake(f"ws://127.0.0.1:{port}/rtc", timeout=3)
    assert res["ok"] is False and res["stage"] == "tcp"


def test_dns_failure_and_timeout_are_classified():
    assert diagnostics.classify_failure(socket.gaierror(-2, "Name or service not known"), None)[0] == "dns"
    assert diagnostics.classify_failure(TimeoutError(), None)[0] == "tcp"
    wrapped = OSError("connect failed")
    wrapped.__cause__ = socket.gaierror(-2, "x")
    assert diagnostics.classify_failure(wrapped, None)[0] == "dns"       # причина ищется по цепочке исключений


@pytest.mark.parametrize("app,lk,warns,infos", [
    ("https://meet.example.org", "wss://meet.example.org/livekit", 0, 0),                     # нормальный адрес — замечаний нет
    ("https://meet.example.org", "wss://203.0.113.10/livekit", 1, 2),                        # IP при wss://: сертификат не будет доверенным (+ сведения: другое имя, диапазон документации «частный»)
    ("https://meet.example.org", "ws://meet.example.org/livekit", 1, 0),                      # ws:// на странице https
    ("https://meet.example.org", "wss://livekit/livekit", 1, 1),                              # короткое внутреннее имя
    ("https://meet.example.org", "wss://127.0.0.1/livekit", 1, 1),                            # служебный адрес
    ("https://meet.example.org", "wss://calls.example.org/livekit", 0, 1),                    # другое имя — только к сведению
    ("https://meet.example.org", "wss://10.0.0.5/livekit", 1, 2),                             # внутренний IP: предупреждение + сведения
])
def test_public_url_review(app, lk, warns, infos):
    r = diagnostics.public_url_review(Settings(app_public_url=app, livekit_public_url=lk))
    assert sum(x["level"] == "warn" for x in r) == warns and sum(x["level"] == "info" for x in r) == infos, r


def test_empty_or_malformed_public_url_is_reported():
    assert diagnostics.public_url_review(Settings(app_public_url="https://meet.example.org", livekit_public_url=""))[0]["level"] == "warn"
    assert diagnostics.public_url_review(Settings(app_public_url="https://meet.example.org", livekit_public_url="https://meet.example.org"))[0]["level"] == "warn"


def test_verdict_names_the_tls_stage_instead_of_blaming_the_proxy():
    ok = {"ok": True, "status": 101}
    bad = {"ok": False, "stage": "tls", "hint": "самоподписанный сертификат: устройства ему не доверяют, и звонки не подключатся"}
    rep = {"checks": {"livekit": {"rtc_v1_internal": ok, "rtc_ws_internal": ok, "rtc_v1_public": bad, "rtc_ws_public": bad,
                                  "public_url_review": [{"level": "warn", "text": "Адрес задан IP-адресом при wss://"}]}}, "asr": {"ready": True}}
    text = "\n".join(diagnostics.verdict(rep))
    assert "TLS" in text and "самоподписанный" in text and "прокси" not in text.lower() and "IP-адресом" in text
