"""Структурированные персональные и корпоративные данные: контакты, сетевые адреса, учётные записи, реквизиты (с проверкой контрольных сумм),
номера документов и суммы. Правила с контекстом срабатывают только рядом с ключевыми словами, чтобы обычные числа и слова не заменялись."""
from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

from .secrets import Found, credentials, shadow

# ----------------------------------------------------------------------------------------------- контрольные суммы
def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def inn_ok(s: str) -> bool:
    d = [int(c) for c in _digits(s)]
    if len(d) == 10:
        w = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        return sum(a * b for a, b in zip(w, d)) % 11 % 10 == d[9]
    if len(d) == 12:
        w1, w2 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8], [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        return sum(a * b for a, b in zip(w1, d)) % 11 % 10 == d[10] and sum(a * b for a, b in zip(w2, d)) % 11 % 10 == d[11]
    return False


def snils_ok(s: str) -> bool:
    d = _digits(s)
    if len(d) != 11:
        return False
    total = sum(int(c) * (9 - i) for i, c in enumerate(d[:9]))
    check = total if total < 100 else (0 if total in (100, 101) else total % 101 % 100)
    return check == int(d[9:])


def ogrn_ok(s: str) -> bool:
    d = _digits(s)
    if len(d) == 13:
        return int(d[:12]) % 11 % 10 == int(d[12])
    if len(d) == 15:
        return int(d[:14]) % 13 % 10 == int(d[14])
    return False


def luhn_ok(s: str) -> bool:
    d = [int(c) for c in _digits(s)][::-1]
    if not 13 <= len(d) <= 19:
        return False
    return sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2) for i, x in enumerate(d)) % 10 == 0


