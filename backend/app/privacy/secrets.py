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
    ("telegram_bot_token", r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    ("stripe_key", r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    ("stripe_webhook", r"\bwhsec_[A-Za-z0-9]{20,}\b"),
    ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("openai_key", r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}\b"),
    ("google_api_key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("google_oauth_secret", r"\bGOCSPX-[A-Za-z0-9_-]{20,}\b"),
    ("yandex_token", r"\b(?:t1\.[A-Za-z0-9_-]{40,}|y[0-3]_[A-Za-z0-9_-]{40,}|AQAAAAA[A-Za-z0-9_-]{30,})\b"),
    ("digitalocean_token", r"\bdo[por]_v1_[a-f0-9]{64}\b"),
    ("npm_token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("docker_pat", r"\bdckr_pat_[A-Za-z0-9_-]{20,}\b"),
    ("sendgrid_key", r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    ("twilio_sid", r"\bAC[a-f0-9]{32}\b"),
    ("mailgun_key", r"\bkey-[a-f0-9]{32}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ("url_credentials", r"\b[a-z][a-z0-9+.-]{1,20}://[^\s/:@]{1,64}:(?P<v>[^\s/@]{1,128})@[^\s]+"),
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
_STOP_VALUES = {"не", "нет", "none", "null", "true", "false", "отсутствует", "пустой", "скрыт", "изменён", "изменен", "сменить", "указан", "задан", "был",
                "будет", "у", "от", "для", "на", "в", "по", "из", "такой", "этот", "тот", "он", "она", "надо", "нужно", "нужен", "нужна", "сбросить", "менять"}

# --- 3. энтропия: случайная строка рядом с маркерной фразой
# ключевое слово, до четырёх слов, затем значение, похожее на пароль (буквы и цифры или спецсимволы): «пароль администратора стенда P@ssw0rd-1»
NEAR = re.compile(r"(?i)(?<![\w-])(?:парол[ьяеюи]\w*|password|passwd|токен\w*|token)(?:\s+[а-яёa-z]{1,20}\b){0,5}\s*[:=—-]?\s*[«\"']?(?<![^\s:=—«\"'-])(?P<v>(?=[^\s\"'»]*\d)(?=[^\s\"'»]*[A-Za-zА-Яа-яЁё])[^\s\"'»,;]{6,128})")
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


def find_secrets(text: str) -> list[Found]:
    return [_whole(text, f) for f in _find(text)]


def _find(text: str) -> list[Found]:
    out: list[Found] = []
    for rid, rx in _EXACT:
        for m in rx.finditer(text):
            if "v" in rx.groupindex and m.group("v") is not None:
                v = m.group("v").rstrip(".,")
                if v.startswith("["):
                    continue                  # метка уже выполненной замены («password=[секрет удалён]»)
                out.append(Found(m.start("v"), m.start("v") + len(v), "secret", rid))
            else:
                out.append(Found(m.start(), m.end(), "secret", rid))
    for m in NEAR.finditer(text):
        v = m.group("v").rstrip(".:)")
        if not v.startswith("["):
            out.append(Found(m.start("v"), m.start("v") + len(v), "secret", "keyword_near_value"))
    for m in CONTEXT.finditer(text):
        v = m.group("v").rstrip(".:)")
        if v.startswith("[") or v.lower() in _STOP_VALUES or (v.isalpha() and v.islower() and len(v) < 12 and not re.search(r"\d", v)):
            continue                      # «пароль: не менять», «токен — сбросить»: слово, а не значение
        out.append(Found(m.start("v"), m.start("v") + len(v), "secret", "context_keyword"))
    for m in _MARKER.finditer(text):
        tail = text[m.end(): m.end() + 120]
        t = _TOKENISH.search(tail)
        if t and _random_looking(t.group(0)):
            out.append(Found(m.end() + t.start(), m.end() + t.end(), "secret", "entropy_near_marker"))
    return out
