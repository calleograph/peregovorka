"""Проверка публичного HTTPS для Outlook: системное доверие + обязательная проверка имени; недоступность — отдельное состояние, а не отказ."""
import datetime as dt
import socket
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.services import tls_check


def _cert(cn, san, days=90, issuer_key=None, issuer_name=None):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer_name or name).public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=days))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), False))
    return key, b.sign(issuer_key or key, hashes.SHA256()), name


@pytest.fixture
def server(tmp_path):
    def start(cn, san, days=90):
        key, cert, _ = _cert(cn, san, days)
        crt, k = tmp_path / f"{cn}.crt", tmp_path / f"{cn}.key"
        crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(crt, k)
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen(5)

        def run():
            while True:
                try:
                    c, _ = sock.accept()
                except OSError:
                    return
                try:
                    ctx.wrap_socket(c, server_side=True).close()
                except OSError:
                    pass
        threading.Thread(target=run, daemon=True).start()
        return sock, crt
    socks = []
    def f(*a, **kw):
        s, crt = start(*a, **kw); socks.append(s); return s.getsockname()[1], crt
    yield f
    for s in socks:
        s.close()


def test_trusted_certificate_with_matching_name_is_ok(server):
    port, crt = server("localhost", "localhost")
    ctx = ssl.create_default_context(); ctx.load_verify_locations(crt)       # «доверенный» центр — именно этот сертификат, как у настоящего клиента
    r = tls_check.probe("localhost", port, context=ctx)
    assert r["state"] == "ok" and r["ok"] is True and r["subject"] == "localhost" and r["days_left"] > 80


def test_untrusted_certificate_is_rejected_and_never_accepted(server):
    port, _ = server("localhost", "localhost")
    r = tls_check.probe("localhost", port)                                   # системное хранилище: самоподписанный не доверен
    assert r["state"] == "untrusted" and r["ok"] is False and r["subject"] == "localhost"


def test_name_mismatch_is_rejected(server):
    port, crt = server("other.example.org", "other.example.org")
    ctx = ssl.create_default_context(); ctx.load_verify_locations(crt)
    r = tls_check.probe("localhost", port, context=ctx)
    assert r["state"] == "name_mismatch" and r["ok"] is False


def test_expiring_is_warning(server):
    port, crt = server("localhost", "localhost", days=5)
    ctx = ssl.create_default_context(); ctx.load_verify_locations(crt)
    assert tls_check.probe("localhost", port, context=ctx)["state"] == "expiring"


def test_unreachable_is_separate_status_not_failure():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    r = tls_check.probe("127.0.0.1", port, timeout=2)
    assert r["state"] == "unreachable" and r["ok"] is None and "Внешняя проверка с сервера недоступна" in r["message"]