# ------------------------------------------------------------------------------------------------- шаблоны
INTERNAL_SUFFIXES = ("local", "lan", "corp", "internal", "intra", "intranet", "home", "localdomain", "ad", "office", "priv")
RESERVED_TLDS = ("test", "example", "invalid", "localhost")      # RFC 2606/6761: в реальной сети это внутренние имена
_HOST_ROLES = {"srv", "server", "db", "sql", "mssql", "pg", "ora", "ws", "app", "web", "fs", "dc", "rds", "ts", "vm", "pc", "nb", "arm", "prod", "dev", "stage", "stg",
               "uat", "backup", "bkp", "mail", "exch", "proxy", "gw", "fw", "k8s", "node", "host", "nas", "esx", "hv", "ldap", "crm", "erp", "1c", "redis", "kafka"}

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}(?:@|\s@\s|\s?\(at\)\s?|\s?\[at\]\s?|\s?\{at\}\s?)[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}(?![\w-])")
HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z][A-Za-z0-9_]{3,31}(?![\w@:/(])")          # @scope/pkg, @scope:registry — не ник
CONFIG_PATH = re.compile(r"(?<![\w/~.:])/(?:opt|etc|srv|var|root|data|usr/local)/[^\s<>\"'|,;]+|(?<![\w])[A-Z]:\\[^\s<>\"'|,;]+\.[A-Za-z0-9]{2,5}(?![\w])")
DN_CN = re.compile(r"(?i)\bCN=(?P<v>[^,=\n]{2,64}),\s*(?:OU|DC|CN|O)=")
URL_USER = re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://(?P<v>[^\s/:@]{1,64}):[^\s/@]{1,128}@")
USER_PATH = re.compile(r"(?i)(?<![\w])[A-Z]:\\(?:Users|Documents and Settings)\\[^\s\\]+(?:\\[^\s\\<>\"'|]+)*|(?<![\w/~.])/(?:home|Users)/[^\s/]+(?:/[^\s<>\"'|]+)*")
SID = re.compile(r"\bS-1-5-21(?:-\d{1,10}){3,4}\b")
HOST_TOKEN = re.compile(r"(?<![\w.@/\\-])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){1,5}\$?(?![\w@-]|\.\w)")
PHONE_INTL = re.compile(r"(?<![\w+])\+(?:[1-689]\d{0,2})(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?![\w])")
PHONE = re.compile(r"(?<![\w+])(?:\+7|8|\+\d{1,3})[\s(.-]{0,2}\d{3}[\s).-]{0,2}\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)|(?<![\w/.-])\d{3}-\d{2}-\d{2}(?![\w-])")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?!\d|\.\d)")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?:/\d{1,3})?(?![\w:])")      # проверяется ipaddress: «10:30:00» не адрес
MAC = re.compile(r"(?<![\w:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:-])")
URL = re.compile(r"\b(?:https?|ftp|smb|ldaps?|ssh|rdp)://[^\s<>\"'«»]+", re.I)
UNC = re.compile(r"\\\\[A-Za-z0-9._$-]+(?:\\[^\s\\<>\"'«»]+)+")
FQDN = re.compile(r"(?<![\w@./-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?\.)+[A-Za-z][A-Za-z0-9-]{1,62}(?![\w-]|\.[A-Za-z0-9])")
WIN_ACCOUNT = re.compile(r"(?<![\w\\])[A-Za-z][A-Za-z0-9-]{1,15}\\[A-Za-z0-9._-]{2,64}\$?(?![\w\\])")
LOGIN_CTX = re.compile(r"(?i)(?:логин|учётн\w+ запис\w+|учетн\w+ запис\w+|учётк\w*|учетк\w*|аккаунт|login|username|user(?:name)?|пользовател\w*|service account|VPN(?:\s+login)?)(?:\s+[а-яё]{2,20}){0,2}\s*[:=—-]?\s*[«\"']?(?P<v>[A-Za-z][A-Za-z0-9._$-]{2,63})")
HOST_CTX = re.compile(r"(?i)(?:сервер\w*|хост\w*|машин\w+|вм|виртуалк\w*|host(?:name)?|server|рабоч\w+ станци\w+|\bАРМ|computer(?: account)?|workstation)\s*[:=]?\s+[«\"']?(?P<v>[A-Za-z][A-Za-z0-9-]*-[A-Za-z0-9-]+|[A-Za-z]+\d+[A-Za-z0-9-]*)")
INN_CTX = re.compile(r"(?i)\b(?:ИНН|INN)(?:\s*/\s*КПП)?(?:[^\d\n]{0,25}?|\s*[:№=]?\s*)(?P<v>\d{10}|\d{12})\b(?:\s*/\s*(?P<kpp>\d{9})\b)?")
INN_BARE = re.compile(r"(?<!\d)(?:\d{10}|\d{12})(?!\d)")
KPP_CTX = re.compile(r"(?i)\b(?:КПП|KPP)\s*[:№=]?\s*(?P<v>\d{9})\b")
OGRN = re.compile(r"(?i)\b(?:ОГРН(?:ИП)?|OGRN(?:IP)?)\s*[:№=]?\s*(?P<v>\d{13}|\d{15})\b")
SNILS = re.compile(r"(?<!\d)\d{3}[- ]?\d{3}[- ]?\d{3}[ -]\d{2}(?!\d)")
SNILS_CTX = re.compile(r"(?i)\b(?:СНИЛС|SNILS)\s*[:№=]?\s*(?P<v>\d{3}-?\d{3}-?\d{3}[\s-]?\d{2})(?!\d)")
SNILS_FORMAT = re.compile(r"(?<![\d-])\d{3}-\d{3}-\d{3}[ -]\d{2}(?![\d-])")
BIK = re.compile(r"(?i)\b(?:БИК|BIK)\s*[:№=]?\s*(?P<v>04\d{7})\b")
ACCOUNT = re.compile(r"(?i)(?:р/с|к/с|л/с|кор/сч|корсч[её]т|расч[её]тн\w+ сч[её]т\w*|лицев\w+ сч[её]т\w*|корр?\.?\s*сч[её]т\w*|сч[её]т\w*\s*№?)\s*[:№]?\s*(?P<v>\d{20})\b")
ACCOUNT_FORMAT = re.compile(r"(?<!\d)(?:301|302|303|40[1-8]|42[0-6]|423)\d{17}(?!\d)")         # 20 цифр с балансовым счётом в начале
CARD = re.compile(r"(?<![\w-])(?:\d{4}[ -]?){3}\d{1,7}(?![\w-])")
_TECH_FIELD = re.compile(r"(?i)\b(?!card|pan\b|account|acc\b|iban|bank|rs\b|ks\b)[a-z_]{2,30}\s*=\s*$")          # order_id=4111…, build=3010… — технический номер, а не реквизит
CARD_CTX = re.compile(r"(?i)(?:карт\w*|card|visa|master\s?card|мир|maestro|amex|unionpay)[^\d\n]{0,20}(?P<v>(?:\d{4}[ -]?){3}\d{4})(?!\d)|(?<!\d)(?P<mir>220[0-4](?:[ -]?\d{4}){3})(?!\d)")
CARD_EXP = re.compile(r"(?i)(?:срок(?:\s+действия)?|valid(?:\s+thru|\s+through)?|exp(?:iry|ires)?\.?|действительна до|(?:\d{4}[ -]?){3}\d{4}\s*[,;]?)\s*[:=]?\s*(?P<v>(?:0[1-9]|1[0-2])\s?/\s?\d{2}(?:\d{2})?)(?![\d/])")
CARD_CVC = re.compile(r"(?i)\b(?:CVC2?|CVV2?|CVP2|CID)\s*[:=]?\s*(?P<v>\d{3,4})\b")
CARD_HOLDER = re.compile(r"(?i:cardholder|card\s?holder|name on card|держател\w*(?: карты)?)\s*[:]?\s*(?P<v>[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){1,2})")
PASSPORT_INTL = re.compile(r"(?i)(?:загран\w*(?:\s+паспорт\w*)?|заграничн\w+\s+паспорт\w*)[^\d\n]{0,20}(?P<v>\d{2}\s?№?\s?\d{7})(?!\d)")
PASSPORT = re.compile(r"(?i)(?:паспорт\w*|п-т|серия\s*(?:и|/)\s*номер|серии|серия)[^\d]{0,40}?(?P<v>\d{2}[\s-]?\d{2}[\s-]*(?:№|номер|N)?\s*\d{6})(?!\d)")
PASSPORT_FORMAT = re.compile(r"(?<![\d-])(?:\d{2} \d{2} \d{6}|\d{4} №\s?\d{6}|\d{4} \d{6})(?![\d-])")
ISSUED = re.compile(r"(?i)(?:выдан\w*|дата выдачи)\s*[:]?\s*(?P<v>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})|(?:код\s+подр\w*\.?|к/п)\s*[:]?\s*(?P<v2>\d{3}-?\d{3})(?!\d)")
_DATE = r"\d{1,2}[./-]\d{1,2}[./-](?:\d{4}|\d{2})(?![\d/-]|\.\d)|\d{8}(?!\d)|\d{1,2}\s+[а-я]+\s+\d{4}"
BIRTH = re.compile(rf"(?i)(?:дата\s+рожд\w*\.?|д\.\s?р\.|г\.\s?р\.|DOB|date of birth|birth\s?date|родил(?:ся|ась))[^\d\n]{{0,25}}\n?[^\d\n]{{0,6}}(?P<v>{_DATE})"
                   r"|(?P<v2>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:г\.\s?р\.|года рождения)"
                   r"|(?i:ДатаРождения|BirthDate|DateOfBirth|birth_date)\s*[=:]\s*(?:Дата|Date)\s*\(\s*(?P<v3>\d{4}\s*,\s*\d{1,2}\s*,\s*\d{1,2})")
