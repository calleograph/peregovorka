"""Аутентификация и адреса EWS: Basic, NTLM, Negotiate (Kerberos с запасным NTLM) и Kerberos; автоопределение по ответу сервера; несколько серверов за балансировщиком.

Ничего не зашито: адреса, домены, области (realm) и учётные записи берутся только из настроек. Пароль не попадает ни в тексты ошибок, ни в журнал (redact).
NTLM реализован в чистом Python (pyspnego); Kerberos использует системные библиотеки MIT krb5 (gssapi + krb5), в образе они есть. Для Kerberos имя сервера в адресе EWS
должно совпадать с зарегистрированным именем службы HTTP/<имя> (при балансировщике — имя балансировщика).
"""
from __future__ import annotations

import base64
import os
import re
import tempfile
from urllib.parse import urlsplit

import httpx

AUTH_CHOICES = ("auto", "basic", "ntlm", "negotiate", "kerberos")
ACCESS_CHOICES = ("auto", "impersonation", "delegate")
SCHEME_LABEL = {"basic": "Basic", "ntlm": "NTLM", "negotiate": "Negotiate (Kerberos или NTLM)", "kerberos": "Kerberos"}
AUTO_ORDER = ("basic", "ntlm", "negotiate")          # порядок по умолчанию: как работало раньше (Basic), затем доменные способы
DEFAULT_PATH = "/EWS/Exchange.asmx"
MAX_URLS = 8


def redact(text: str, *secrets: str) -> str:
    """Убирает секреты из текста (пароль, а также base64-представление пары логин:пароль) — для сообщений и журнала."""
    out = str(text)
    for s in secrets:
        if s and len(s) >= 1:
            out = out.replace(s, "***")
    return re.sub(r"(?i)(Authorization:\s*\S+\s+)\S+", r"\1***", out)


def normalize_urls(raw: str) -> list[str]:
    """Адреса EWS по одному в строке (или через ; ,). `mail.example.org` → https://mail.example.org/EWS/Exchange.asmx. Только HTTPS. ValueError — понятное сообщение."""
    out: list[str] = []
    for part in re.split(r"[\s;,]+", (raw or "").strip()):
        if not part:
            continue
        if "://" not in part:
            part = "https://" + part
        u = urlsplit(part)
        if u.scheme != "https":
            raise ValueError(f"Адрес EWS «{part}»: допустим только HTTPS (учётные данные нельзя передавать открытым текстом).")
        if not u.hostname or u.username or u.password:
            raise ValueError(f"Адрес EWS «{part}» некорректен (без логина и пароля в адресе).")
        path = u.path if u.path not in ("", "/") else DEFAULT_PATH
        url = f"https://{u.netloc}{path}"
        if url not in out:
            out.append(url)
    if len(out) > MAX_URLS:
        raise ValueError(f"Слишком много адресов EWS (не больше {MAX_URLS}).")
    return out


def credentials(user: str, domain: str) -> tuple[str, str]:
    """(имя для NTLM/Kerberos, имя для Basic): `ДОМЕН\\user` и UPN принимаются как есть; отдельный домен дописывается, если в имени его нет."""
    user = user.strip()
    domain = domain.strip()
    if domain and "\\" not in user and "@" not in user:
        return f"{domain}\\{user}", f"{domain}\\{user}"
    return user, user


def parse_schemes(header_values: list[str]) -> list[str]:
    """Схемы из заголовков WWW-Authenticate ответа 401 (в порядке предложения, без повторов), в нижнем регистре."""
    out: list[str] = []
    for v in header_values:
        for m in re.finditer(r"(?i)(?:^|,)\s*(Basic|NTLM|Negotiate|Kerberos|Bearer|Digest)\b", v):
            s = m.group(1).lower()
            if s not in out:
                out.append(s)
    return out


def choose(offered: list[str], preferred: str) -> str | None:
    """Выбор схемы: явная настройка требует, чтобы сервер её предлагал; auto берёт первую из предложенных в порядке AUTO_ORDER."""
    if preferred == "auto":
        for s in AUTO_ORDER:
            if s in offered:
                return s
        return None
    need = "negotiate" if preferred == "kerberos" else preferred
    return preferred if need in offered else None


def apply_kerberos_env(realm: str, kdc: str) -> None:
    """Если администратор указал область/KDC — собирает минимальный krb5.conf; иначе область и KDC находятся стандартно (DNS)."""
    if not (realm or kdc):
        return
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", realm or "x") or not re.fullmatch(r"[A-Za-z0-9.\-:]+", kdc or "x"):
        raise ValueError("Область Kerberos и KDC: только буквы, цифры, точки и дефисы.")
    lines = ["[libdefaults]", "  dns_lookup_kdc = true", "  dns_lookup_realm = false", "  rdns = false"]
    if realm:
        lines.append(f"  default_realm = {realm.upper()}")
        if kdc:
            lines += ["[realms]", f"  {realm.upper()} = {{", f"    kdc = {kdc}", "  }"]
    path = os.path.join(tempfile.gettempdir(), "peregovorka-krb5.conf")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.environ["KRB5_CONFIG"] = path


class SpnegoAuth(httpx.Auth):
    """NTLM / Negotiate по схеме запрос-ответ поверх httpx. Требует одного соединения на весь обмен (NTLM привязан к соединению): клиент создаётся с max_connections=1."""

    def __init__(self, scheme: str, username: str, password: str, host: str):
        self.scheme, self._user, self._pw, self._host = scheme, username, password, host      # scheme: ntlm | negotiate | kerberos

    def _context(self):
        import spnego  # noqa: PLC0415

        return spnego.client(self._user, self._pw, hostname=self._host, service="HTTP", protocol={"ntlm": "ntlm", "negotiate": "negotiate", "kerberos": "kerberos"}[self.scheme])

    def auth_flow(self, request: httpx.Request):
        ctx = self._context()
        word = "NTLM" if self.scheme == "ntlm" else "Negotiate"
        token = ctx.step()
        request.headers["Authorization"] = f"{word} {base64.b64encode(token or b'').decode('ascii')}"
        response = yield request
        for _ in range(4):
            if response.status_code != 401 or ctx.complete:
                return
            challenge = next((h.split(None, 1)[1] for h in response.headers.get_list("www-authenticate") if h.lower().startswith(word.lower() + " ") and len(h.split(None, 1)) == 2), None)
            if not challenge:
                return
            out = ctx.step(base64.b64decode(challenge.strip()))
            if out is None:
                return
            request.headers["Authorization"] = f"{word} {base64.b64encode(out).decode('ascii')}"
            response = yield request


def build_auth(scheme: str, user: str, password: str, domain: str, host: str):
    name, basic_name = credentials(user, domain)
    if scheme == "basic":
        return httpx.BasicAuth(basic_name, password)
    return SpnegoAuth(scheme, name, password, host)
