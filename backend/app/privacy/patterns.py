"""Структурированные персональные и корпоративные данные: контакты, сетевые адреса, учётные записи, реквизиты (с проверкой контрольных сумм),
номера документов и суммы. Правила с контекстом срабатывают только рядом с ключевыми словами, чтобы обычные числа и слова не заменялись."""
from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

from .secrets import Found

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

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}(?![\w-])")
PHONE = re.compile(r"(?<![\w+])(?:\+7|8|\+\d{1,3})[\s(.-]{0,2}\d{3}[\s).-]{0,2}\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)|(?<![\w/.-])\d{3}-\d{2}-\d{2}(?![\w-])")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![\d.]|\.\d)")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}(?:/\d{1,3})?(?![\w:])|(?<![\w:])(?:[0-9a-fA-F]{1,4}:){1,7}:(?:[0-9a-fA-F]{1,4})?(?![\w:])")
MAC = re.compile(r"(?<![\w:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:-])")
URL = re.compile(r"\b(?:https?|ftp|smb|ldaps?|ssh|rdp)://[^\s<>\"'«»]+", re.I)
UNC = re.compile(r"\\\\[A-Za-z0-9._$-]+(?:\\[^\s\\<>\"'«»]+)+")
FQDN = re.compile(r"(?<![\w@./-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?\.)+[A-Za-z][A-Za-z0-9-]{1,62}(?![\w-]|\.[A-Za-z0-9])")
WIN_ACCOUNT = re.compile(r"(?<![\w\\])[A-Za-z][A-Za-z0-9-]{1,15}\\[A-Za-z0-9._-]{2,64}(?![\w\\])")
LOGIN_CTX = re.compile(r"(?i)(?:логин|учётн\w+ запис\w+|учетн\w+ запис\w+|аккаунт|login|username|user(?:name)?)\s*[:=—-]?\s*[«\"']?(?P<v>[A-Za-z][A-Za-z0-9._-]{2,63})")
HOST_CTX = re.compile(r"(?i)(?:сервер\w*|хост\w*|машин\w+|вм|виртуалк\w*|host(?:name)?|server)\s+[«\"']?(?P<v>[A-Za-z][A-Za-z0-9-]*-[A-Za-z0-9-]+|[A-Za-z]+\d+[A-Za-z0-9-]*)")
INN_CTX = re.compile(r"(?i)\bИНН\s*[:№]?\s*(?P<v>\d{10}|\d{12})\b")
INN_BARE = re.compile(r"(?<!\d)(?:\d{10}|\d{12})(?!\d)")
KPP_CTX = re.compile(r"(?i)\bКПП\s*[:№]?\s*(?P<v>\d{9})\b")
OGRN = re.compile(r"(?i)\bОГРН(?:ИП)?\s*[:№]?\s*(?P<v>\d{13}|\d{15})\b")
SNILS = re.compile(r"(?<!\d)\d{3}[- ]?\d{3}[- ]?\d{3}[ -]\d{2}(?!\d)")
BIK = re.compile(r"(?i)\bБИК\s*[:№]?\s*(?P<v>04\d{7})\b")
ACCOUNT = re.compile(r"(?i)(?:р/с|к/с|расч[её]тн\w+ сч[её]т\w*|корр?\.?\s*сч[её]т\w*|сч[её]т\w*\s*№?)\s*[:№]?\s*(?P<v>\d{20})\b")
CARD = re.compile(r"(?<!\d)(?:\d{4}[ -]?){3}\d{1,7}(?!\d)")
PASSPORT = re.compile(r"(?i)паспорт\w*[^\d\n]{0,40}?(?P<v>\d{2}\s?\d{2}\s?(?:№\s*)?\d{6})")
BIRTH = re.compile(r"(?i)(?:дата рождения|д\.\s?р\.|родил(?:ся|ась))\s*[:—-]?\s*(?P<v>\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}\s+[а-я]+\s+\d{4})")
DOC_NUMBER = re.compile(r"(?i)(?:договор\w*|контракт\w*|соглашени\w*|счёт\w*|счет\w*|заявк\w*|инцидент\w*|кп|коммерческ\w+ предложени\w+|заказ\w*|акт\w*)\s*(?:№|N|#|номер)\s*(?P<v>[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9/._-]{0,40})")
MONEY = re.compile(r"(?i)(?<![\w.])(?P<v>\d{1,3}(?:[\s ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(?:(?:тыс(?:яч\w*)?|млн|миллион\w*|млрд|миллиард\w*|k|m)\.?\s*)?(?:руб\w*|₽|р\.|долл\w*|\$|usd|евро|€|eur)(?![\w])"
                   r"|(?:\$|€|₽)\s?(?P<v2>\d[\d\s .,]*\d)")
