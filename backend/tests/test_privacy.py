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
    assert run("Адрес сервера 192.0.2.17. Подсеть 192.0.2.0/24, версия 2.14.1.", people=[]) == "Адрес сервера [IP 1]. Подсеть [IP 2], версия 2.14.1.", \
        "адрес в конце предложения тоже заменяется (найдено живой проверкой)"


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
    assert run("Бюджет — 2,4 млн руб., срок — май. Итого 5 руб. Далее.", people=[]) == "Бюджет — [сумма], срок — май. Итого [сумма]. Далее."


def test_a_participant_without_a_first_name_gets_a_number_not_a_bare_initial():
    out = run("Локальный администратор: начинаем. Петров на связи, слово Петрову.", people=["Локальный администратор", "Петров"])
    assert out == "Локальный администратор: начинаем. Участник 1 на связи, слово Участнику 1.", "служебное имя — не человек; фамилия без имени — номер, а не «П.»"
    assert "Okafor" not in run("Ngozi Okafor доложила.", people=["Ngozi Okafor"]) and "Хайруллин" not in run("Хайруллина доложила.", people=["Зульфия Хайруллина"])


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


# ------------------------------------------------- правила, перенесённые из прежнего PHP-обезличивателя (сверка — docs/ANONYMIZATION.md)
@pytest.mark.parametrize("text,gone,kept", [
    # подписанные номера заменяются и с неверной контрольной суммой — опечатка не делает номер открытым
    ("СНИЛС 123-456-789 01\nИНН/КПП 2308123456 / 230801001\nОГРН 1232300004567\nр/с 40702810900000012345\nBIK=040349225",
     ["123-456-789 01", "2308123456", "230801001", "1232300004567", "40702810900000012345", "040349225"], []),
    ("— паспорт?\n— 03 18 654321, выдан 21.04.2018\nпаспорт серии 03 16 номер 445566\nп-т 0314 №991122\nзагранпаспорт 72 9988776\nкод подр 230-017",
     ["03 18 654321", "21.04.2018", "03 16 номер 445566", "0314 №991122", "72 9988776", "230-017"], []),
    ("Дата рожденuя: 05.08.1989.\nдата рождения 17.04.1986 | 17/04/1986 | 17 апреля 1986\nПациентка Петрова Е.А., 07.11.1992, карта №184725.",
     ["05.08.1989", "17.04.1986", "17/04/1986", "17 апреля 1986", "07.11.1992", "184725"], ["Пациентка"]),
    ("Карта МИР 2202 2000 1234 5678; valid thru 10/29; CVC 411; CARDHOLDER ALEXEY ORLOV\norder_id=4111111111111111",
     ["2202 2000 1234 5678", "10/29", "CVC 411", "ALEXEY ORLOV"], ["order_id=4111111111111111"]),
    ('DB_PASSWORD="Prod-Med!8891"\n{"username":"nlebedev","password":"Json!Pass#2026"}\n<password>Xml!Pass#2026</password>\n'
     '<input name="client_secret" value="html_secret_abcdef123456">\nAUTH-003 | пароль | Temp!Auth#003\nusername,password,host\nsvc_csv,Csv!Pass#2026,db\n'
     "login;pass = report_user;Report#7788!\ncurl -u k.zhdanov:Curl!Core#2026 https://api.example.org\nTOTP secret JBSWY3DPEHPK3PXR\n"
     "backup codes 19aa-22bb-33cc,44dd-55ee-66ff\nCookie: remember_me=remember_778899abcdef\nпароль........ Qwerty-Test!995\nbind pwd Ldap#Bind!7788",
     ["Prod-Med!8891", "nlebedev", "Json!Pass#2026", "Xml!Pass#2026", "html_secret_abcdef123456", "Temp!Auth#003", "Csv!Pass#2026", "svc_csv",
      "report_user", "Report#7788!", "Curl!Core#2026", "JBSWY3DPEHPK3PXR", "19aa-22bb-33cc", "44dd-55ee-66ff", "remember_778899abcdef", "Qwerty-Test!995", "Ldap#Bind!7788"],
     ["AUTH-003"]),
    ('ПарольБД = "Test!1C#2026";\nСоединение = Новый HTTPСоединение(\n    "api.example.org",\n    443,\n    "svc_1c",\n    "Http!1C#2026");\n'
     'Параметры = Новый Структура("Логин,Пароль", "exchangeuser", "Params!1C#2026");\n1cestart.exe ENTERPRISE /N"svc_launch" /P"Launch!1C#2026"\n'
     'ЭтоНеПароль = "просто строка без метки";\nПолучитьТокен();\nРелизКонфигурации = "8.3.24.1559";\nВерсия платформы 192.0.2.17\nTOKEN=${TOKEN}\nPASSWORD=%PASSWORD%\nSECRET={{SECRET}}',
     ["Test!1C#2026", "svc_1c", "Http!1C#2026", "exchangeuser", "Params!1C#2026", "svc_launch", "Launch!1C#2026"],
     ['ЭтоНеПароль = "просто строка без метки"', "ПолучитьТокен()", "8.3.24.1559", "Версия платформы 192.0.2.17", "${TOKEN}", "%PASSWORD%", "{{SECRET}}"]),
    ("сервер db-prod-02.corp.example.org\nрабочая станция KRD-WS-0091\nC:\\Users\\kovalev\\Desktop\\passwords.txt\n/home/svc_med/.ssh/id_ed25519\n"
     "S-1-5-21-1234567890-2233445566-998877665-1104\nTG @petrova_ea, почта petrova.elena(at)example.org\nIPv6 2001:db8:85a3::8a2e:370:7334\n"
     "Инцидент INC-2026-001, горячая линия 8-800-555-35-35, пакет @scope/pkg",
     ["db-prod-02", "KRD-WS-0091", "kovalev", "svc_med", "S-1-5-21", "@petrova_ea", "petrova.elena", "2001:db8"],
     ["INC-2026-001", "8-800-555-35-35", "@scope/pkg"]),
    ("Ds: первичная открытоугольная глаукома OD.\nМКБ-10 H40.1.\nАллергоанамнез: пенициллин. ВИЧ — отриц.\nу неё рекуррентное депрессивное расстройство, врач в курсе",
     ["глаукома", "H40.1", "пенициллин", "ВИЧ — отриц", "депрессивное расстройство"], ["врач в курсе"]),
    ("Себестоимость 13 420 000; маржа 31,4%; цена продажи 12 500 000; бонус менеджера 240 000.\nОткрытая цена на сайте 4 900 руб. Скидка по публичной акции 10%.",
     ["13 420 000", "31,4%", "12 500 000", "240 000"], ["4 900 руб.", "акции 10%"]),
    ("Коммерческая тайна. ДСП. Клиенту не передавать.\nДело № 2-1847/2026, Дело № А40-55123/2026\nVIN XTA212140Y1234567, кадастровый номер 23:43:0205017:1122, "
     "государственный регистрационный знак А123ВС123\nПроект MED-CORE-2027 стартует. Ст. 309 ГК РФ.",
     ["Коммерческая тайна", "ДСП", "Клиенту не передавать", "2-1847/2026", "XTA212140Y1234567", "23:43:0205017:1122", "А123ВС123", "MED-CORE-2027"],
     ["А40-55123/2026", "ГК РФ"]),
])
def test_rules_carried_over_from_the_previous_anonymizer(text, gone, kept):
    out = run(text, people=[])
    for g in gone:
        assert g not in out, (g, out)
    for k in kept:
        assert k in out, (k, out)


