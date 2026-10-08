"""Исходящая почта (профили SMTP, диагностика, очередь с повторами) и доставка материалов встречи. SMTP подменён; адреса и имена — нейтральные."""
from __future__ import annotations

import smtplib
import socket
import ssl
from email.message import EmailMessage

import pytest

from app.models import MailMessage, Protocol
from app.services import mail as mailmod
from app.workers.mail_queue import process_once

from .conftest import OTHER_GROUP, login, make_room, put_settings
from .test_admin_features import _drain
from .test_transcripts import _join

LEADERS = [{"subject_type": "group", "subject_ref": OTHER_GROUP, "display_name": "Руководители"}]
SMTP = {"name": "Основной relay", "host": "smtp.example.local", "port": 587, "security": "starttls", "auth_type": "login", "username": "svc-mail",
        "secret": "Smtp-Pass-123!", "from_address": "noreply@example.local", "from_name": "Peregovorka", "timeout_s": 10}


class FakeSMTP:
    """Подмена smtplib.SMTP / SMTP_SSL: фиксирует письма, умеет падать на заданном этапе."""

    sent: list[EmailMessage] = []
    fail_at: str | None = None
    login_calls: list[tuple[str, str]] = []

    def __init__(self, host, port, timeout=None, context=None):
        if FakeSMTP.fail_at == "connect":
            raise ConnectionRefusedError(111, "refused")
        if FakeSMTP.fail_at == "timeout":
            raise TimeoutError()
        self.host, self.port = host, port

    def ehlo(self):
        return (250, b"ok")

    def starttls(self, context=None):
        if FakeSMTP.fail_at == "cert":
            raise ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate")
        if FakeSMTP.fail_at == "tls":
            raise ssl.SSLError("handshake failure")
        return (220, b"ready")

    def login(self, user, password):
        FakeSMTP.login_calls.append((user, password))
        if FakeSMTP.fail_at == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        if FakeSMTP.fail_at == "recipient":
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (550, b"no such user")})
        if FakeSMTP.fail_at == "recipient_temp":
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (451, b"try later")})
        if FakeSMTP.fail_at == "sender":
            raise smtplib.SMTPSenderRefused(553, b"not allowed", from_addr)
        if FakeSMTP.fail_at == "data_temp":
            raise smtplib.SMTPDataError(451, b"busy")
        FakeSMTP.sent.append(msg)
        return {}

    def quit(self):
        return (221, b"bye")


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail_at, FakeSMTP.login_calls = [], None, []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.0.2.25", 587))])
    return FakeSMTP


def add_profile(client, **over):
    login(client, "root")
    r = client.post("/api/v1/admin/mail/profiles", json={**SMTP, **over})
    assert r.status_code == 201, r.text
    return r.json()


def run_queue(client, passes: int = 1) -> int:
    n = 0
    for _ in range(passes):
        n += client.portal.call(lambda: process_once(client.app_obj.state.session_maker, client.app_obj.state.delivery))
    return n


def messages(client) -> list[dict]:
    login(client, "root")
    return client.get("/api/v1/admin/mail/messages").json()["items"]


# --------------------------------------------------------------------------------------------------- профили
def test_profile_crud_secret_hidden_first_is_active_and_activation_exclusive(client):
    p = add_profile(client)
    assert p["is_active"] is True and p["secret_set"] is True and "Smtp-Pass" not in str(p)
    assert "Smtp-Pass" not in client.get("/api/v1/admin/mail/profiles").text
    q = add_profile(client, name="Резервный relay", host="smtp2.example.local", security="ssl", port=0)
    assert q["is_active"] is False and q["port"] == 465                  # порт по умолчанию — по режиму защиты
    client.post(f"/api/v1/admin/mail/profiles/{q['id']}/activate")
    items = client.get("/api/v1/admin/mail/profiles").json()["items"]
    assert [i["is_active"] for i in sorted(items, key=lambda i: i["name"])] == [False, True]    # «Основной relay», «Резервный relay»
    assert client.patch(f"/api/v1/admin/mail/profiles/{q['id']}", json={"secret": ""}).status_code == 422
    assert client.patch(f"/api/v1/admin/mail/profiles/{q['id']}", json={"from_name": "Другое имя"}).json()["secret_set"] is True
    assert client.delete(f"/api/v1/admin/mail/profiles/{q['id']}").status_code == 204
    assert [i["is_active"] for i in client.get("/api/v1/admin/mail/profiles").json()["items"]] == [True]     # активным стал оставшийся
    audit = client.get("/api/v1/admin/audit").text
    assert "mail.profile.create" in audit and "Smtp-Pass" not in audit