ADDRESS = re.compile(r"(?:\b\d{6},\s*)?(?:г\.|город)\s*[А-ЯЁ][а-яё-]+,?\s*(?:ул\.|улица|пр-т|проспект|пер\.|переулок|ш\.|шоссе|пл\.|площадь|наб\.|набережная|бульвар|б-р)\s*[А-ЯЁ0-9][^,\n]{1,40},?\s*(?:д\.|дом)\s*\d+[а-яА-Я]?(?:[,\s]*(?:корп\.|к\.|стр\.)\s*\d+)?(?:[,\s]*(?:кв\.|квартира|оф\.|офис)\s*\d+)?"
                     r"|(?:ул\.|улица|пр-т|проспект|пер\.|переулок|ш\.|шоссе)\s*[А-ЯЁ][^,\n]{1,40},\s*(?:д\.|дом)\s*\d+[а-яА-Я]?(?:[,\s]*(?:кв\.|квартира|оф\.|офис)\s*\d+)?", re.I)

LEGAL_ENTITY = re.compile(r"(?<![\w])(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|НКО|ФГУП|ГУП|МУП|ТОО|LLC|Ltd\.?|Inc\.?|GmbH)\s*(?:«[^»\n]{1,80}»|\"[^\"\n]{1,80}\"|'[^'\n]{1,80}')")

PUBLIC_TLDS_OK = {"ru", "com", "org", "net", "рф", "su", "io", "dev", "app", "info", "biz", "gov", "edu", "ai", "me", "tv", "co", "uk", "de", "by", "kz", "ua", "eu", "us", "cloud", "online", "pro", "tech", "site"}
_FILE_EXT = {"txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "json", "xml", "yml", "yaml", "md", "py", "js", "ts", "tsx", "png", "jpg", "jpeg", "gif", "zip",
             "rar", "exe", "msi", "html", "htm", "log", "conf", "cfg", "ini", "sh", "ps1", "bat", "sql", "mp4", "mp3", "wav", "m4a", "vue", "css", "php", "java", "go", "rs", "rb"}


def _host_internal(host: str, internal_domains: Iterable[str]) -> bool:
    h = host.lower().rstrip(".")
    if h.split(".")[-1] in INTERNAL_SUFFIXES:
        return True
    return any(h == d or h.endswith("." + d) for d in internal_domains)


def _v(m: re.Match, name: str = "v") -> tuple[int, int]:
    return (m.start(name), m.end(name)) if name in m.re.groupindex and m.group(name) is not None else (m.start(), m.end())


def find_structured(text: str, *, internal_domains: Iterable[str] = (), public_domains: Iterable[str] = ()) -> list[Found]:
    """Все структурированные находки. Внешние публичные домены (github.com, example.org) не заменяются, если не перечислены как внутренние."""
    internal = [d.lower().strip(".") for d in internal_domains if d]
    public = {d.lower().strip(".") for d in public_domains if d}
    out: list[Found] = []

    def add(s: int, e: int, cat: str, rule: str) -> None:
        while e > s and text[e - 1] in ".,":                 # точка или запятая в конце — знак препинания, а не часть адреса или учётной записи
            e -= 1
        out.append(Found(s, e, cat, rule))

    for m in EMAIL.finditer(text):
        add(m.start(), m.end(), "email", "email")
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
    for m in BIK.finditer(text):
        add(*_v(m), "bank", "bik")
    for m in ACCOUNT.finditer(text):
        add(*_v(m), "bank", "bank_account")
    for m in CARD.finditer(text):
        if luhn_ok(m.group(0)):
            add(m.start(), m.end(), "bank", "card_luhn")
    for m in PASSPORT.finditer(text):
        add(*_v(m), "id_number", "passport")
    for m in BIRTH.finditer(text):
        add(*_v(m), "id_number", "birth_date")
    for m in PHONE.finditer(text):
        if len(_digits(m.group(0))) >= 7:
            add(m.start(), m.end(), "phone", "phone")
    for m in DOC_NUMBER.finditer(text):
        add(*_v(m), "doc_number", "document_number")
    for m in MONEY.finditer(text):
        e = m.end()
        if re.match(r"\.\s*[а-яёa-z]", text[e:e + 4]):
            e += 1                                       # «1,2 млн руб. подпишем» — точка сокращения входит в сумму, а не остаётся «концом предложения»
        out.append(Found(m.start(), e, "money", "money"))
    for m in LEGAL_ENTITY.finditer(text):
        add(m.start(), m.end(), "organization", "legal_entity")
    for m in ADDRESS.finditer(text):
        add(m.start(), m.end(), "address", "address")
    return out
