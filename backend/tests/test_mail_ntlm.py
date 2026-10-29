"""SMTP AUTH NTLM (Microsoft Exchange): настоящий обмен NTLMv2 с принимающей стороной pyspnego, запрет NTLMv1, диагностика способов входа до и после STARTTLS,
классификация ошибок и отсутствие секретов в журналах. SMTP-сервер подменён; домены и имена — нейтральные."""
from __future__ import annotations

import base64
import logging
import smtplib
import socket

import pytest
import spnego

from app.services import mail as mailmod

from .conftest import login

PASSWORD = "Ntlm-Pass-456xyz"
NTLM = {"name": "Exchange relay", "host": "mail.example.local", "port": 25, "security": "starttls", "auth_type": "ntlm", "username": "CORP\\svc-mail",
        "secret": PASSWORD, "from_address": "noreply@example.local", "from_name": "Peregovorka", "timeout_s": 10}


class NtlmSMTP:
    """Подмена smtplib.SMTP: способы входа до/после STARTTLS задаются классом; обмен NTLM принимает настоящий acceptor pyspnego (проверяет NTLMv2)."""

    pre: dict = {"auth": "NTLM"}
    post: dict = {"auth": "NTLM"}
    last: "NtlmSMTP | None" = None
    sent: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.esmtp_features = dict(NtlmSMTP.pre)
        self.state, self.log, self.tls, self.authed, self.acceptor = "idle", [], False, False, None
        NtlmSMTP.last = self

    def ehlo(self):
        return (250, b"ok")

    def starttls(self, context=None):
        self.tls = True
        self.esmtp_features = dict(NtlmSMTP.post)
        return (220, b"ready")

    def docmd(self, cmd, args=""):
        if cmd == "AUTH":
            assert args == "NTLM"
            self.log.append("AUTH NTLM")
            self.acceptor = spnego.server(options=spnego.NegotiateOptions.use_ntlm, protocol="ntlm")
            self.state = "type1"
            return (334, b"")
        self.log.append("token")
        token = base64.b64decode(cmd, validate=True)
        if self.state == "type1":
            self.state = "type3"
            return (334, base64.b64encode(self.acceptor.step(token)))
        try:
            self.acceptor.step(token)
        except spnego.exceptions.SpnegoError:
            return (535, b"5.7.3 Authentication unsuccessful")
        self.authed = True
        return (235, b"2.7.0 Authentication successful")

    def login(self, user, password):                     # профиль «Логин и пароль»: настоящий smtplib.login выбрал бы метод из объявленных
        raise AssertionError("login() не должен вызываться, если сервер не объявил PLAIN/LOGIN/CRAM-MD5")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        assert self.authed, "письмо отправлено без аутентификации"
        NtlmSMTP.sent.append(msg)
        return {}

    def quit(self):
        return (221, b"bye")


@pytest.fixture(autouse=True)
def ntlm_smtp(monkeypatch, tmp_path):
    NtlmSMTP.pre, NtlmSMTP.post, NtlmSMTP.last, NtlmSMTP.sent = {"auth": "NTLM"}, {"auth": "NTLM"}, None, []
    accounts = tmp_path / "ntlm_users"
    accounts.write_text(f"CORP:svc-mail:{PASSWORD}\n:svc-mail@corp.example:{PASSWORD}\n")
    monkeypatch.setenv("NTLM_USER_FILE", str(accounts))
    monkeypatch.delenv("LM_COMPAT_LEVEL", raising=False)
    monkeypatch.setattr(smtplib, "SMTP", NtlmSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", NtlmSMTP)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.0.2.25", 25))])
    return NtlmSMTP


def add(client, **over):
    login(client, "root")
    r = client.post("/api/v1/admin/mail/profiles", json={**NTLM, **over})
    assert r.status_code == 201, r.text
    return r.json()


def cfg(**over) -> mailmod.SmtpConfig:
    base = dict(host="mail.example.local", port=25, security="starttls", auth_type="ntlm", username="CORP\\svc-mail", password=PASSWORD,
                from_address="noreply@example.local")
    return mailmod.SmtpConfig(**{**base, **over})


# ------------------------------------------------------------------------------------------------ вход NTLM
@pytest.mark.parametrize("user", ["CORP\\svc-mail", "svc-mail@corp.example"])
def test_ntlmv2_login_succeeds_for_domain_backslash_and_upn_names(client, user):
    p = add(client, username=user)
    assert p["auth_type"] == "ntlm" and p["secret_set"] is True and PASSWORD not in str(p)
    res = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check").json()
    assert res["ok"] is True and [s["stage"] for s in res["stages"]] == ["dns", "connect", "tls", "auth_methods", "auth"]
    assert NtlmSMTP.last.authed and NtlmSMTP.last.log == ["AUTH NTLM", "token", "token"]        # AUTH NTLM → Type 1 → Type 3
    assert "NTLMv2" in res["stages"][-1]["message"]


