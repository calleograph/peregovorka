"""Обезличивание на русском (синтетические данные): люди в разных падежах и формах, похожие имена, организации, внутренняя инфраструктура,
контакты, реквизиты, секреты, качество протокола, пропуски и лишние замены, профили, словари и исключения."""
from __future__ import annotations

import re

import pytest

from app.privacy.engine import Anonymizer, Policy, PrivacyError, Term
from app.privacy.patterns import inn_ok, luhn_ok, ogrn_ok, snils_ok
from app.privacy.secrets import find_secrets

PEOPLE = ["Иванов Иван Петрович", "Петрова Мария Сергеевна", "Кузнецов Сергей Андреевич", "Смирнов Сергей Олегович"]


def run(text: str, profile: str = "basic", people=PEOPLE, **kw) -> str:
    return Anonymizer(Policy.from_profile(profile, **kw), people).process(text).text


# ------------------------------------------------------------------------------------------------------------ люди
@pytest.mark.parametrize("phrase,expected", [
    ("Иванов Иван Петрович выступил", "Иван И. выступил"),
    ("слово Ивану Петровичу", "слово Ивану И."),
    ("согласовать с Ивановым", "согласовать с Иваном И."),
    ("письмо от Иванова", "письмо от Ивана И."),
    ("поручено Петровой", "поручено Марии П."),
    ("доклад Марии Сергеевны", "доклад Марии П."),
    ("с Марией Петровой", "с Марией П."),
    ("И. П. Иванов сообщил", "Иван И. сообщил"),
    ("Иванов И. П. сообщил", "Иван И. сообщил"),
])
def test_one_person_in_any_case_and_form_gets_the_same_short_name(phrase, expected):
    assert run(phrase) == expected


def test_participants_list_reads_naturally_and_order_and_links_are_kept():
    out = run("Присутствовали: Иванов Иван Петрович, Петрова Мария Сергеевна, Кузнецов Сергей Андреевич.\n"
              "Иван Петрович предложил перенос. Мария Сергеевна возразила. Кузнецов поддержал Ивана Петровича.")
    assert out.splitlines()[0] == "Присутствовали: Иван И., Мария П., Сергей К."
    assert out.splitlines()[1] == "Иван И. предложил перенос. Мария П. возразила. Сергей К. поддержал Ивана И."


def test_strict_profile_numbers_participants_and_declines_the_word():
    out = run("Иванов предложил. Петровой поручено. Согласовать с Кузнецовым.", "strict")
    assert out == "Участник 1 предложил. Участнику 2 поручено. Согласовать с Участником 3."


def test_two_sergeys_are_not_merged_and_a_bare_ambiguous_first_name_does_not_leak_a_surname():
    out = run("Кузнецов Сергей и Смирнов Сергей обсудили план. Сергей Андреевич за, Смирнов против. Сергей промолчал.")
    assert "Сергей К. и Сергей С." in out and "Сергей К. за, Сергей С. против" in out
    assert "Кузнецов" not in out and "Смирнов" not in out
    assert out.endswith("Сергей промолчал."), "одно имя при двух Сергеях — неоднозначно: имя остаётся, фамилия не появляется"


def test_spouses_with_one_surname_are_different_people():
    out = run("Иванов Пётр и Иванова Анна пришли вместе. Иванову поручено, Ивановой — нет.", people=[])
    assert "Пётр И." in out and "Анна И." in out and "Иванов" not in out


def test_people_outside_the_participant_list_are_also_replaced_consistently():
    out = run("Клиент Соколов Андрей Викторович позвонил. Соколову перезвонят.", people=[])
    assert "Соколов" not in out and out.count("Андрей С.") == 1 and "Андрею С." in out


# --------------------------------------------------------------------------------------------- инфраструктура и контакты
def test_infrastructure_contacts_and_accounts():
    text = ("Сервер db-prod-01.corp.example.local (192.0.2.40, MAC 00:1A:2B:3C:4D:5E) перенесён; ссылка https://wiki.corp.example.local/pages/42, "
            "папка \\\\fs01\\share\\finance. Учётка CORP\\i.ivanov, логин: m.petrova. Почта ivanov@example.org, телефон +7 (912) 345-67-89, доб. 555-12-34.")
    out = run(text, internal_domains=["corp.example.local"])
    for leaked in ("db-prod-01", "192.0.2.40", "00:1A:2B", "wiki.corp", "fs01", "CORP\\i.ivanov", "m.petrova", "ivanov@example.org", "345-67-89", "555-12-34"):
        assert leaked not in out, leaked
    assert "[сервер 1]" in out and "[IP 1]" in out and "[учётная запись 1]" in out and "[учётная запись 2]" in out