def test_names_in_latin_and_capitals_and_with_initials_are_the_same_person():
    out = run("IVANOV IVAN PETROVICH подписал. ПЕТРОВА МАРИЯ СЕРГЕЕВНА согласовала. Пациент: Кузнецов Сергей Андреевич.\nИванов И.П. тел. 1234\n"
              "=== CONTROL SUMMARY ===")
    assert out.splitlines()[0] == "Иван И. подписал. Мария П. согласовала. Пациент: Сергей К."
    assert out.splitlines()[1].startswith("Иван И.") and "CONTROL SUMMARY" in out


def test_participant_found_by_the_list_when_ner_misses_name_and_patronymic():
    """«Иван Петрович» в начале строки и после короткой реплики NER пропускал — участник узнаётся по списку в любом порядке и падеже."""
    for prof in ("basic", "strict"):
        out = run("Иван Петрович: да.\nДа. Иван Петрович, начнём. Мария Сергеевна, Петровой передайте. С Кузнецовым и Петровой Марией обсудим.", profile=prof)
        assert not re.search(r"Петрович|Сергеевн|Петров|Кузнецов", out), out
        assert out.count("Мари") == (3 if prof == "basic" else 0)


def test_a_value_found_once_is_replaced_everywhere_and_a_login_from_an_email():
    out = run("пароль: Zx9!kLm2q-77\nпозже повторил в чате Zx9!kLm2q-77\nпочта m.voronova@example.org, в VPN заходит как m.voronova", people=[])
    assert "Zx9!kLm2q-77" not in out and "m.voronova" not in out