MEDICAL_ID = re.compile(r"(?i)(?:(?:полис\w*\s*|номер\s+)?(?:ОМС|ДМС))\s*[:№]?\s*(?P<v>\d{4}\s?\d{4}\s?\d{4}\s?\d{4}(?!\d)|(?=[\w-]*\d)[A-ZА-ЯЁ0-9][\w-]{5,30})"
                        r"|(?:вод\.?\s*уд\.?|водительск\w+\s+удостоверени\w+|\bВУ\b)\s*[:№]?\s*(?P<v2>\d{2}\s?\d{2}\s?\d{6})(?!\d)"
                        r"|(?:мед\.?\s*карт\w*|медкарт\w*|амбулаторн\w+\s+карт\w*|карт\w*|истори\w+\s+болезни)(?:\s+[а-яё]{2,20}){0,2}\s*№\s*(?P<v3>(?=[\w/-]*\d)[A-ZА-ЯЁ0-9][\w/-]{2,30})"
                        r"|(?:IMEI|IMSI|ICCID|MSISDN)(?:\s+[а-яё]{2,20}){0,2}\s*[:=]?\s*(?P<v4>\d{14,20})(?!\d)|\bVIN\s*[:=]?\s*(?P<v5>[A-HJ-NPR-Z0-9]{17})\b|(?:гос\.?\s*номер\w*|г/н|госномер\w*|регистрационн\w+\s+знак\w*)\s*[:]?\s*(?P<v7>[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3})(?!\w)|(?<![\d:])(?P<v6>\d{2}:\d{2}:\d{6,7}:\d{1,6})(?![\d:])")
