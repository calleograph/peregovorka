"""Шаблоны писем, отправка из истории (тема/текст можно поправить, несколько форматов), политика «нет материала», журнал отправок, защита от подмены заголовков и
подстановки кода в шаблон. SMTP подменён (как в test_mail.py)."""
from __future__ import annotations

from app.models import MailMessage
from app.services import mail_templates as mt

from .conftest import login, make_room, put_settings
from .test_mail import LEADERS, add_profile, fake_smtp, finished_meeting, messages, run_queue, set_delivery  # noqa: F401  (fake_smtp — autouse-фикстура)

ADM = "/api/v1/admin/mail/templates"


# ------------------------------------------------------------------------------------------------ подстановка
def test_variables_are_replaced_only_from_the_whitelist_and_code_is_never_evaluated():
    ctx = {"meeting_title": "Планёрка", "meeting_date": "09.10.2026"}
    assert mt.render("«{{meeting_title}}» от {{ meeting_date }}", ctx) == "«Планёрка» от 09.10.2026"
    evil = "{{ ''.__class__.__mro__ }} {{config}} {{__import__('os')}} ${HOME} {% for x in y %}"
    assert mt.render(evil, ctx) == evil, "всё, что не из белого списка, остаётся обычным текстом"
    assert mt.unknown_variables("{{meeting_title}} {{secret}} {{room_name}}") == ["secret"]
    assert mt.one_line("Тема\r\nBcc: attacker@example.org\x00") == "Тема Bcc: attacker@example.org", "переводы строк в теме убираются"
    subject, body = mt.compose({"subject": "Тема {{meeting_title}}\nBcc: x@y.z", "body": "Текст", "signature": "С уважением"}, {"meeting_title": "A\r\nB"})
    assert "\n" not in subject and "\r" not in subject and body.endswith("С уважением")


# ------------------------------------------------------------------------------------------------ администрирование
def test_admin_manages_templates_default_exists_and_leaders_cannot(client):
    login(client, "root")
    first = client.get(ADM).json()
    assert len(first["items"]) == 1 and first["items"][0]["is_default"] and "{{meeting_title}}" in first["items"][0]["subject"]
    assert {v["name"] for v in first["variables"]} >= {"meeting_title", "meeting_date", "room_name", "organizer_name", "participants", "protocol_name", "project_name"}
    default_id = first["items"][0]["id"]
    new = {"name": "Короткий", "subject": "Итоги: {{room_name}}", "body": "Материалы во вложении.", "signature": "Peregovorka", "materials": ["protocol", "map", "нет-такого"]}
    r = client.post(ADM, json=new)
    assert r.status_code == 201 and r.json()["materials"] == ["protocol", "map"]
    tid = r.json()["id"]
    for bad in ({**new, "subject": "{{evil}}"}, {**new, "name": " "}, {**new, "body": ""}):
        assert client.post(ADM, json=bad).status_code == 422
    assert client.delete(f"{ADM}/{default_id}").status_code == 409, "шаблон по умолчанию удалить нельзя"
    assert client.put(f"{ADM}/{tid}", json={**new, "name": "Короткий 2", "is_default": True}).status_code == 200
    items = {t["name"]: t for t in client.get(ADM).json()["items"]}
    assert items["Короткий 2"]["is_default"] and not items["Стандартный"]["is_default"]
    assert client.delete(f"{ADM}/{default_id}").status_code == 204
    acts = {a["action"] for a in client.get("/api/v1/admin/audit").json()}
    assert {"mail.template.create", "mail.template.update", "mail.template.delete"} <= acts
    login(client, "carol")
    assert client.get(ADM).status_code == 403 and client.post(ADM, json=new).status_code == 403
    assert client.get("/api/v1/mail-templates").status_code == 200, "названия шаблонов видны для выбора в настройках комнаты"


# ------------------------------------------------------------------------------------------------ отправка из истории
def test_manual_send_uses_template_edited_subject_and_text_several_formats_and_is_logged(client, fake_smtp):  # noqa: F811
    add_profile(client)
    room = make_room(client, name="Планёрка ИБ", moderators=LEADERS)
    mid = finished_meeting(client, room)
    login(client, "carol")
    pv = client.get(f"/api/v1/meetings/{mid}/delivery").json()
    t = next(x for x in pv["templates"] if x["is_default"])
    assert "Планёрка ИБ" in t["subject"] and "Планёрка ИБ" in t["body"] and "{{" not in t["subject"] + t["body"] and t["body"].rstrip().endswith("Peregovorka")
    assert set(pv["formats"]) == {"docx", "pdf", "html", "txt", "md"} and {k["kind"] for k in pv["available_kinds"]} >= {"protocol", "summary", "transcript", "map"}
    r = client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["protocol"], "emails": ["alice@corp.test"], "template_id": t["id"], "formats": ["docx", "html"],
                                                                  "subject": "Моя тема\r\nBcc: evil@example.org", "body": "Правка только для этого письма."})
    assert r.status_code == 200, r.text
    assert run_queue(client) == 1
    msg = fake_smtp.sent[-1]
    assert msg["Subject"] == "Моя тема Bcc: evil@example.org" and msg["Bcc"] is None, "тему из формы нельзя использовать для подмены заголовков"
    body = msg.get_body(("plain",)).get_content()
    assert "Правка только для этого письма." in body and f"/history/{mid}" in body
    assert sorted(p.get_filename().rsplit(".", 1)[-1] for p in msg.iter_attachments()) == ["docx", "html"]
    html = next(p for p in msg.iter_attachments() if p.get_filename().endswith(".html")).get_content()
    assert "<table" in html or "Протокол" in html
    # шаблон не изменился от правки в письме
    login(client, "root")
    assert next(x for x in client.get(ADM).json()["items"] if x["id"] == t["id"])["subject"] == "Материалы встречи «{{meeting_title}}» от {{meeting_date}}"
    # журнал по встрече: кто, когда, шаблон, форматы, результат; секретов нет
    login(client, "carol")
    log = client.get(f"/api/v1/meetings/{mid}/delivery/log").json()
    assert len(log) == 1 and log[0]["trigger"] == "manual" and log[0]["state"] == "sent" and log[0]["template"] == "Стандартный" and log[0]["formats"] == ["docx", "html"]
    assert log[0]["by"] and log[0]["recipient"] == "alice@corp.test" and "Smtp-Pass" not in str(log)
    login(client, "alice")
    assert client.get(f"/api/v1/meetings/{mid}/delivery/log").status_code in (403, 404), "журнал отправок — только руководителям и администраторам"