def test_a_secret_revealed_by_earlier_replacements_is_masked_not_refused():
    """Коды восстановления — секрет; маркерное правило после первой замены могло увидеть следующую строку как «новый» секрет."""
    out = run("recovery codes 11aa-22bb-33dd,44dd-55ee-66aa\n\nstate=550e8400-e29b-41d4-a716-446655440051 nonce=r4nd0mN0nc3Value7788", people=[])
    assert "11aa-22bb-33dd" not in out and "44dd-55ee-66aa" not in out


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


def _pdf(text: str, image: bool = False) -> bytes:
    """Простой PDF с текстовым слоем (латиница — встроенный шрифт Helvetica без кириллицы)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    if image:                                       # «скан»: страница — изображение без текстового слоя
        content = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
    xobj = b" /XObject << /Im1 6 0 R >>" if image else b""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >>" + xobj + b" >> >>",
            b"<< /Length %d >>stream\n" % len(content) + content + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    if image:
        objs.append(b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>stream\n\x80\nendstream")
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % x for x in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return bytes(out)


def test_scanned_pdf_page_blocks_the_whole_document():
    """Страница-изображение без текста не проверена: результат по документу не выдаётся (отказ), а не «чистый» текст остальных страниц."""
    from app.privacy.extract import ExtractError, extract

    with pytest.raises(ExtractError, match="сканы"):
        extract("scan.pdf", _pdf("", image=True))
    text, info = extract("text.pdf", _pdf("Ivanov Ivan, password=Qw3rty-Pass-2026, phone +7 916 123-45-67"))
    assert "Ivanov" in text and info["pages"] == 1
    import io as _io

    from pypdf import PdfReader, PdfWriter

    w = PdfWriter()
    for src in (_pdf("Ivanov Ivan, phone +7 916 123-45-67"), _pdf("", image=True)):
        w.add_page(PdfReader(_io.BytesIO(src)).pages[0])
    buf = _io.BytesIO()
    w.write(buf)
    with pytest.raises(ExtractError, match=r"сканы\): 2 из 2"):
        extract("mixed.pdf", buf.getvalue())             # текст первой страницы не выдаётся за обезличенный документ


def test_docx_headers_footnotes_are_checked_and_image_only_documents_are_refused():
    import io as _io
    import zipfile as _zip

    from docx import Document

    from app.privacy.extract import ExtractError, extract

    d = Document()
    d.add_paragraph("Основной текст протокола.")
    d.sections[0].header.paragraphs[0].text = "Исполнитель: ivanov@example.org"
    d.sections[0].footer.paragraphs[0].text = "Тел. +7 912 345-67-89"
    buf = _io.BytesIO()
    d.save(buf)
    text, info = extract("a.docx", buf.getvalue())
    assert "ivanov@example.org" in text and "345-67-89" in text and text.startswith("Основной текст") and "note" not in info

    def with_image(body: str) -> bytes:
        d = Document()
        d.add_paragraph(body)
        b = _io.BytesIO()
        d.save(b)
        with _zip.ZipFile(b, "a") as z:
            z.writestr("word/media/image1.png", b"\x89PNG")
        return b.getvalue()
    with pytest.raises(ExtractError, match="изображений"):
        extract("scan.docx", with_image(""))
    text, info = extract("logo.docx", with_image("Договор поставки, ответственный Иванов."))
    assert "Договор" in text and "Изображений в документе: 1" in info["note"]


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