@pytest.mark.parametrize("bad", [{"host": "bad host!"}, {"security": "tls13"}, {"port": 99999}, {"from_address": "не-адрес"}, {"timeout_s": 1},
                                 {"auth_type": "login", "username": ""}, {"from_name": "Имя\nBcc: x@example.local"}])
def test_profile_validation(client, bad):
    login(client, "root")
    assert client.post("/api/v1/admin/mail/profiles", json={**SMTP, **bad}).status_code == 422


def test_leader_and_user_cannot_see_or_change_smtp(client):
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    login(client, "carol")
    for path in ("/api/v1/admin/mail/profiles", "/api/v1/admin/mail/messages"):
        assert client.get(path).status_code == 403
    got = client.get(f"/api/v1/rooms/{room['id']}/manage")
    assert got.status_code == 200 and "smtp" not in got.text.lower() and "Smtp-Pass" not in got.text


# -------------------------------------------------------------------------------------------------- диагностика
def test_connection_check_ok_and_each_failure_stage_has_a_clear_reason(client, fake_smtp):
    p = add_profile(client)
    ok = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check").json()
    assert ok["ok"] is True and [s["stage"] for s in ok["stages"]] == ["dns", "connect", "tls", "auth"]
    assert fake_smtp.login_calls[-1] == ("svc-mail", "Smtp-Pass-123!")
    expected = {"connect": "connect", "timeout": "connect", "cert": "certificate", "tls": "tls", "auth": "auth"}
    for fail, stage in expected.items():
        fake_smtp.fail_at = fail
        res = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check").json()
        assert res["ok"] is False and res["stage"] == stage and res["message"], fail
        assert "Smtp-Pass" not in str(res)
    fake_smtp.fail_at = None

    def no_dns(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")

    socket.getaddrinfo = no_dns   # восстановит monkeypatch фикстуры
    res = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/check").json()
    assert res["stage"] == "dns" and "DNS" in res["message"]


def test_test_send_goes_immediately_and_reports_rejections(client, fake_smtp):
    p = add_profile(client)
    r = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/test-send", json={"to": "user1@example.local"}).json()
    assert r["ok"] is True and fake_smtp.sent[-1]["To"] == "user1@example.local" and "Peregovorka" in fake_smtp.sent[-1]["From"]
    assert client.post(f"/api/v1/admin/mail/profiles/{p['id']}/test-send", json={"to": "не адрес"}).status_code == 422
    fake_smtp.fail_at = "recipient"
    r = client.post(f"/api/v1/admin/mail/profiles/{p['id']}/test-send", json={"to": "user1@example.local"}).json()
    assert r["ok"] is False and r["message"].startswith("recipient_rejected")
    fake_smtp.fail_at = "sender"
    assert client.post(f"/api/v1/admin/mail/profiles/{p['id']}/test-send", json={"to": "user1@example.local"}).json()["message"].startswith("sender_rejected")
    assert "mail.test_send" in client.get("/api/v1/admin/audit").text


def test_classify_permanent_vs_transient():
    cfg = mailmod.SmtpConfig("h", 25, "none", "none", "")
    assert mailmod.classify(smtplib.SMTPRecipientsRefused({"a@b.c": (550, b"x")}), cfg).transient is False
    assert mailmod.classify(smtplib.SMTPRecipientsRefused({"a@b.c": (451, b"x")}), cfg).transient is True
    assert mailmod.classify(smtplib.SMTPAuthenticationError(535, b"x"), cfg).transient is False
    assert mailmod.classify(ConnectionRefusedError(), cfg).transient is True


# ------------------------------------------------------------------------------------------ доставка материалов
def finished_meeting(client, room, users=("alice", "bob")):
    """Встреча с участниками завершена; готовы протокол и резюме."""
    joins = [_join(client, u, room["id"]) for u in users]
    mid = joins[0]["meeting_id"]

    async def add():
        import uuid
        async with client.app_obj.state.session_maker() as db:
            for kind, text in (("protocol", "# Протокол\n\nРешили провести тест."), ("summary", "Кратко: тест проведён.")):
                db.add(Protocol(meeting_id=uuid.UUID(mid), kind=kind, status="ready", content=text, created_by="test"))
            await db.commit()

    client.portal.call(add)
    login(client, "root")
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    _drain(client)
    return mid


def set_delivery(client, room, spec, user="carol"):
    login(client, user)
    r = client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": spec})
    assert r.status_code == 200, r.text
    return r.json()["mail_delivery"]


def test_leader_chooses_materials_and_recipients_preview_shows_missing_email(client, directory):
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    spec = {"enabled": True, "materials": ["protocol", "summary"], "recipients": {"leaders": False, "participants": True, "emails": ["partner@example.local", "ext@outside.test"]}}
    saved = set_delivery(client, room, spec)
    assert saved["materials"] == ["protocol", "summary"] and saved["recipients"]["participants"] is True
    put_settings(client, "mail_policy", allowed_domains="corp.test, example.local")
    login(client, "carol")
    pv = client.post(f"/api/v1/rooms/{room['id']}/manage/delivery-preview", json={}).json()
    by = {r["email"]: r for r in pv["recipients"]}
    assert by["partner@example.local"]["problem"] is None
    assert by["ext@outside.test"]["problem"] == "domain_not_allowed"          # внешний адрес запрещён глобальной политикой
    assert pv["mail_configured"] is True and pv["allowed_domains"] == ["corp.test", "example.local"]
    assert "smtp" not in str(pv).lower() and "svc-mail" not in str(pv)
    # неверные значения
    assert client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": {"enabled": True, "materials": []}}).status_code == 422
    assert client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": {"materials": ["секрет"]}}).status_code == 422
    assert client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": {"materials": ["protocol"], "recipients": {"emails": ["не адрес"]}}}).status_code == 422
    login(client, "alice")                                                       # обычный участник настроить не может
    assert client.patch(f"/api/v1/rooms/{room['id']}/manage", json={"mail_delivery": spec}).status_code in (403, 404)


def test_auto_delivery_after_meeting_goes_through_queue_with_attachments(client, fake_smtp):
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {"enabled": True, "materials": ["protocol", "summary"], "recipients": {"leaders": False, "participants": True, "emails": ["partner@example.local"]}})
    mid = finished_meeting(client, room)
    rows = messages(client)
    assert {r["recipient"] for r in rows} == {"alice@corp.test", "bob@corp.test", "partner@example.local"}
    assert all(r["state"] == "queued" and r["trigger"] == "auto" and r["kinds"] == ["protocol", "summary"] for r in rows)
    assert run_queue(client) == 3
    rows = messages(client)
    assert all(r["state"] == "sent" and r["delivery"] == "attachment" for r in rows)
    msg = fake_smtp.sent[0]
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert len(names) == 2 and all(n.endswith(".docx") for n in names) and any("Протокол" in n for n in names)
    body = msg.get_body(("plain",)).get_content()
    assert "Кратко: тест проведён." in body and f"/history/{mid}" in body
    # повторно автоматически не отправляется (по встрече — один раз)
    async def again():
        import uuid
        await client.app_obj.state.delivery.run_auto(uuid.UUID(mid))

    client.portal.call(again)
    assert len(messages(client)) == 3
    # в журнале событий — только результат, без содержимого документов
    client.portal.call(client.app_obj.state.journal.flush)
    ev = client.get("/api/v1/admin/journal", params={"limit": 200, "category": "mail"}).json()["items"]
    assert any(e["event"] == "mail_sent" for e in ev)
    assert "Решили провести тест" not in str(ev) and "Smtp-Pass" not in str(ev)