def test_map_is_sent_as_a_link_without_attachment(client, fake_smtp):  # noqa: F811
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    mid = finished_meeting(client, room)

    async def add_map():
        import uuid

        from app.models import ConversationMap
        async with client.app_obj.state.session_maker() as db:
            db.add(ConversationMap(meeting_id=uuid.UUID(mid), status="ready", data={"topics": []}))
            await db.commit()

    client.portal.call(add_map)
    login(client, "carol")
    assert client.post(f"/api/v1/meetings/{mid}/delivery/send", json={"kinds": ["map", "summary"], "emails": ["alice@corp.test"]}).status_code == 200
    run_queue(client)
    msg = fake_smtp.sent[-1]
    assert len(list(msg.iter_attachments())) == 1 and "Карта разговора" in msg.get_body(("plain",)).get_content()


# ------------------------------------------------------------------------------------------------ автоматическая рассылка
def test_auto_delivery_uses_room_template_and_formats_and_respects_missing_policy(client, fake_smtp):  # noqa: F811
    add_profile(client)
    login(client, "root")
    tid = client.post(ADM, json={"name": "Для ИБ", "subject": "ИБ: {{room_name}} {{meeting_date}}", "body": "Привет, {{organizer_name}}", "signature": "", "materials": []}).json()["id"]
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {"enabled": True, "materials": ["protocol"], "template_id": tid, "formats": ["pdf", "txt"], "recipients": {"leaders": False, "participants": True}})
    finished_meeting(client, room)
    rows = messages(client)
    assert rows and all(r["subject"].startswith("ИБ: ") for r in rows)
    run_queue(client, 2)
    assert sorted(p.get_filename().rsplit(".", 1)[-1] for p in fake_smtp.sent[0].iter_attachments()) == ["pdf", "txt"]
    # «если материала нет → не отправлять»: выбран transcript, которого нет; письмо не уходит, событие записано
    room2 = make_room(client, moderators=LEADERS)
    set_delivery(client, room2, {"enabled": True, "materials": ["protocol", "map"], "if_missing": "skip", "recipients": {"leaders": False, "participants": True}})
    before = len(messages(client))
    finished_meeting(client, room2)
    assert len(messages(client)) == before, "часть материалов не готова, а выбрано «не отправлять»"
    client.portal.call(client.app_obj.state.journal.flush)
    ev = client.get("/api/v1/admin/journal", params={"limit": 200, "category": "mail"}).json()["items"]
    assert any(e["event"] == "mail_material_missing" for e in ev) and any(e["event"] == "mail_skipped" for e in ev)


def test_allowed_domains_are_not_in_the_room_form_but_still_enforced(client):
    add_profile(client)
    room = make_room(client, moderators=LEADERS)
    set_delivery(client, room, {"enabled": True, "materials": ["protocol"], "recipients": {"emails": ["x@outside.test"]}})
    put_settings(client, "mail_policy", allowed_domains="corp.test")
    login(client, "carol")
    pv = client.post(f"/api/v1/rooms/{room['id']}/manage/delivery-preview", json={}).json()
    assert next(r for r in pv["recipients"] if r["email"] == "x@outside.test")["problem"] == "domain_not_allowed"
    _ = MailMessage


# ------------------------------------------------------------------------------------------------ cookie и «Обработка данных»
def test_privacy_texts_are_public_editable_by_admin_and_validated(client):
    client.cookies.clear()
    pub = client.get("/api/v1/public/privacy")
    assert pub.status_code == 200 and "технические cookie" in pub.json()["cookie_text"] and pub.json()["operator"] == ""
    login(client, "carol")
    assert client.put("/api/v1/admin/settings/privacy", json={"operator": "x"}).status_code == 403
    login(client, "root")
    assert client.put("/api/v1/admin/settings/privacy", json={"operator": "ООО Пример", "contact": "ib@example.local", "policy_url": "javascript:alert(1)"}).status_code == 422
    assert client.put("/api/v1/admin/settings/privacy", json={"operator": "ООО Пример", "contact": "ib@example.local", "policy_url": "https://intranet.example.local/p"}).status_code == 200
    client.cookies.clear()
    got = client.get("/api/v1/public/privacy").json()
    assert got["operator"] == "ООО Пример" and got["policy_url"].startswith("https://") and set(got) == {"cookie_text", "operator", "purpose", "data_types", "cookies", "retention", "contact", "policy_url"}
