"""SMTP AUTH NTLM против настоящего SMTP-сервера на сокетах: реальный smtplib, реальный STARTTLS с проверкой сертификата, реальный обмен NTLMv2 (принимающая сторона —
pyspnego). Без подмены smtplib: проверяется то, что подставные объекты скрыть не могут (разбор объявленных способов входа, переход на TLS, порядок команд)."""
from __future__ import annotations

import base64
import datetime
import ipaddress
import socket
import ssl
import threading

import pytest
import spnego
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.services import mail as mailmod

PASSWORD = "Ntlm-Real-Pass-789"


def _make_cert(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    crt, pem = tmp_path / "server.crt", tmp_path / "server.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return str(crt), str(pem)


class NtlmServer:
    """Минимальный SMTP: EHLO (способы входа до и после STARTTLS задаются), STARTTLS, AUTH NTLM (334 → Type 1 → 334 Type 2 → Type 3 → 235/535), MAIL/RCPT/DATA/QUIT."""

    def __init__(self, crt, key, pre="NTLM", post="NTLM"):
        self.pre, self.post, self.messages, self.step3_seen, self.auth_ok = pre, post, [], 0, 0
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(crt, key)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.sock.close()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        conn.settimeout(10)
        f = conn.makefile("rwb", buffering=0)
        tls, authed, acceptor, state = False, False, None, ""

        def say(line: str):
            f.write(line.encode() + b"\r\n")

        def readline() -> str:
            return f.readline().decode().rstrip("\r\n")

        try:
            say("220 mail.example.local ESMTP ready")
            while True:
                line = readline()
                if not line:
                    return
                cmd = line.split(" ", 1)[0].upper()
                if state in ("t1", "t3"):
                    token = base64.b64decode(line, validate=True)
                    if state == "t1":
                        state = "t3"
                        say("334 " + base64.b64encode(acceptor.step(token)).decode())
                        continue
                    self.step3_seen += 1
                    state = ""
                    try:
                        acceptor.step(token)
                        authed = True
                        self.auth_ok += 1
                        say("235 2.7.0 Authentication successful")
                    except spnego.exceptions.SpnegoError:
                        say("535 5.7.3 Authentication unsuccessful")
                    continue
                if cmd in ("EHLO", "HELO"):
                    say("250-mail.example.local")
                    say("250-SIZE 10485760")
                    if not tls:
                        say("250-STARTTLS")
                    methods = self.post if tls else self.pre
                    if methods:
                        say(f"250-AUTH {methods}")
                    say("250 8BITMIME")
                elif cmd == "STARTTLS":
                    say("220 2.0.0 SMTP server ready")
                    conn = self.ctx.wrap_socket(conn, server_side=True)
                    f = conn.makefile("rwb", buffering=0)
                    tls = True
                elif cmd == "AUTH":
                    if line.upper() != "AUTH NTLM":
                        say("504 5.7.4 Unrecognized authentication type")
                        continue
                    acceptor = spnego.server(options=spnego.NegotiateOptions.use_ntlm, protocol="ntlm")
                    state = "t1"
                    say("334 NTLM supported")
                elif cmd == "MAIL":
                    say("250 2.1.0 Sender OK" if authed else "530 5.7.1 Client was not authenticated")
                elif cmd == "RCPT":
                    say("250 2.1.5 Recipient OK")
                elif cmd == "DATA":
                    say("354 Start mail input")
                    body = []
                    while True:
                        ln = readline()
                        if ln == ".":
                            break
                        body.append(ln)
                    self.messages.append("\n".join(body))
                    say("250 2.6.0 Queued")
                elif cmd == "QUIT":
                    say("221 2.0.0 Service closing")
                    return
                else:
                    say("250 OK")
        except Exception:  # noqa: BLE001 — обрыв клиента посреди обмена для сервера не ошибка
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass


@pytest.fixture
def smtp(tmp_path, monkeypatch):
    crt, key = _make_cert(tmp_path)
    users = tmp_path / "users"
    users.write_text(f"CORP:svc-mail:{PASSWORD}\n")
    monkeypatch.setenv("NTLM_USER_FILE", str(users))
    monkeypatch.delenv("LM_COMPAT_LEVEL", raising=False)
    servers: list[NtlmServer] = []

    def make(pre="NTLM", post="NTLM") -> tuple[NtlmServer, str]:
        s = NtlmServer(crt, key, pre, post)
        servers.append(s)
        return s, crt

    yield make
    for s in servers:
        s.close()


def cfg(server: NtlmServer, ca: str, **over) -> mailmod.SmtpConfig:
    base = dict(host="localhost", port=server.port, security="starttls", auth_type="ntlm", username="CORP\\svc-mail", password=PASSWORD,
                from_address="noreply@example.local", from_name="Peregovorka", timeout=10, verify_cert=True, ca_file=ca)
    return mailmod.SmtpConfig(**{**base, **over})


def test_real_starttls_ntlmv2_login_and_delivery(smtp):
    server, ca = smtp()
    c = cfg(server, ca)
    res = mailmod.check_connection(c)
    assert res["ok"] is True, res
    assert [s["stage"] for s in res["stages"]] == ["dns", "connect", "tls", "auth_methods", "auth"]
    assert "проверен" in res["stages"][2]["message"] and "NTLMv2" in res["stages"][-1]["message"]
    mailmod.send(c, mailmod.build_message(c, "user1@example.local", "Тема NTLM", "Текст письма"))
    assert server.auth_ok == 2 and len(server.messages) == 1                       # вход при проверке и вход при отправке
    assert "To: user1@example.local" in server.messages[0] and "noreply@example.local" in server.messages[0]


def test_real_server_shows_methods_before_and_after_tls_and_login_is_enough_when_offered(smtp, monkeypatch):
    server, ca = smtp(pre="NTLM", post="NTLM PLAIN LOGIN")
    res = mailmod.check_connection(cfg(server, ca))
    msg = next(s["message"] for s in res["stages"] if s["stage"] == "auth_methods")
    assert "NTLM, PLAIN, LOGIN" in msg and "до STARTTLS: NTLM" in msg
    # профиль «Логин и пароль» к такому серверу проходит предварительную проверку способов (вход затем делает smtplib)
    import smtplib
    s = smtplib.SMTP("localhost", server.port, timeout=5)
    s.ehlo()
    s.starttls(context=mailmod._context(cfg(server, ca)))
    s.ehlo()
    mailmod._require_login_mechanism(s, cfg(server, ca, auth_type="login"))        # не бросает
    s.close()


def test_real_login_profile_against_ntlm_only_server_is_an_auth_error_with_a_hint(smtp):
    server, ca = smtp()
    res = mailmod.check_connection(cfg(server, ca, auth_type="login", username="svc-mail"))
    assert res["ok"] is False and res["stage"] == "auth", res
    assert "NTLM" in res["message"] and "Windows NTLM (Microsoft Exchange)" in res["message"] and "нет соединения" not in res["message"]


def test_real_wrong_password_is_rejected_as_auth(smtp):
    server, ca = smtp()
    res = mailmod.check_connection(cfg(server, ca, password="definitely-wrong-1"))
    assert res["ok"] is False and res["stage"] == "auth" and "отклонил учётные данные NTLM" in res["message"] and "definitely-wrong" not in res["message"]
    assert server.auth_ok == 0 and server.step3_seen == 1


def test_real_certificate_is_still_verified_with_ntlm(smtp):
    server, _ca = smtp()
    res = mailmod.check_connection(cfg(server, ""))           # сертификат самоподписанный и не доверен
    assert res["ok"] is False and res["stage"] == "certificate" and server.step3_seen == 0       # до NTLM дело не дошло, учётные данные не отправлялись


def test_real_ntlmv1_never_reaches_the_server(smtp, monkeypatch):
    server, ca = smtp()
    monkeypatch.setenv("LM_COMPAT_LEVEL", "0")
    res = mailmod.check_connection(cfg(server, ca))
    assert res["ok"] is False and res["stage"] == "auth" and "NTLMv1" in res["message"]
    assert server.step3_seen == 0 and server.auth_ok == 0


def test_real_server_without_ntlm_for_ntlm_profile(smtp):
    server, ca = smtp(pre="PLAIN LOGIN", post="PLAIN LOGIN")
    res = mailmod.check_connection(cfg(server, ca))
    assert res["ok"] is False and res["stage"] == "auth" and "не предлагает NTLM" in res["message"] and "PLAIN, LOGIN" in res["message"]


def test_real_server_without_any_auth(smtp):
    server, ca = smtp(pre="", post="")
    res = mailmod.check_connection(cfg(server, ca, auth_type="login", username="svc-mail"))
    assert res["ok"] is False and res["stage"] == "auth" and "не объявлены" in res["message"]