HEALTH = re.compile(r"(?i)(?:\bDs\.?\s*:|(?:основн\w*\s+|сопутствующ\w*\s+)?диагноз\w*(?:\s+(?:основн\w*|сопутствующ\w*|клиническ\w*|заключительн\w*))?\s*[:—-]|сопутствующ\w*(?:\s+диагноз\w*)?\s*:|\bСоп\.\s*:|"
                    r"анамнез\w*\s*:|аллерг\w*(?:\s+анамнез)?\s*:|аллергоанамнез\s*:|жалоб\w*\s*:|инвалидност\w*\s*[:—-]?|заболевани\w*\s*:|назначен\w*\s*:|лечени\w*\s*:|терапи\w*\s*:)\s*(?P<v>(?:[^\n.;]|\.(?!\s|$)){2,160})"
                    r"|(?:\bМКБ(?:-?10)?|диагноз\w*(?:\s+по\s+МКБ(?:-?10)?)?|\bDs\.?)\s*[:]?\s*(?P<v2>[A-TV-Z]\d{2}(?:\.\d{1,2})?)\b"
                    r"|(?P<v3>\b(?:ВГД|IOP|Vis|Visus)\b[^\n]{0,40}?\b(?:OD|OS|OU)\b[^\n.]{0,40}(?:\.\d+[^\n.]{0,20})*)"
                    r"|(?P<v4>\b(?:ВИЧ|HIV|HBsAg|HCV|анти-HCV|RW|ПЦР)\b\s*[—:-]?\s*(?:отриц\w*\.?|полож\w*\.?|не обнаружен\w*|обнаружен\w*))"
                    r"|(?P<v6>(?:[а-яё]+(?:ая|ое|ый|ий|ой|ые|ых|ую|ого|ной)\s+){0,3}(?:расстройств|глауком|диабет|шизофрени|депресси|гипертензи|гипертоническ\w+\s+болезн|онкологи|опухол|ВИЧ-инфекци|гепатит|туберкул[её]з|эпилепси|инсульт|инфаркт|катаракт|астм|аутизм|деменци|биполярн\w+\s+расстройств)\w*"
                    r"(?:\s+(?:\d\s+(?:типа|ст\.?|степени)|обоих глаз|правого глаза|левого глаза|[IV]{1,3}\s+(?:стадии|степени|ст\.)))?)"
                    r"|(?P<v7>\b[A-Z][A-Z0-9]{2,6}\s+c\.\d+[\w>]+)")
MARKER = re.compile(r"(?i)\b(?:коммерческая тайна|для служебного пользования|для внутреннего использования|клиенту не передавать|конфиденциально|строго конфиденциально|\bДСП\b|confidential|internal use only|strictly confidential)(?![\w])")
PROJECT_CODE = re.compile(r"(?i)\bпроект\w*\s+(?P<v>(?-i:[A-ZА-ЯЁ][A-ZА-ЯЁ0-9]+(?:-[A-ZА-ЯЁ0-9]+)+))")
NUMBER_LABEL = re.compile(r"(?i)\b(?:(?:серийн\w+\s+|регистрационн\w+\s+)?номер(?=\s+(?:заявлени|выписк|зач[её]тн|диплом|сертификат|полис|удостоверени|свидетельств|договор|кадастр|лицев)|\s*:)|штрихкод|штрих-код)(?:\s+[а-яё]{2,20}){0,2}\s*:\s*№?\s*(?P<v>(?=[\w/ -]*\d)[A-ZА-ЯЁ0-9][\w/-]{2,30}(?:\s\d{4,10})?)")
LABELED_PERSON = re.compile(r"(?:\.|\b)(?:ФИО|FullName|ПолноеИмя)\s*=\s*\"(?P<v>[А-ЯЁA-Z][^\"\n]{2,80})\""
                            r"|(?i:\b(?:ФИО|Ф\.И\.О\.|фамилия,?\s*имя,?\s*отчество|пациент\w*|абонент\w*|правообладател\w*|заявител\w*|клиент\w*|сотрудник\w*|кандидат\w*|студент\w*|"
                            r"истец|истца|ответчик\w*|доверител\w*|получател\w*|плательщик\w*|from|to|cc|от|кому))\s*:?[ \t]*\n?[ \t]*"
                            r"(?P<v2>[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)(?![\w])")