def test_test_send_and_queue_use_ntlm(client):
    p = add(client)
    r = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/test-send", json={"to": "user1@example.local"}).json()
    assert r["ok"] is True and NtlmSMTP.sent[-1]["To"] == "user1@example.local" and NtlmSMTP.last.authed


def test_wrong_password_is_an_auth_error_not_a_connection_error():
    with pytest.raises(mailmod.MailError) as e:
        mailmod._ntlm_login(NtlmSMTP("h", 25), cfg(password="not-the-password-1"))
    assert e.value.stage == "auth" and "отклонил учётные данные NTLM" in e.value.message and "not-the-password" not in e.value.message


def test_ntlmv1_is_never_sent_even_if_environment_lowers_the_compatibility_level(monkeypatch):
    monkeypatch.setenv("LM_COMPAT_LEVEL", "0")           # pyspnego при этом строит ответ NTLMv1; принимающая сторона такой ответ приняла бы
    server = NtlmSMTP("h", 25)
    with pytest.raises(mailmod.MailError) as e:
        mailmod._ntlm_login(server, cfg())
    assert e.value.stage == "auth" and "NTLMv1" in e.value.message and "небезопасен" in e.value.message
    assert server.authed is False and server.log == ["AUTH NTLM", "token"]       # Type 3 не отправлялся


def test_v2_detection_on_real_messages(monkeypatch):
    ctx = spnego.client("CORP\\svc-mail", PASSWORD, hostname="h", service="SMTP", protocol="ntlm", options=spnego.NegotiateOptions.use_ntlm)
    acc = spnego.server(options=spnego.NegotiateOptions.use_ntlm, protocol="ntlm")
    t3 = ctx.step(acc.step(ctx.step()))
    assert mailmod.ntlm_message_type(t3) == 3 and mailmod.ntlm_is_v2_authenticate(t3) is True
    monkeypatch.setenv("LM_COMPAT_LEVEL", "0")
    ctx1 = spnego.client("CORP\\svc-mail", PASSWORD, hostname="h", service="SMTP", protocol="ntlm", options=spnego.NegotiateOptions.use_ntlm)
    acc1 = spnego.server(options=spnego.NegotiateOptions.use_ntlm, protocol="ntlm")
    t3v1 = ctx1.step(acc1.step(ctx1.step()))
    assert mailmod.ntlm_is_v2_authenticate(t3v1) is False
    assert mailmod.ntlm_is_v2_authenticate(b"") is False and mailmod.ntlm_is_v2_authenticate(b"NTLMSSP\x00\x03\x00\x00\x00") is False


def test_server_that_answers_with_garbage_or_non_ntlm_is_rejected_cleanly():
    class Odd(NtlmSMTP):
        def docmd(self, cmd, args=""):
            return (334, b"") if cmd == "AUTH" else (334, base64.b64encode(b"not an ntlm challenge"))

    with pytest.raises(mailmod.MailError) as e:
        mailmod._ntlm_login(Odd("h", 25), cfg())
    assert e.value.stage == "auth" and "Type 2" in e.value.message


# ------------------------------------------------------------------------------ способы входа и подсказки
def test_diagnostics_show_methods_before_and_after_starttls(client):
    NtlmSMTP.pre, NtlmSMTP.post = {"auth": "NTLM"}, {"auth": "NTLM LOGIN PLAIN"}
    p = add(client)
    res = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check").json()
    msg = next(s["message"] for s in res["stages"] if s["stage"] == "auth_methods")
    assert "NTLM, LOGIN, PLAIN" in msg and "до STARTTLS: NTLM" in msg


def test_login_profile_against_ntlm_only_server_explains_instead_of_reporting_no_connection(client):
    p = add(client, auth_type="login", username="svc-mail")
    res = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check").json()
    assert res["ok"] is False and res["stage"] == "auth"
    assert "NTLM" in res["message"] and "Windows NTLM (Microsoft Exchange)" in res["message"] and "нет соединения" not in res["message"]


def test_login_with_security_none_hints_to_enable_starttls():
    s = NtlmSMTP("h", 25)
    s.esmtp_features = {"auth": "NTLM", "starttls": ""}
    with pytest.raises(mailmod.MailError) as e:
        mailmod._require_login_mechanism(s, cfg(auth_type="login", security="none"))
    assert "STARTTLS" in e.value.message and "NTLM" in e.value.message


