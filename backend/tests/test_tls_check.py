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
    assert r["state"] == "self_signed" and r["ok"] is False and r["subject"] == "localhost" and r["fix"]["where"] == "certificate"


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


# ----------------------------------------------------------------------------------------- цепочки: корень → промежуточный → сайт
import datetime as _dt
from cryptography.hazmat.primitives.asymmetric import ec as _ec


def _issue(cn, issuer_key, issuer_name, san=None, ca=False, days=90):
    key = _ec.generate_private_key(_ec.SECP256R1())
    now = _dt.datetime.now(_dt.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    b = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - _dt.timedelta(days=1)).not_valid_after(now + _dt.timedelta(days=days))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True))
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), False)
        b = b.add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(x509.oid.AuthorityInformationAccessOID.CA_ISSUERS, x509.UniformResourceIdentifier("http://ca.example.org/issuing.crt"))]), False)
    b = b.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)          # строгая проверка OpenSSL (Python 3.13+) требует SKI/AKI
    b = b.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer_key or key).public_key()), False)
    key_usage = x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False,
                              key_cert_sign=ca, crl_sign=ca, encipher_only=False, decipher_only=False)
    b = b.add_extension(key_usage, True)
    return key, b.sign(issuer_key or key, hashes.SHA256()), name


def _pki(tmp_path):
    rk, rc, rn = _issue("Test Root CA", None, x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")]), ca=True, days=3650)
    ik, ic, inn = _issue("Test Issuing CA", rk, rn, ca=True, days=1800)
    lk, lc, _ = _issue("localhost", ik, inn, san="localhost")
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
    root = tmp_path / "root.pem"; root.write_bytes(pem(rc))
    return {"root": root, "root_cert": rc, "inter": ic, "leaf": lc, "leaf_key": lk, "pem": pem}


def _serve(tmp_path, pki, send_intermediate):
    chain = tmp_path / ("full.pem" if send_intermediate else "leaf.pem")
    chain.write_bytes(pki["pem"](pki["leaf"]) + (pki["pem"](pki["inter"]) if send_intermediate else b""))
    key = tmp_path / "k.pem"
    key.write_bytes(pki["leaf_key"].private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sctx.load_cert_chain(chain, key)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen(8)

    def run():
        while True:
            try:
                c, _ = sock.accept()
            except OSError:
                return
            try:
                sctx.wrap_socket(c, server_side=True).close()
            except OSError:
                pass
    threading.Thread(target=run, daemon=True).start()
    return sock


def test_full_chain_behind_proxy_is_trusted_and_listed(tmp_path):
    pki = _pki(tmp_path); sock = _serve(tmp_path, pki, True)
    try:
        ctx = ssl.create_default_context(); ctx.load_verify_locations(pki["root"])
        r = tls_check.probe("localhost", sock.getsockname()[1], context=ctx)
        assert r["state"] == "ok" and r["trusted_by_server"] and r["chain_complete"] and [c["role"] for c in r["chain"]][:2] == ["leaf", "intermediate"]
    finally:
        sock.close()


def test_incomplete_chain_is_found_completed_from_aia_and_points_to_proxy(tmp_path):
    pki = _pki(tmp_path); sock = _serve(tmp_path, pki, False)               # прокси «забыл» промежуточный сертификат
    try:
        ctx = ssl.create_default_context(); ctx.load_verify_locations(pki["root"])
        r = tls_check.probe("localhost", sock.getsockname()[1], context=ctx, fetch_aia=True, fetcher=lambda url: pki["inter"])
        assert r["state"] == "incomplete_chain" and r["ok"] is None and r["fix"]["where"] == "reverse_proxy" and "fullchain" in r["fix"]["downloads"]
        assert r["completed_chain_pem"].count("BEGIN CERTIFICATE") == 2 and r["chain_complete"] is False
        # без разрешения на загрузку из AIA сервер ничего не дописывает: остаётся «не доверено», исправлений не изобретаем
        r2 = tls_check.probe("localhost", sock.getsockname()[1], context=ctx, fetch_aia=False)
        assert r2["state"] == "untrusted" and "completed_chain_pem" not in r2
        # подложенный чужой сертификат вместо промежуточного не принимается
        other = _pki(tmp_path)
        r3 = tls_check.probe("localhost", sock.getsockname()[1], context=ctx, fetch_aia=True, fetcher=lambda url: other["inter"])
        assert r3["state"] == "untrusted" and "completed_chain_pem" not in r3
    finally:
        sock.close()


def test_private_ca_is_reported_as_organization_trust_not_as_failure_of_the_addin(tmp_path):
    pki = _pki(tmp_path); sock = _serve(tmp_path, pki, True)
    try:
        r = tls_check.probe("localhost", sock.getsockname()[1], fetch_aia=True, fetcher=lambda url: None)     # корня организации в хранилище этого сервера нет
        assert r["state"] == "untrusted" and r["cause"] == "private_ca" and r["ok"] is None and r["fix"]["where"] == "organization_trust"
        assert "не является" in r["fix"]["text"]
    finally:
        sock.close()


def test_aia_fetch_refuses_internal_addresses():
    from app.services import tls_chain
    for url in ("http://127.0.0.1/ca.crt", "http://localhost/ca.crt", "http://10.0.0.5/ca.crt", "http://169.254.169.254/x", "file:///etc/passwd", "ftp://example.org/ca.crt"):
        assert tls_chain.fetch_aia(url) is None