ONEC_USER = re.compile(r"(?i)\s/N\s*\"(?P<v>[^\"\n]{1,64})\"")
_NUM = r"\d{1,3}(?:[\s\u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*(?:тыс|млн|млрд)\.?"
MONEY_CTX = re.compile(rf"(?i)(?:себестоимост\w*|прибыл\w*|цен[аеуы]\w*|оклад\w*|преми[яиюей]\w*|компенсаци\w*|доход\w*|зарплат\w*|заработн\w+\s+плат\w*|бюджет\w*|границ\w*|"
                       rf"стоимост\w*|выручк\w*|бонус\w*|выплат\w*|ставк\w*|вилк\w*)(?:\s+[а-яё]{{1,15}}){{0,2}}(?:\s+20\d{{2}})?\s*[:—-]?\s*(?P<v>{_NUM})(?![\d%])")
PERCENT_CTX = re.compile(r"(?i)(?:марж\w*|маржинальн\w*|скидк\w*|комисси\w*|рентабельн\w*|вероятност\w*|наценк\w*|бонус\w*|откат\w*)[^\d\n]{0,25}?(?P<v>\d{1,3}(?:[.,]\d+)?\s?%)")
_PUBLIC_PRICE = re.compile(r"(?i)публичн\w*|открыт\w+\s+цен\w*|на сайте|прайс\w*|розничн\w+\s+цен\w*")
_VERSION_BEFORE = re.compile(r"(?i)(?:релиз|верси|version|release|build|сборк|платформ)\w*\s*[:=]?\s*[\"'«]?\s*$")
DOC_NUMBER = re.compile(r"(?i)(?:договор\w*|контракт\w*|соглашени\w*|счёт\w*|счет\w*|заявк\w*|заявлени\w*|инцидент\w*|кп|коммерческ\w+ предложени\w+|заказ\w*|акт\w*|дел[оау]\w*|"
                        r"материал\w*(?:\s+проверки)?|реестр\w*(?:\s+за)?|выписк\w*|диплом\w*|обращени\w*|сертификат\w*)\s*(?:№|N|#|номер)\s*(?P<v>[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9/._-]{0,40})")
MONEY = re.compile(r"(?i)(?<![\w.])(?P<v>\d{1,3}(?:[\s ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(?:(?:тыс(?:яч\w*)?|млн|миллион\w*|млрд|миллиард\w*|k|m)\.?\s*)?(?:руб\w*|₽|р\.|долл\w*|\$|usd|евро|€|eur)(?![\w])"
                   r"|(?:\$|€|₽)\s?(?P<v2>\d[\d\s .,]*\d)")
ADDRESS_INDEX = re.compile(r"(?<!\d)\d{6},?\s+[А-ЯЁ][а-яё-]+[^\n;|]{3,80}?(?:кв\.?|квартира|оф\.?|офис|д\.)\s*\d+")
ADDRESS_CTX = re.compile(r"(?i)\b(?:адрес\w*(?:\s+(?:проживания|регистрации|доставки|прописки))?|доставк\w*|прожива\w+\s+по\s+адресу|зарегистрирован\w*\s+по\s+адресу|место\s+рождения)\s*[:—-]\s*(?P<v>[^\n;]{4,120}?)(?=\s*(?:\n|;|$|\.\s|\s—\s))")
ADDRESS = re.compile(r"(?:\b\d{6},\s*)?(?:г\.|город)\s*[А-ЯЁ][а-яё-]+,?\s*(?:ул\.|улица|пр-т|проспект|пер\.|переулок|ш\.|шоссе|пл\.|площадь|наб\.|набережная|бульвар|б-р)\s*[А-ЯЁ0-9][^,\n]{1,40},?\s*(?:д\.|дом)\s*\d+[а-яА-Я]?(?:[,\s]*(?:корп\.|к\.|стр\.)\s*\d+)?(?:[,\s]*(?:кв\.|квартира|оф\.|офис)\s*\d+)?"
                     r"|(?<![\w-])(?:ул\.?|улица|пр-т|проспект|пер\.?|переулок|ш\.|шоссе|бульвар|б-р|наб\.)\s*[А-ЯЁ][^,\n]{1,40},\s*(?:д\.\s*|дом\s*)?\d+(?:/\d+)?[а-яА-Я]?(?:[,\s]*(?:корп\.|к\.|стр\.)\s*\d+)?(?:[,\s]*(?:кв\.?|квартира|оф\.|офис)\s*\d+)?"
                     r"|\b[А-ЯЁ][а-яё-]+,\s*[А-ЯЁ][а-яё-]+\s+\d+[а-я]?(?:/\d+)?,\s*(?:кв\.?|квартира|оф\.|офис)\s*\d+", re.I)