def test_the_same_server_gets_the_same_label_across_texts_of_one_meeting():
    a = Anonymizer(Policy.from_profile("basic", internal_domains=["corp.example.local"]), PEOPLE)
    one = a.process("Сервер app01.corp.example.local упал.").text
    two = a.process("Перезапустили app01.corp.example.local и db01.corp.example.local.").text
    assert one == "Сервер [сервер 1] упал." and two == "Перезапустили [сервер 1] и [сервер 2]."


def test_public_sites_and_ordinary_words_are_not_replaced():
    text = "Документация на github.com и docs.python.org. Решили: Вера в успех есть, Надежда тоже. Москва ждёт отчёт. Роман с цифрами окончен."
    out = run(text)
    assert out == text, "общеизвестные сайты, обычные слова с заглавной буквы и города в базовом профиле не трогаются"


# ------------------------------------------------------------------------------------------------- реквизиты и деньги
def test_checksums_of_russian_identifiers():
    assert inn_ok("7707083893") and not inn_ok("7707083894")
    assert inn_ok("500100732259") and not inn_ok("500100732250")
    assert snils_ok("112-233-445 95") and not snils_ok("112-233-445 96")
    assert ogrn_ok("1027700132195") and not ogrn_ok("1027700132196")
    assert luhn_ok("4111 1111 1111 1111") and not luhn_ok("4111 1111 1111 1112")


def test_identifiers_bank_details_documents_and_money():
    text = ("ИНН 7707083893, КПП 773601001, ОГРН 1027700132195, СНИЛС 112-233-445 95, паспорт 45 10 123456, дата рождения 12.03.1985. "
            "Карта 4111 1111 1111 1111, р/с 40702810900000012345, БИК 044525225. Договор № 15/2026-КП на 1 200 000 руб., скидка 12 млн рублей, бюджет $25 000.")
    out = run(text)
    for leaked in ("7707083893", "773601001", "1027700132195", "112-233-445", "123456", "12.03.1985", "4111", "40702810900000012345", "044525225",
                   "15/2026-КП", "1 200 000", "12 млн", "25 000"):
        assert leaked not in out, leaked
    assert "Договор № [номер 1]" in out


def test_ordinary_numbers_dates_and_counts_are_kept():
    text = "Встреча 15 марта, 3 пункта повестки, версия 2.4, 10 участников, срок — 5 дней, в 2026 году."
    assert run(text) == text