def test_login_works_after_starttls_when_server_offers_login_only_there(client, monkeypatch):
    """Сначала проверяется простой путь: если после STARTTLS сервер предлагает LOGIN, достаточно «Логин и пароль» — NTLM не нужен."""
    NtlmSMTP.pre, NtlmSMTP.post = {"auth": "NTLM"}, {"auth": "NTLM LOGIN"}
    calls = []
    monkeypatch.setattr(NtlmSMTP, "login", lambda self, u, p: calls.append((u, p)))
    p = add(client, auth_type="login", username="svc-mail")
    assert client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check").json()["ok"] is True and calls == [("svc-mail", PASSWORD)]


def test_ntlm_profile_against_server_without_ntlm_explains():
    s = NtlmSMTP("h", 25)
    s.esmtp_features = {"auth": "PLAIN LOGIN"}
    with pytest.raises(mailmod.MailError) as e:
        mailmod._ntlm_login(s, cfg())
    assert e.value.stage == "auth" and "не предлагает NTLM" in e.value.message and "PLAIN, LOGIN" in e.value.message


def test_server_without_any_auth_is_reported_as_auth_problem():
    s = NtlmSMTP("h", 25)
    s.esmtp_features = {}
    with pytest.raises(mailmod.MailError) as e:
        mailmod._require_login_mechanism(s, cfg(auth_type="login"))
    assert e.value.stage == "auth" and "не объявлены" in e.value.message


def test_classify_no_suitable_method_is_auth_not_connect():
    err = mailmod.classify(smtplib.SMTPException("No suitable authentication method found."), cfg(auth_type="login"))
    assert err.stage == "auth" and "нет соединения" not in err.message
    assert mailmod.classify(smtplib.SMTPException("something else"), cfg()).stage == "smtp"            # остальное — не «нет соединения»
    assert mailmod.classify(ConnectionRefusedError(), cfg()).stage == "connect"                       # настоящие сетевые ошибки по-прежнему connect


# ----------------------------------------------------------------------------------------- профили и защита
def test_ntlm_requires_encrypted_channel_username_and_password(client):
    login(client, "root")
    assert client.post("/api/v1/admin/mail/profiles", json={**NTLM, "security": "none", "port": 25}).status_code == 422
    assert client.post("/api/v1/admin/mail/profiles", json={**NTLM, "username": ""}).status_code == 422
    assert client.post("/api/v1/admin/mail/profiles", json={**NTLM, "secret": ""}).status_code == 422
    assert client.post("/api/v1/admin/mail/profiles", json={**NTLM, "auth_type": "digest-md5"}).status_code == 422
    p = add(client)
    assert client.patch(f"/api/v1/admin/mail/profiles/{p['id']}", json={"security": "none"}).status_code == 422       # понизить защиту нельзя
    assert client.patch(f"/api/v1/admin/mail/profiles/{p['id']}", json={"security": "ssl", "port": 465}).status_code == 200


def test_existing_profiles_keep_working_without_changes(client, monkeypatch):
    """Профили none/login не затронуты: без объявленных способов (подмена без esmtp_features) вход идёт прежним путём smtplib.login."""
    calls = []

    class Plain(NtlmSMTP):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            del self.esmtp_features

        def starttls(self, context=None):
            return (220, b"ready")

        def login(self, u, p):
            calls.append((u, p))

    monkeypatch.setattr(smtplib, "SMTP", Plain)
    p = add(client, auth_type="login", username="svc-mail")
    assert client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check").json()["ok"] is True and calls == [("svc-mail", PASSWORD)]
    q = add(client, name="Без входа", auth_type="none", username="")
    assert client.post(f"/api/v1/admin/mail/profiles/{q['id']}/check").json()["stages"][-1]["message"].startswith("Без аутентификации")


def test_no_password_or_ntlm_tokens_in_logs_audit_or_responses(client, caplog):
    caplog.set_level(logging.DEBUG)
    p = add(client)
    res = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check")
    bad = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/test-send", json={"to": "user1@example.local"})
    wrong = add(client, name="Неверный пароль", secret="another-wrong-password-9")
    fail = client.post(f"/api/v1/admin/mail/profiles/{wrong['id']}/check")
    blob = caplog.text + res.text + bad.text + fail.text + client.get("/api/v1/admin/audit").text + client.get("/api/v1/admin/mail/profiles").text
    for secret in (PASSWORD, "another-wrong-password-9", "TlRMTVNTUA"):          # пароль и base64 от «NTLMSSP\0» (начало любого сообщения NTLM)
        assert secret not in blob, secret
