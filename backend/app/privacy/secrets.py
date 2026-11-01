"""Обязательное обнаружение секретов перед отправкой текста во внешнюю языковую модель.

Три слоя (как в каталогах gitleaks и GitHub Secret Scanning, шаблоны составлены по открытым форматам провайдеров):
  1. точный формат — известный префикс или структура (ключи облаков, токены сервисов, JWT, закрытые ключи, строки подключения);
  2. контекст — ключевое слово перед значением (password=, «пароль:», api_key:, Authorization: Bearer …);
  3. энтропия — случайная на вид строка только рядом с ключевым словом или маркерной фразой («временный ключ»).
Обнаружение не зависит от профиля обезличивания и от настроек комнаты: секреты маскируются всегда.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Found:
    start: int
    end: int
    category: str          # secret | ... (у секретов всегда secret)
    rule: str              # идентификатор правила — для отчёта лаборатории и журнала (без самого значения)


# --- 1. точные форматы: (идентификатор, шаблон). Группа «v», если есть, — что именно скрыть; иначе — всё совпадение.
EXACT: list[tuple[str, str]] = [
    ("private_key", r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----|\Z)"),
    ("aws_access_key", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b"),
    ("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"),
    ("gitlab_token", r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    ("slack_token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    ("slack_webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+"),
    ("telegram_bot_token", r"\b\d{8,10}:[A-Za-z0-9_-]{35,}"),
    ("stripe_key", r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9_]{16,}"),
    ("huggingface_token", r"\bhf_[A-Za-z0-9]{20,}\b"),
    ("gitlab_runner_token", r"\bgl(?:rt|cbt|dt|ptt|ft|soat)-[A-Za-z0-9_-]{10,}"),
    ("ssh_public_key", r"\b(?:ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com)\s+(?P<v>AAAA[A-Za-z0-9+/=]{16,})"),
    ("vault_token", r"\bhv[sbr]\.[A-Za-z0-9_-]{20,}"),
    ("discord_webhook", r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
    ("azure_account_key", r"(?i)\b(?:AccountKey|SharedAccessKey|sig)=(?P<v>[A-Za-z0-9+/=%]{20,})"),
    ("powershell_securestring", r"(?i)ConvertTo-SecureString\s+[\"'](?P<v>[^\"'\n]{1,128})[\"']\s+-AsPlainText"),
    ("onec_launch_password", r"(?i)\s/P\s*\"(?P<v>[^\"\n]{1,128})\""),
    ("mfa_seed", r"(?i:\b(?:TOTP|HOTP|OTP|MFA|2FA)\b)\W{0,3}(?i:secret|seed|key|ключ|секрет)?\W{0,3}(?P<v>[A-Z2-7]{16,64})(?![A-Za-z0-9])"),
    ("recovery_codes", r"(?i)(?:recovery|backup|резервн\w*|запасн\w*)\s+(?:codes?|код\w*)\s*[:=—-]?\s*(?P<v>[A-Za-z0-9]{4,5}(?:-[A-Za-z0-9]{4,5}){1,3}(?:\s*[,;]\s*[A-Za-z0-9]{4,5}(?:-[A-Za-z0-9]{4,5}){1,3})*)"),
    ("cookie_header", r"(?i)\b(?:Set-)?Cookie\s*:\s*[\w.-]+=(?P<v>[^;\s]{8,})"),
    ("stripe_webhook", r"\bwhsec_[A-Za-z0-9]{20,}\b"),
    ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("openai_key", r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}\b"),
    ("google_api_key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("google_oauth_secret", r"\bGOCSPX-[A-Za-z0-9_-]{20,}\b"),
    ("yandex_token", r"\b(?:t1\.[A-Za-z0-9_-]{40,}|y[0-3]_[A-Za-z0-9_-]{40,}|AQAAAAA[A-Za-z0-9_-]{30,})\b"),
    ("digitalocean_token", r"\bdo[por]_v1_[a-f0-9]{64}\b"),
    ("npm_token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("docker_pat", r"\bdckr_pat_[A-Za-z0-9_-]{20,}\b"),
    ("sendgrid_key", r"\bSG\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ("twilio_sid", r"\bAC[a-f0-9]{32}\b"),
    ("mailgun_key", r"\bkey-[a-f0-9]{32}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ("url_credentials", r"\b[a-z][a-z0-9+.-]{1,20}://[^\s/:@]{0,64}:(?P<v>[^\s/@]{1,128})@[^\s]+"),
    ("curl_user", r"(?:curl|wget)\b[^\n]*?\s(?:-u|--user)\s+[\"']?[^\s:\"']+:(?P<v>[^\s\"']+)"),
    ("provider_token", r"(?i)\b(?:twilio|jenkins|sendgrid|hugging\s?face|gitlab(?:\s+runner)?|azure(?:\s+devops)?(?:\s+pat)?|github|slack|stripe|openai|anthropic|vault|telegram(?:\s+bot)?|discord|npm|docker(?:\s+hub)?|yandex|cloudflare|mailgun|pat)"
                       r"(?:\s+(?:api\s+)?(?:token|key|pat|секрет|ключ|токен))?\s*[:=—-]?\s*(?P<v>(?=[\w.-]*[\d_])(?=[\w.-]*[A-Za-z])[A-Za-z0-9][\w.-]{11,})"),
    ("db_conn_password", r"(?i)\b(?:password|pwd)\s*=\s*(?P<v>[^;\s'\"]{1,128})(?=;|\s|$|['\"])"),
    ("basic_auth", r"(?i)\bAuthorization\s*:\s*Basic\s+(?P<v>[A-Za-z0-9+/=]{8,})"),
    ("bearer", r"(?i)\b(?:Authorization\s*:\s*)?Bearer\s+(?P<v>[A-Za-z0-9._~+/=-]{16,})"),
    ("otpauth", r"otpauth://[^\s]*secret=(?P<v>[A-Z2-7]{16,64})"),
    ("session_cookie", r"(?i)\b(?:PHPSESSID|JSESSIONID|connect\.sid|session_?id|[a-z0-9_]+_session)\s*[=:]\s*(?P<v>[A-Za-z0-9%._-]{16,})"),
]

# --- 2. контекст: ключевое слово, разделитель, значение (без пробелов; кавычки — снимаются)
_KEYS_EN = r"pass(?:word|wd|phrase)?|pwd|secret|client_secret|api[_-]?key|apikey|access[_-]?key|secret[_-]?key|private[_-]?key|token|auth[_-]?token|access[_-]?token|refresh[_-]?token|bind_?password|webhook[_-]?secret|signing[_-]?key|encryption[_-]?key"
_KEYS_RU = r"парол[ья]|пароль от [^\s:=]{1,30}|секрет(?:ный ключ)?|токен|ключ(?: доступа| api| апи)?|код доступа"
CONTEXT = re.compile(
    rf"(?i)(?<![\w-])(?:{_KEYS_EN}|{_KEYS_RU})(?:\s*\([^)]{{0,30}}\))?\s*(?:[:=]|=>|—|-|\s(?:это|is))\s*[\"'«]?(?P<v>[^\s\"'»,;]{{4,200}})")
# заглушки из документации и шаблонов (<token>, ${TOKEN}, %PASSWORD%, {{SECRET}}, YOUR_KEY, example-secret, ***) — не секрет
_PLACEHOLDER = re.compile(r"(?i)^(?:<[^<>]{1,60}>|\$\{[\w.-]{1,60}\}|%[\w]{1,60}%|\{\{\s*[\w.]{1,60}\s*\}\}|your[_-][\w-]*|example[_-][\w-]*|changeme|\.{2,}|…|x{3,}|\*{3,})$")
_STOP_VALUES = {"не", "нет", "none", "null", "true", "false", "отсутствует", "пустой", "скрыт", "изменён", "изменен", "сменить", "указан", "задан", "был",
                "будет", "у", "от", "для", "на", "в", "по", "из", "такой", "этот", "тот", "он", "она", "надо", "нужно", "нужен", "нужна", "сбросить", "менять"}

# --- 3. энтропия: случайная строка рядом с маркерной фразой
# ключевое слово, до четырёх слов, затем значение, похожее на пароль (буквы и цифры или спецсимволы): «пароль администратора стенда P@ssw0rd-1»
NEAR = re.compile(r"(?i)(?<![\w-])(?:парол[ьяеюи]\w*|password|passwd|pwd|passphrase|pass|секрет\w*|secret|токен\w*|token|api[\s_-]?key)(?:\s+[а-яёa-z]{1,20}\b){0,5}(?:\s*\([^)\n]{0,30}\))?\s*[.…]*\s*[:=—-]?\s*[«\"']?(?<![^\s:=—«\"'-])(?P<v>(?=[^\s\"'»]*\d)(?=[^\s\"'»]*[A-Za-zА-Яа-яЁё])[^\s\"'»,;]{6,128})")
_MARKER = re.compile(r"(?i)(?:временн\w+ ключ|одноразов\w+ (?:ключ|код|пароль)|diagnostic key|one-?time|резервн\w+ код|recovery code|backup code)")
_TOKENISH = re.compile(r"[A-Za-z0-9_+/=-]{16,}")


def entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


def _random_looking(v: str) -> bool:
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return len(v) >= 16 and classes >= 2 and entropy(v) >= 3.3


_EXACT = [(rid, re.compile(p)) for rid, p in EXACT]


_STOP_L = frozenset(" \t\r\n:=«\"'(<[—")
_STOP_R = frozenset(" \t\r\n»\"',;)>]")


def _whole(text: str, f: Found) -> Found:
    """Секрет заменяется до границ слова: правило, начавшее значение с середины слова, не должно оставить в тексте часть пароля."""
    s, e = f.start, f.end
    while s > 0 and text[s - 1] not in _STOP_L:
        s -= 1
    while e < len(text) and text[e] not in _STOP_R and not (text[e] == "." and (e + 1 == len(text) or text[e + 1].isspace())):
        e += 1
    return Found(s, e, f.category, f.rule)


# --- 4. пары «имя → значение» в конфигурациях, коде, JSON/XML/HTML, таблицах и CSV. Имя разбирается на части: DB_PASSWORD, X-API-Key, apiKey, ПарольБД, HTTPПароль
_SECRET_END = ("password", "passwd", "passphrase", "token", "secret", "apikey", "пароль", "токен", "секрет", "парольбд")
_SECRET_PART = {"pwd", "pass", "psw", "pw", "sas", "otp", "totp", "hotp", "seed", "pin", "cvv", "cvc", "credential", "credentials", "auth", "cookie", "пар",
                "authorization", "session", "sessionid", "sid", "remember", "dbpwd", "dbpass"}
_KEY_QUAL = {"api", "access", "secret", "private", "auth", "signing", "encryption", "account", "client", "master", "app", "license", "ssh", "апи", "доступа", "лицензии"}
_LOGIN_PART = {"user", "username", "login", "uid", "usr", "логин", "пользователь", "пользователя", "имяпользователя", "лог", "учетка", "учётка"}
_NOT_VALUE = {"url", "uri", "endpoint", "path", "file", "type", "name", "ttl", "expires", "expiry", "length", "enabled", "required", "policy", "min", "max", "count",
              "mode", "format", "lifetime", "timeout", "header", "field", "label", "hint", "prompt", "id", "не", "not", "no", "без", "имя"}


def key_parts(ident: str) -> list[str]:
    s = re.sub(r"([a-zа-яё0-9])([A-ZА-ЯЁ])", r"\1 \2", ident)
    s = re.sub(r"([A-ZА-ЯЁ]+)([A-ZА-ЯЁ][a-zа-яё])", r"\1 \2", s)           # HTTPПароль → HTTP Пароль, APIКлюч → API Ключ
    return [x.lower().replace("ё", "е") for x in re.split(r"[\s_.$:-]+", s) if x]


def key_kind(ident: str) -> str:
    """secret | login | '' — что хранит поле с таким именем."""
    parts = key_parts(ident)
    if not parts:
        return ""
    whole = "".join(parts)
    if any(p in ("не", "not", "no", "без", "non") for p in parts):
        return ""                                                   # ЭтоНеПароль, not_a_password
    secret = (any(p.endswith(_SECRET_END) for p in parts) or whole.endswith(_SECRET_END) or any(p in _SECRET_PART for p in parts)
              or (any(p in ("key", "ключ") for p in parts) and any(p in _KEY_QUAL for p in parts)))
    if secret:
        return "" if any(p in _NOT_VALUE for p in parts if p not in ("auth",)) and not parts[-1].endswith(_SECRET_END) else "secret"
    if any(p in _LOGIN_PART for p in parts) or whole in ("userid", "username", "loginname") or (parts[-1].endswith(("uid", "usr", "user", "login")) and not parts[-1].endswith(("guid", "uuid"))):
        return "" if any(p in _NOT_VALUE - {"name", "id", "имя"} for p in parts) else "login"
    return ""


def is_value(v: str, kind: str = "secret") -> bool:
    """Похоже ли на значение, а не на слово или заглушку: «пароль: не менять», password=${TOKEN}, token_ttl=3600 — не значения."""
    v = v.strip()
    if len(v) < (2 if kind == "login" else 4) or v.startswith("[") or _PLACEHOLDER.match(v) or v.lower() in _STOP_VALUES:
        return False
    if len(v.split()) >= 3 and re.fullmatch(r"[\w\s,.-]+", v) and not re.search(r"\d", v):
        return False                                            # «просто строка без метки» — фраза, а не значение
    if kind == "secret" and v.isdigit() and len(v) < 6:
        return False
    if kind == "secret" and v.lower() in ("bearer", "basic", "jwt", "oauth", "md5", "sha256", "sha1", "bcrypt", "argon2"):
        return False
    return True


_IDENT = r"[A-Za-zА-Яа-яЁё_][\w.$-]{0,60}"
_VAL = r"(?:\"(?P<q1>[^\"\n]{1,200})\"|'(?P<q2>[^'\n]{1,200})'|«(?P<q3>[^»\n]{1,200})»|(?P<u>[^\s,;\"'<>)]{1,200}))"
KV = re.compile(rf"(?<![\w$.-])(?P<q>[\"']?)(?P<k>{_IDENT}|User Id|User ID)(?P=q)\s*(?:=>|:=|=|:|(?<=[\"']),)\s*{_VAL}")
XML_EL = re.compile(r"<(?P<k>[\w:.-]{2,60})(?:\s[^>]{0,80})?>(?P<v>[^<\n]{1,200})</(?P=k)>")
HTML_ATTR = re.compile(r"(?i)\b(?:name|id|key|property)\s*=\s*[\"'](?P<k>[\w.-]{2,60})[\"'][^>]{0,80}?\b(?:value|content)\s*=\s*[\"'](?P<v>[^\"'\n]{1,200})[\"']")
PAIR = re.compile(rf"(?i)(?<![\w-])(?P<k1>{_IDENT})\s*(?P<d>[/|;,])\s*(?P<k2>{_IDENT})\s*[:=]+\s*(?P<v1>[^\s/|;,]{{2,64}})\s*(?P=d)\s*(?P<v2>[^\s|;,]{{2,128}})")
STRUCT_1C = re.compile(r"(?i)Новый\s+(?:Структура|Structure)\s*\(\s*\"(?P<keys>[^\"\n]+)\"\s*,(?P<rest>[^)\n]*)\)")
CONN_1C = re.compile(r"(?i)Новый\s+(?:HTTPСоединение|HTTPConnection|FTPСоединение|FTPConnection)\s*\((?P<args>[^)]{0,400})\)")
_LIT = re.compile(r"\"([^\"\n]*)\"")


def _val(m: re.Match) -> tuple[int, int, str] | None:
    for g in ("q1", "q2", "q3", "u"):
        if m.group(g) is not None:
            v = m.group(g).rstrip(".:") if g == "u" else m.group(g)
            return m.start(g), m.start(g) + len(v), v
    return None


def credentials(text: str) -> list[Found]:
    """Значения полей с секретами (category=secret) и учётными записями (category=login), найденные по имени поля."""
    out: list[Found] = []

    def add(s: int, e: int, kind: str, rule: str) -> None:
        if kind and e > s and is_value(text[s:e], kind):
            out.append(Found(s, e, kind, rule))

    pos = 0
    while (m := KV.search(text, pos)) is not None:
        pos = m.end("k")                                            # с перекрытием: в 'user' => 'svc', 'password' => 'p' значение одной пары — имя следующей
        kind = key_kind(m.group("k"))
        v = _val(m)
        if kind and v and not (m.group("u") and "(" in m.group("u")):          # ТекущийПользователь() — вызов, а не значение
            add(v[0], v[1], kind, "named_field")
    for rx, rule in ((XML_EL, "xml_field"), (HTML_ATTR, "html_field")):
        for m in rx.finditer(text):
            add(m.start("v"), m.end("v"), key_kind(m.group("k").split(":")[-1]), rule)
    for m in PAIR.finditer(text):                                   # «логин/пароль: user / pass», «login;pass = a;b», «user|password: a|b»
        k1, k2 = key_kind(m.group("k1")), key_kind(m.group("k2"))
        if (k1, k2) == ("login", "secret"):
            add(m.start("v1"), m.end("v1"), "login", "credential_pair")
            add(m.start("v2"), m.end("v2"), "secret", "credential_pair")
    for m in STRUCT_1C.finditer(text):                              # Новый Структура("Логин,Пароль", "u", "p")
        keys = [k.strip() for k in m.group("keys").split(",")]
        for k, lit in zip(keys, _LIT.finditer(m.group("rest"))):
            add(m.start("rest") + lit.start(1), m.start("rest") + lit.end(1), key_kind(k), "onec_structure")
    for m in CONN_1C.finditer(text):                                # Новый HTTPСоединение(хост, порт, пользователь, пароль)
        args = list(re.finditer(r"[^,]+", m.group("args")))
        for i, kind in ((2, "login"), (3, "secret")):
            if len(args) > i:
                lit = _LIT.search(args[i].group(0))
                if lit:
                    s0 = m.start("args") + args[i].start() + lit.start(1)
                    add(s0, s0 + len(lit.group(1)), kind, "onec_connection")
    pairs = [f for f in out if f.rule == "credential_pair"]
    out = [f for f in out if f.rule != "named_field" or not any(f.start < g.end and g.start < f.end for g in pairs)]
    out += _tables(text)
    return out


def _tables(text: str) -> list[Found]:
    """Таблицы «| пароль | значение |» и CSV с заголовком (user,password,host): значения столбцов с секретами и учётными записями."""
    out: list[Found] = []
    pos, header, delim = 0, None, ""
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        if "|" in line:                                             # ячейка с ключевым словом, за ней — значение
            cells = list(re.finditer(r"[^|]+", line))
            for a, b in zip(cells, cells[1:]):
                label = a.group(0).strip()
                kind = "secret" if len(label) <= 40 and not re.search(r"[=:]", label) and re.search(r"(?i)парол|password|passwd|\bpwd\b|токен|token|api[ -]?key|ключ|secret|секрет", label) else ""
                v = b.group(0).strip()
                if kind and " " not in v and "=" not in v and is_value(v):
                    s0 = start + b.start() + b.group(0).index(v)
                    out.append(Found(s0, s0 + len(v), kind, "table_cell"))
        d = next((c for c in (",", ";", "\t") if line.count(c) >= 1), "")
        cells = [c.strip() for c in line.split(d)] if d else []
        kinds = [key_kind(c) if re.fullmatch(r"[\w .-]{1,40}", c) else "" for c in cells]
        if d and any(kinds) and all(re.fullmatch(r"[\w .-]{1,40}", c) for c in cells):
            header, delim = kinds, d                                # строка заголовка CSV
            continue
        if header and d == delim and len(cells) == len(header):
            off = start
            for c, kind in zip(line.split(delim), header):
                v = c.strip()
                if kind and v and is_value(v, kind):
                    s0 = off + c.index(v)
                    out.append(Found(s0, s0 + len(v), kind, "csv_column"))
                off += len(c) + 1
        elif not line.strip() or (header and d != delim):
            header = None
    return out


_LAT2CYR = str.maketrans("aceopxyuAEOPCTHBKMX", "асеорхуиАЕОРСТНВКМХ")
_CYR2LAT = str.maketrans("асеорхуАЕОРСТНВКМХ", "aceopxyAEOPCTHBKMX")
_MIXED = re.compile(r"[A-Za-zА-Яа-яЁё]+")


def shadow(text: str) -> str:
    """Копия текста той же длины, где в словах из смеси алфавитов буквы-двойники приведены к алфавиту большинства: «рожденuя» → «рождения»,
    «Pаssword» → «Password». Поиск идёт по копии, замены — по тем же позициям исходного текста."""
    def fix(m: re.Match) -> str:
        w = m.group(0)
        lat = sum(c < "\u0400" for c in w)
        if not lat or lat == len(w):
            return w
        if re.search(r"[A-Z]{2,}[А-ЯЁа-яё]|[А-ЯЁ]{2,}[A-Za-z]|[а-яё][A-Z]{2,}|[a-z][А-ЯЁ]{2,}", w):
            return w                                            # слитная аббревиатура (HTTPПароль, APIКлюч, КлючAPI) — не подмена букв
        return w.translate(_LAT2CYR if lat * 2 < len(w) else _CYR2LAT)
    return _MIXED.sub(fix, text)


def find_secrets(text: str) -> list[Found]:
    return [_whole(text, f) for f in _find(shadow(text))]


def _find(text: str) -> list[Found]:
    out: list[Found] = []
    for rid, rx in _EXACT:
        for m in rx.finditer(text):
            if "v" in rx.groupindex and m.group("v") is not None:
                v = m.group("v").rstrip(".,")
                if v.startswith("[") or _PLACEHOLDER.match(v):
                    continue                  # метка уже выполненной замены («password=[секрет удалён]»)
                out.append(Found(m.start("v"), m.start("v") + len(v), "secret", rid))
            else:
                out.append(Found(m.start(), m.end(), "secret", rid))
    for m in NEAR.finditer(text):
        v = m.group("v").rstrip(".:)")
        if not v.startswith("[") and not _PLACEHOLDER.match(v):
            out.append(Found(m.start("v"), m.start("v") + len(v), "secret", "keyword_near_value"))
    for m in CONTEXT.finditer(text):
        v = m.group("v").rstrip(".:)")
        if v.startswith("[") or _PLACEHOLDER.match(v) or v.lower() in _STOP_VALUES or (v.isalpha() and v.islower() and len(v) < 12 and not re.search(r"\d", v)):
            continue                      # «пароль: не менять», «токен — сбросить»: слово, а не значение
        out.append(Found(m.start("v"), m.start("v") + len(v), "secret", "context_keyword"))
    out += [f for f in credentials(text) if f.category == "secret"]
    for m in _MARKER.finditer(text):
        tail = text[m.end(): m.end() + 120].split("\n")[0]            # только та же строка: после замены первого значения правило не должно дотягиваться до следующего
        t = _TOKENISH.search(tail)
        if t and _random_looking(t.group(0)):
            out.append(Found(m.end() + t.start(), m.end() + t.end(), "secret", "entropy_near_marker"))
    return out