# ------------------------------------------------------------------------------------------------------------- секреты
@pytest.mark.parametrize("secret_text,value", [
    ("пароль от стенда: Qw3rty!2026xZ", "Qw3rty!2026xZ"),
    ("Пароль администратора стенда: Qw3rty!2026xZ", "Qw3rty!2026xZ"),
    ("пароль для входа в CRM — Zx9!kLm2pQ", "Zx9!kLm2pQ"),
    ("новый токен доступа: abc123def456ghi789", "abc123def456ghi789"),
    ("password=S3cr3t-Value-77", "S3cr3t-Value-77"),
    ("токен ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("ключ AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlLXRlc3Q", "eyJhbGciOiJIUzI1NiJ9"),
    ("postgresql://app:Pa55word@db.example.org:5432/prod", "Pa55word"),
    ("Server=sql01;Database=crm;User Id=sa;Password=Sup3rSecret;", "Sup3rSecret"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA"),
    ("временный ключ 7f3ac9e2-51db-4c8a-9f1e-a2b4c6d8e0f1", "7f3ac9e2-51db"),
])
def test_secrets_are_always_removed(secret_text, value):
    keep_all = Policy(actions={c: "keep" for c in ("person", "organization", "location", "address", "phone", "email", "ip", "host", "url", "login",
                                                    "id_number", "bank", "doc_number", "money", "project", "custom")}, use_ner=False)
    out = Anonymizer(keep_all).process(f"Напоминаю: {secret_text}. Спасибо.").text
    assert value not in out and "[секрет удалён]" in out, "секреты маскируются даже при профиле «всё оставить»"


def test_a_secret_is_replaced_whole_and_nothing_of_it_is_left():
    out = run("Мария: пароль от стенда Qw3rty!2026, а пароль: x1y2z3w4.", people=[])
    assert "Qw" not in out and "3rty" not in out and "x1y2" not in out and out.count("[секрет удалён]") == 2


def test_words_after_password_are_not_mistaken_for_secrets():
    text = "Пароль не менять, токен — сбросить завтра, ключ доступа выдаст администратор."
    assert find_secrets(text) == []


# --------------------------------------------------------------------------------------------------- организации и словари
def test_organizations_basic_vs_strict_and_dictionary_of_clients_and_projects():
    text = "ООО «Ромашка» и АО «Вектор» обсудили проект «Альфа» с Банком Северный. Альфу запускаем в мае, по Альфе всё готово."
    terms = [Term("Альфа", "project"), Term("Банк Северный", "organization")]
    basic = run(text, terms=terms)
    assert "Ромашка" not in basic and "Вектор" not in basic and "Альф" not in basic and "Северн" not in basic
    assert len(re.findall(r"Проект\w* 1", basic)) == 3 and "по Проекту 1" in basic, "одна и та же внутренняя тема — одно обозначение во всех формах слова"
    assert "Организация 1" in basic and "Организация 2" in basic and "с Организацией 3" in basic


def test_places_organizations_and_coordinated_names_are_declined_and_numbered_by_meaning():
    strict = run("Встреча с ООО «Ромашка», счёт от ООО «Ромашки». Поедем в Нижний Новгород, офис в Нижнем Новгороде, звонили из Казани.", "strict", people=[])
    assert strict == "Встреча с Организацией 1, счёт от Организации 1. Поедем в Место 1, офис в Месте 1, звонили из Места 2."
    assert "с Иваном И. и Марией П." in run("Обсудим с Ивановым и Петровой.")
    assert run("Проект Альфа переносим.", terms=[Term("Альфа", "project")]) == "Проект 1 переносим."
    assert run("Договор на 1,2 млн руб. подпишем.", people=[]) == "Договор на [сумма] подпишем."


def test_custom_replacement_and_exceptions():
    terms = [Term("Атлант", "custom", "внутренняя CRM")]
    out = run("Перенесли Атлант на новый сервер. Атланта хватит до мая. Иванов Иван доволен.", terms=terms, exceptions=["Иванов Иван"], people=[])
    assert out == "Перенесли внутренняя CRM на новый сервер. внутренняя CRM хватит до мая. Иванов Иван доволен."


# --------------------------------------------------------------------------------------------------- протокол целиком
PROTOCOL = """Протокол совещания по переносу CRM
Дата: 14 октября 2026 г.
Присутствовали: Иванов Иван Петрович (руководитель), Петрова Мария Сергеевна, Кузнецов Сергей Андреевич.

1. Иван Петрович доложил о сбоях на сервере crm-app-02.corp.example.local (адрес 192.0.2.17). Предложил перенос на новый кластер.
2. Мария Сергеевна сообщила, что бюджет на перенос — 2,4 млн руб., договор № ДП-2026/118 с ООО «СтройИнвест» подписан.
3. Кузнецов предупредил: пароль администратора стенда P@ssw0rd-Crm-2026 нужно сменить.

Решения:
- Перенести CRM до 1 ноября. Ответственный — Кузнецов С. А.
- Петровой согласовать оплату с бухгалтерией.

Поручения:
- Иванову И. П. — подготовить план работ к пятнице.
- Марии Сергеевне — уведомить ООО «СтройИнвест».
"""


def test_protocol_keeps_structure_decisions_tasks_and_who_is_who():
    out = run(PROTOCOL, internal_domains=["corp.example.local"])
    lines, src = out.splitlines(), PROTOCOL.splitlines()
    assert len(lines) == len(src) and [bool(x.strip()) for x in lines] == [bool(x.strip()) for x in src], "строки и пустые строки на своих местах"
    assert lines[0] == src[0] and lines[1] == src[1] and "Решения:" in lines and "Поручения:" in lines
    assert lines[2] == "Присутствовали: Иван И. (руководитель), Мария П., Сергей К."
    assert lines[4].startswith("1. Иван И. доложил о сбоях на сервере [сервер 1]")
    assert "Ответственный — Сергей К." in out and "- Марии П. согласовать оплату" in out and "- Ивану И. — подготовить план" in out
    assert "- Марии П. — уведомить Организацию 1" in out or "- Марии П. — уведомить Организация 1" in out
    for leaked in ("Иванов", "Петров", "Кузнецов", "crm-app-02", "192.0.2.17", "2,4 млн", "ДП-2026/118", "СтройИнвест", "P@ssw0rd"):
        assert leaked not in out, leaked
    assert len(re.findall(r"Иван И\.|Ивану И\.", out)) == 3 and "Сергей К." in out


def test_verification_fails_closed_when_a_secret_survives(monkeypatch):
    from app.privacy import engine

    monkeypatch.setattr(engine, "find_secrets", lambda t: [] if "ЗАМЕНА" not in t else find_secrets("password=Leaked-Value-123"))
    a = Anonymizer(Policy.from_profile("basic", use_ner=False))
    monkeypatch.setattr(a, "find", lambda t: [])
    with pytest.raises(PrivacyError) as e:
        a.process("ЗАМЕНА password=Leaked-Value-123")
    assert e.value.code == "verification_failed"


# ---------------------------------------------------------------------------------------------------- лаборатория
def _docx(text: str) -> bytes:
    import io as _io

    from docx import Document

    d = Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = _io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    """Простой PDF с текстовым слоем (латиница — встроенный шрифт Helvetica без кириллицы)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>stream\n" % len(content) + content + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % x for x in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return bytes(out)


def test_lab_text_file_formats_draft_settings_limits_and_rights(client):
    from .conftest import login

    login(client, "root")
    r = client.post("/api/v1/admin/privacy/lab", json={"text": PROTOCOL, "profile": "basic", "participants": PEOPLE[:3],
                                                       "draft": {"internal_domains": ["corp.example.local"], "terms": [{"term": "CRM", "category": "project"}]}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert "Иван И." in out["text"] and "Иванов" not in out["text"] and "Проект 1" in out["text"]
    assert out["stats"]["person"] >= 5 and out["stats"]["secret"] == 1 and any(x["rule"] == "keyword_near_value" for x in out["replacements"])
    strict = client.post("/api/v1/admin/privacy/lab", json={"text": PROTOCOL, "profile": "strict", "participants": PEOPLE[:3]}).json()
    assert "Участник 1" in strict["text"] and "Иван И." not in strict["text"]
    docx = client.post("/api/v1/admin/privacy/lab/file", params={"name": "протокол.docx", "profile": "basic"}, content=_docx("Почта: ivanov@example.org\nТелефон +7 912 345-67-89"))
    assert docx.status_code == 200 and docx.json()["info"]["format"] == "DOCX" and "ivanov@" not in docx.json()["text"] and "345-67-89" not in docx.json()["text"]
    pdf = client.post("/api/v1/admin/privacy/lab/file", params={"name": "a.pdf"}, content=_pdf("Mail ivanov@example.org IP 192.0.2.40 password=S3cr3t-Value-77"))
    assert pdf.status_code == 200, pdf.text
    assert pdf.json()["info"]["format"] == "PDF" and all(x not in pdf.json()["text"] for x in ("ivanov@", "192.0.2.40", "S3cr3t"))
    txt = client.post("/api/v1/admin/privacy/lab/file", params={"name": "a.txt"}, content="ИНН 7707083893".encode("cp1251"))
    assert txt.status_code == 200 and "7707083893" not in txt.json()["text"], "текст в cp1251 тоже читается"
    assert client.post("/api/v1/admin/privacy/lab/file", params={"name": "a.exe"}, content=b"MZ").status_code == 422
    assert client.post("/api/v1/admin/privacy/lab/file", params={"name": "a.docx"}, content=b"not a zip").status_code == 422
    assert client.post("/api/v1/admin/privacy/lab", json={"text": PROTOCOL, "draft": {"terms": [{"term": ""}]}}).status_code == 422
    from .conftest import put_settings
    put_settings(client, "anonymizer", lab_max_chars=1000)
    login(client, "root")
    assert client.post("/api/v1/admin/privacy/lab", json={"text": "а" * 1500}).status_code == 413
    audit = client.get("/api/v1/admin/audit?limit=50").json()
    rows = [a for a in audit if a["action"] == "privacy.lab"]
    assert rows and "Иванов" not in str(rows) and "ivanov@" not in str(rows), "в журнале — объём и число замен, без текста"
    cat = client.get("/api/v1/admin/privacy/catalog").json()
    assert cat["nlp"]["ready"] is True and {c["id"] for c in cat["categories"]} >= {"person", "secret", "money"} and "pdf" in str(cat["formats"])
    login(client, "alice")
    assert client.post("/api/v1/admin/privacy/lab", json={"text": "x"}).status_code == 403
    assert client.get("/api/v1/admin/privacy/catalog").status_code == 403