def test_auto_delivery_off_by_default_and_without_mail_profile_messages_wait_then_fail(client, fake_smtp):
    room = make_room(client, moderators=LEADERS)
    finished_meeting(client, room)
    assert messages(client) == []                                                # рассылка у комнаты не включена
    room2 = make_room(client, name="Вторая", moderators=LEADERS)
    set_delivery(client, room2, {"enabled": True, "materials": ["summary"], "recipients": {"participants": True}})
    finished_meeting(client, room2)
    rows = messages(client)
    assert rows and run_queue(client) == len(rows)                               # профиля почты нет — письма остаются в очереди и ждут
    rows = messages(client)
    assert all(r["state"] == "queued" and "нет активного профиля" in r["last_error"] and r["attempts"] == 1 for r in rows)


def test_retry_with_limit_and_permanent_errors_do_not_retry(client, fake_smtp):
    add_profile(client)
    put_settings(client, "mail_policy", max_attempts=3, retry_minutes=1)
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {"enabled": True, "materials": ["summary"], "recipients": {"emails": ["partner@example.local"]}})
    finished_meeting(client, room)
    fake_smtp.fail_at = "connect"
    for attempt in (1, 2, 3):
        assert run_queue(client) == 1, attempt
        row = messages(client)[0]
        assert row["attempts"] == attempt and row["state"] == ("queued" if attempt < 3 else "failed") and row["last_error"].startswith("connect")
        if attempt < 3:
            async def due():
                from sqlalchemy import update
                from app.models import utcnow
                async with client.app_obj.state.session_maker() as db:
                    await db.execute(update(MailMessage).values(next_attempt_at=utcnow()))
                    await db.commit()

            client.portal.call(due)
    assert run_queue(client) == 0                                                # после исчерпания повторов письмо не трогаем
    # ручной повтор администратором
    fake_smtp.fail_at = None
    mid = messages(client)[0]["id"]
    assert client.post(f"/api/v1/admin/mail/messages/{mid}/retry").status_code == 200
    assert run_queue(client) == 1 and messages(client)[0]["state"] == "sent"