LEGAL_ENTITY = re.compile(r"(?<![\w])(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|НКО|ФГУП|ГУП|МУП|ТОО|LLC|Ltd\.?|Inc\.?|GmbH)\s*(?:«[^»\n]{1,80}»|\"[^\"\n]{1,80}\"|'[^'\n]{1,80}')")

PUBLIC_TLDS_OK = {"ru", "com", "org", "net", "рф", "su", "io", "dev", "app", "info", "biz", "gov", "edu", "ai", "me", "tv", "co", "uk", "de", "by", "kz", "ua", "eu", "us", "cloud", "online", "pro", "tech", "site"}
_FILE_EXT = {"txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "json", "xml", "yml", "yaml", "md", "py", "js", "ts", "tsx", "png", "jpg", "jpeg", "gif", "zip",
             "rar", "exe", "msi", "html", "htm", "log", "conf", "cfg", "ini", "sh", "ps1", "bat", "sql", "mp4", "mp3", "wav", "m4a", "vue", "css", "php", "java", "go", "rs", "rb"}


def _host_internal(host: str, internal_domains: Iterable[str]) -> bool:
    h = host.lower().rstrip(".")
    labels = h.split(".")
    if labels[-1] in INTERNAL_SUFFIXES or labels[-1] in RESERVED_TLDS or (len(labels) > 2 and any(x in INTERNAL_SUFFIXES for x in labels[1:-1])):
        return True                                     # db.corp.example.org, portal.int.example.test
    return any(h == d or h.endswith("." + d) for d in internal_domains)


def _v(m: re.Match, name: str = "v") -> tuple[int, int]:
    return (m.start(name), m.end(name)) if name in m.re.groupindex and m.group(name) is not None else (m.start(), m.end())