def test_permanent_error_fails_at_once(client, fake_smtp):
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {"enabled": True, "materials": ["summary"], "recipients": {"emails": ["nobody@example.local"]}})
    finished_meeting(client, room)
    fake_smtp.fail_at = "recipient"
    run_queue(client)
    row = messages(client)[0]
    assert row["state"] == "failed" and row["attempts"] == 1 and "recipient_rejected" in row["last_error"]


def test_manual_send_preview_confirm_audit_and_resend(client, fake_smtp):
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    mid = finished_meeting(client, room)
    login(client, "carol")                                                       # руководитель комнаты
    pv = client.get(f"/api/v1/meetings/{mid}/delivery").json()
    assert {m["kind"] for m in pv["materials"]} == {"protocol", "summary"} and all(m["available"] for m in pv["materials"])
    assert {r["email"] for r in pv["recipients"]} >= {"alice@corp.test", "bob@corp.test"} and pv["mail_configured"] is True
    r = client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol", "transcript"], "emails": ["alice@corp.test", "extra@example.local"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["queued"] == 2 and out["kinds"] == ["protocol"] and out["unavailable"] == ["transcript"]
    assert client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": []}).status_code == 422
    assert client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["не адрес"]}).status_code == 422
    assert client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["alice@corp.test"]}).status_code == 200   # повторная отправка допустима
    login(client, "root")
    audit = [a for a in client.get("/api/v1/admin/audit").json() if a["action"] == "meeting.materials.send"]
    assert len(audit) == 2
    login(client, "alice")                                                       # обычный участник отправлять не может
    assert client.get(f"/api/v1/meetings/{mid}/delivery").status_code in (403, 404)
    assert client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["alice@corp.test"]}).status_code in (403, 404)


def test_manual_send_requires_mail_and_finished_meeting(client):
    room = make_room(client, moderators=LEADERS)
    live = _join(client, "alice", room["id"])["meeting_id"]
    login(client, "carol")
    assert client.get(f"/api/v1/meetings/{live}/delivery").status_code in (404, 409)
    mid = finished_meeting(client, make_room(client, name="Другая", moderators=LEADERS))
    login(client, "carol")
    r = client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["alice@corp.test"]})
    assert r.status_code == 409 and "не настроена" in r.json()["detail"]


def test_large_materials_are_sent_as_link_not_attachment(client, fake_smtp, monkeypatch):
    add_profile(client)
    put_settings(client, "mail_policy", max_attachment_mb=1)
    monkeypatch.setattr("app.services.mail_delivery.to_docx", lambda md, title=None: b"x" * (2 * 1024 * 1024))
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {"enabled": True, "materials": ["protocol"], "recipients": {"emails": ["partner@example.local"]}})
    mid = finished_meeting(client, room)
    run_queue(client)
    row = messages(client)[0]
    assert row["state"] == "sent" and row["delivery"] == "link"
    msg = fake_smtp.sent[-1]
    assert list(msg.iter_attachments()) == [] and f"/history/{mid}" in msg.get_body(("plain",)).get_content()


def test_group_leaders_expand_to_member_emails_and_missing_email_is_visible(client, directory):
    add_profile(client)
    directory.add("noemail", "noemail-pass", groups=(OTHER_GROUP,))
    object.__setattr__(directory.users["noemail"][1], "email", None)
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {"enabled": True, "materials": ["summary"], "recipients": {"leaders": True}})
    login(client, "carol")
    pv = client.post(f"/api/v1/rooms/{room['id']}/manage/delivery-preview", json={}).json()
    emails = {r["email"] for r in pv["recipients"] if r["email"]}
    assert "carol@corp.test" in emails
    assert any(r["problem"] == "no_email" and r["name"] for r in pv["recipients"])      # нет адреса — виден в интерфейсе, не молчаливая ошибка


# ---------------------------------------------------------------------------------- текст ответа сервера в диагностике
def _cfg(**over):
    base = dict(host="smtp.example.local", port=587, security="starttls", auth_type="login", username="svc", password="Mail-Pass-7q", from_address="noreply@example.local")
    base.update(over)
    return mailmod.SmtpConfig(**base)


def test_server_reply_text_is_shown_for_all_smtp_response_errors():
    reply = b"5.7.60 SMTP; Client does not have permissions to send as this sender"
    cfg = _cfg()
    cases = [
        smtplib.SMTPSenderRefused(550, reply, "noreply@example.local"),
        smtplib.SMTPDataError(550, reply),
        smtplib.SMTPRecipientsRefused({"user1@example.local": (550, reply)}),
        smtplib.SMTPResponseException(550, reply),
        smtplib.SMTPAuthenticationError(535, b"5.7.3 Authentication unsuccessful"),
    ]
    for exc in cases:
        err = mailmod.classify(exc, cfg)
        assert "Client does not have permissions to send as this sender" in err.message or "Authentication unsuccessful" in err.message, err.message
        assert "550" in err.message or "535" in err.message
    assert "550 5.7.60 SMTP; Client does not have permissions" in mailmod.classify(cases[0], cfg).short()


def test_server_reply_is_decoded_cleaned_and_never_leaks_secrets():
    cfg = _cfg()
    assert mailmod.server_text("Ошибка сервера".encode("cp1251"), cfg) == "Ошибка сервера"                 # не UTF-8 — декодируется запасной кодировкой
    assert mailmod.server_text(b"line1\r\nline2\x00\x07  \tend", cfg) == "line1 line2 end"                  # управляющие символы и переводы строк убраны
    assert len(mailmod.server_text(b"x" * 5000, cfg)) == 300
    assert "Mail-Pass-7q" not in mailmod.server_text(b"5.7.8 bad password Mail-Pass-7q", cfg)              # пароль, повторённый сервером, вырезается
    assert mailmod.server_text(None, cfg) == "" and mailmod.server_text(b"\xff\xfe\xfd", cfg)
    # адреса получателей и тело письма в сообщение не попадают
    err = mailmod.classify(smtplib.SMTPRecipientsRefused({"user1@example.local": (550, b"5.1.1 mailbox unavailable")}), cfg)
    assert "user1@example.local" not in err.message and "mailbox unavailable" in err.message


def test_transient_flag_and_empty_reply_still_work():
    cfg = _cfg()
    e = mailmod.classify(smtplib.SMTPDataError(451, b"4.3.0 try again later"), cfg)
    assert e.transient is True and "try again later" in e.message
    e = mailmod.classify(smtplib.SMTPResponseException(554, b""), cfg)
    assert e.transient is False and "554" in e.message