def find_structured(text: str, *, internal_domains: Iterable[str] = (), public_domains: Iterable[str] = ()) -> list[Found]:
    """Все структурированные находки. Внешние публичные домены (github.com, example.org) не заменяются, если не перечислены как внутренние."""
    text = shadow(text)
    internal = [d.lower().strip(".") for d in internal_domains if d]
    public = {d.lower().strip(".") for d in public_domains if d}
    out: list[Found] = []

    def add(s: int, e: int, cat: str, rule: str) -> None:
        while e > s and text[e - 1] in ".,":                 # точка или запятая в конце — знак препинания, а не часть адреса или учётной записи
            e -= 1
        out.append(Found(s, e, cat, rule))

    for m in EMAIL.finditer(text):
        add(m.start(), m.end(), "email", "email")
    for f in credentials(text):
        if f.category == "login":
            add(f.start, f.end, "login", f.rule)
    for m in HANDLE.finditer(text):
        add(m.start(), m.end(), "login", "messenger_handle")
    for m in USER_PATH.finditer(text):
        add(m.start(), m.end(), "host", "user_path")
    for m in CONFIG_PATH.finditer(text):
        add(m.start(), m.end(), "host", "config_path")
    for rx, rule in ((DN_CN, "directory_cn"), (URL_USER, "url_user")):
        for m in rx.finditer(text):
            add(*_v(m), "login", rule)
    for m in SID.finditer(text):
        add(m.start(), m.end(), "login", "windows_sid")
    for m in ONEC_USER.finditer(text):
        add(*_v(m), "login", "onec_launch_user")
    for m in HOST_TOKEN.finditer(text):
        segs = m.group(0).rstrip("$").lower().split("-")
        if any(c.isdigit() for c in m.group(0)) and any(x in _HOST_ROLES or re.fullmatch(r"(?:srv|db|sql|ws|app|web|fs|dc|vm|pc|nb|arm)\d+", x) for x in segs):
            add(m.start(), m.end(), "host", "host_name")
    for m in URL.finditer(text):
        u = m.group(0).rstrip(".,;:)")
        host = re.sub(r"^[a-z]+://(?:[^@/]*@)?", "", u, flags=re.I).split("/")[0].split(":")[0]
        try:
            ip = ipaddress.ip_address(host)
            internal_ip = ip.is_private or ip.is_loopback
        except ValueError:
            internal_ip = False
        if internal_ip or _host_internal(host, internal) or ("@" in u.split("/")[2] if u.count("/") >= 2 else False) or re.search(r"(?i)[?&](?:token|key|secret|password|sig|signature)=", u):
            add(m.start(), m.start() + len(u), "url", "internal_url")
    for m in UNC.finditer(text):
        add(m.start(), m.end(), "host", "unc_path")
    for m in IPV4.finditer(text):
        raw = m.group(0).split("/")[0]
        if _VERSION_BEFORE.search(text[max(0, m.start() - 30):m.start()]):
            continue                                     # РелизКонфигурации = "8.3.24.1559", version 1.2.3.4 — номер версии, а не адрес
        try:
            ipaddress.IPv4Address(raw)
        except ValueError:
            continue
        if raw.count(".") == 3 and not re.match(r"^\d+\.\d+\.\d+\.\d+$", raw):
            continue
        add(m.start(), m.end(), "ip", "ipv4")
    for m in IPV6.finditer(text):
        try:
            ipaddress.IPv6Address(m.group(0).split("/")[0])
        except ValueError:
            continue
        add(m.start(), m.end(), "ip", "ipv6")
    for m in MAC.finditer(text):
        add(m.start(), m.end(), "ip", "mac")
    for m in FQDN.finditer(text):
        host = m.group(0)
        tld = host.rsplit(".", 1)[-1].lower()
        if tld in _FILE_EXT or host.lower() in public:
            continue
        if _host_internal(host, internal):
            add(m.start(), m.end(), "host", "internal_host")
        elif tld not in PUBLIC_TLDS_OK and not re.fullmatch(r"[a-z]{2,24}", tld):
            add(m.start(), m.end(), "host", "host")
    for m in WIN_ACCOUNT.finditer(text):
        add(m.start(), m.end(), "login", "domain_account")
    for m in LOGIN_CTX.finditer(text):
        add(*_v(m), "login", "login_context")
    for m in HOST_CTX.finditer(text):
        add(*_v(m), "host", "host_context")
    for m in INN_CTX.finditer(text):
        add(*_v(m), "id_number", "inn" if inn_ok(m.group("v")) else "inn_context")
        if m.group("kpp"):
            add(m.start("kpp"), m.end("kpp"), "id_number", "kpp")
    for m in INN_BARE.finditer(text):
        if inn_ok(m.group(0)) and not re.match(r"(?:19|20)\d{2}", m.group(0)):
            add(m.start(), m.end(), "id_number", "inn_checksum")
    for m in KPP_CTX.finditer(text):
        add(*_v(m), "id_number", "kpp")
    for m in OGRN.finditer(text):
        add(*_v(m), "id_number", "ogrn")
    for m in SNILS.finditer(text):
        if snils_ok(m.group(0)):
            add(m.start(), m.end(), "id_number", "snils")
    for m in SNILS_CTX.finditer(text):                   # подписанный номер заменяется и с неверной контрольной суммой: опечатка не делает его открытым
        add(*_v(m), "id_number", "snils_context")
    for m in SNILS_FORMAT.finditer(text):
        add(m.start(), m.end(), "id_number", "snils_format")
    for m in BIK.finditer(text):
        add(*_v(m), "bank", "bik")
    for m in ACCOUNT.finditer(text):
        add(*_v(m), "bank", "bank_account")
    for m in ACCOUNT_FORMAT.finditer(text):
        if not _TECH_FIELD.search(text[max(0, m.start() - 32):m.start()]):
            add(m.start(), m.end(), "bank", "bank_account_format")
    for m in CARD.finditer(text):
        if luhn_ok(m.group(0)) and not _TECH_FIELD.search(text[max(0, m.start() - 32):m.start()]):
            add(m.start(), m.end(), "bank", "card_luhn")
    for rx, rule in ((CARD_CTX, "card_context"), (CARD_EXP, "card_expiry"), (CARD_CVC, "card_cvc"), (CARD_HOLDER, "card_holder")):
        for m in rx.finditer(text):
            for g in ("v", "mir"):
                if g in rx.groupindex and m.group(g) and not (g == "mir" and _TECH_FIELD.search(text[max(0, m.start() - 32):m.start()])):
                    add(m.start(g), m.end(g), "bank", rule)
    for m in PASSPORT.finditer(text):
        add(*_v(m), "id_number", "passport")
    for m in PASSPORT_INTL.finditer(text):
        add(*_v(m), "id_number", "passport_intl")
    for m in PASSPORT_FORMAT.finditer(text):
        add(m.start(), m.end(), "id_number", "passport_format")
    for rx, rule in ((ISSUED, "issue_details"), (BIRTH, "birth_date"), (MEDICAL_ID, "document_id")):
        for m in rx.finditer(text):
            for g in rx.groupindex:
                if m.group(g):
                    add(m.start(g), m.end(g), "id_number", rule)
    for m in HEALTH.finditer(text):
        g = next(g for g in HEALTH.groupindex if m.group(g))
        add(m.start(g), m.start(g) + len(m.group(g).rstrip(" ,")), "health", "health")
    for m in LABELED_PERSON.finditer(text):
        g = "v" if m.group("v") else "v2"
        add(m.start(g), m.end(g), "person", "labeled_person")
    for m in PHONE_INTL.finditer(text):
        if 10 <= len(_digits(m.group(0))) <= 15 and not m.group(0).startswith("+7"):
            add(m.start(), m.end(), "phone", "phone_intl")
    for m in PHONE.finditer(text):
        if len(_digits(m.group(0))) >= 7 and not re.match(r"[78]800", _digits(m.group(0))):      # 8-800 — открытый номер организации
            add(m.start(), m.end(), "phone", "phone")
    for m in DOC_NUMBER.finditer(text):
        if re.fullmatch(r"[АA]\d{2}-\d+/\d{4}", m.group("v")):
            continue                                     # номер арбитражного дела (А40-55123/2026) — открытые сведения картотеки суда
        add(*_v(m), "doc_number", "document_number")
    for rx in (MONEY_CTX, PERCENT_CTX):
        for m in rx.finditer(text):
            if not _PUBLIC_PRICE.search(text[max(0, m.start() - 60):m.start("v")]):
                add(*_v(m), "money", "commercial_figure")
    for m in MARKER.finditer(text):
        add(m.start(), m.end(), "marker", "confidentiality_marker")
    for m in PROJECT_CODE.finditer(text):
        add(*_v(m), "project", "project_code")
    for m in NUMBER_LABEL.finditer(text):
        if not re.search(r"(?i)табельн\w*\s+$", text[max(0, m.start() - 15):m.start()]):        # табельный номер — служебный, как номер заявки
            add(*_v(m), "doc_number", "labeled_number")
    for m in MONEY.finditer(text):
        if _PUBLIC_PRICE.search(text[max(0, m.start() - 60):m.start()]):
            continue                                     # «открытая цена на сайте 4 900 руб.» — публичные сведения
        e = m.end()
        if re.match(r"\.(?:\s*[а-яёa-z]|[,;:)\]])", text[e:e + 4]):
            e += 1                                       # «1,2 млн руб. подпишем» — точка сокращения входит в сумму, а не остаётся «концом предложения»
        out.append(Found(m.start(), e, "money", "money"))
    for m in LEGAL_ENTITY.finditer(text):
        add(m.start(), m.end(), "organization", "legal_entity")
    for m in ADDRESS.finditer(text):
        add(m.start(), m.end(), "address", "address")
    for m in ADDRESS_INDEX.finditer(text):
        add(m.start(), m.end(), "address", "address_index")
    for m in ADDRESS_CTX.finditer(text):
        add(*_v(m), "address", "address_context")
    return out
