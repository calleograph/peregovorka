"""Лёгкий встроенный WAF: базовая защита от массового сканирования и типовых автоматических атак на API.

Это НЕ промышленный WAF и не замена OWASP Core Rule Set на внешнем узле. Он дополняет самостоятельные механизмы защиты приложения (права доступа, параметризованные запросы,
экранирование вывода, безопасная работа с файлами), а не заменяет их. Если перед сервером стоит настоящий WAF, встроенный можно оставить в режиме «Наблюдение» или выключить.

Что проверяется (только метаданные запроса — путь, строка запроса, метод, User-Agent, частота ответов 404/405; ТЕЛА ЗАПРОСОВ НЕ СКАНИРУЮТСЯ: чаты, протоколы и инструкции содержат произвольный
текст, и поиск «опасных слов» в нём дал бы ложные срабатывания):
  path_traversal   — «..», закодированные варианты, обратная косая черта, NUL в пути и строке запроса;
  bad_method       — методы вне GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS (TRACE, CONNECT и т. п.);
  scanner_path     — пробы служебных путей под /api (.env, .git, phpinfo, wp-*, actuator, …);
  sqli_query       — типовые признаки SQL-инъекции в строке запроса (эвристика);
  xss_query        — типовые признаки XSS в строке запроса (эвристика);
  cmd_query        — признаки внедрения команд в строке запроса (эвристика);
  bot_ua           — известные сканеры в User-Agent (sqlmap, nikto, nmap, masscan, zgrab, nuclei…);
  api_probe        — много ответов 404/405 подряд с одного адреса за минуту (перебор путей и идентификаторов).
Режимы: «Выключен» · «Наблюдение» (только записывает событие) · «Защита» (проверенные правила блокируют, новые эвристические остаются в наблюдении, пока администратор не переведёт их в «Блок»).
Блокировка касается запросов без сессии: вошедший пользователь (cookie сессии или гостевой токен) с ложным срабатыванием на обычной работе не остановится — отказ получает только анонимный трафик.
WebSocket, загрузки и скачивания (длинные и потоковые запросы) не буферизуются и не читаются: правила смотрят лишь на заголовок запроса. Исключения (пути и адреса) настраиваются администратором.
"""
from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass

RULES: dict[str, dict] = {
    "path_traversal": {"title": "Выход за пределы каталога (../)", "verified": True, "blurb": "Признаки «..», закодированные варианты и NUL в пути и строке запроса. Законным запросам приложения не свойственны."},
    "bad_method": {"title": "Недопустимый метод HTTP", "verified": True, "blurb": "TRACE, CONNECT, нестандартные методы. Приложению нужны только GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS."},
    "scanner_path": {"title": "Проба служебных путей", "verified": True, "blurb": "Обращения под /api к .env, .git, phpinfo, wp-admin, actuator и другим путям, которых в приложении нет."},
    "sqli_query": {"title": "Признаки SQL-инъекции в строке запроса", "verified": False, "blurb": "Эвристика: union select, or 1=1, sleep(), information_schema… Может срабатывать на необычные поисковые запросы."},
    "xss_query": {"title": "Признаки XSS в строке запроса", "verified": False, "blurb": "Эвристика: <script, javascript:, onerror=… Вывод в приложении экранируется независимо от этого правила."},
    "cmd_query": {"title": "Признаки внедрения команд", "verified": False, "blurb": "Эвристика: /etc/passwd, /bin/sh, $(…), обратные кавычки с командами."},
    "bot_ua": {"title": "Известные сканеры в User-Agent", "verified": False, "blurb": "sqlmap, nikto, nmap, masscan, zgrab, nuclei, gobuster и подобные. User-Agent легко подделать, поэтому это лишь дополнительный сигнал."},
    "api_probe": {"title": "Перебор путей и идентификаторов", "verified": False, "blurb": "Много ответов 404/405 с одного адреса за минуту. Порог настраивается."},
}
ALLOWED_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}

_TRAV = re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)|%2e%2e|%252e|\.\.%2f|\.\.%5c|%00|\x00", re.I)
_SCAN = re.compile(r"/(?:\.env|\.git|\.svn|\.hg|\.ds_store|\.aws|\.ssh|phpinfo\.php|wp-admin|wp-login\.php|wp-content|xmlrpc\.php|phpmyadmin|cgi-bin|actuator|server-status|vendor/phpunit|"
                   r"config\.php|admin\.php|shell\.php|backup\.(?:zip|sql|tar)|dump\.sql|id_rsa|\.bash_history|boaform|HNAP1)", re.I)
_SQLI = re.compile(r"(?:\bunion\b[\s/\*+]+(?:all[\s/\*+]+)?select\b|\bselect\b[^&]{0,40}\bfrom\b[^&]{0,40}\binformation_schema\b|\binformation_schema\b|['\"]\s*(?:or|and)\s+['\"]?\w+['\"]?\s*=\s*['\"]?\w+|"
                   r"\b(?:or|and)\s+\d+\s*=\s*\d+\b|\bsleep\s*\(\s*\d|\bbenchmark\s*\(|\bwaitfor\s+delay\b|;\s*(?:drop|truncate|alter|insert|update|delete)\s+\w|\bxp_cmdshell\b|--\s*$|/\*!\d)", re.I)
_XSS = re.compile(r"(?:<\s*script\b|<\s*/\s*script|javascript\s*:|\bon(?:error|load|click|mouseover|focus)\s*=|<\s*(?:iframe|svg|img|body|embed|object)\b[^>]*\bon\w+\s*=|<\s*iframe\b|document\.cookie|\balert\s*\()", re.I)
_CMD = re.compile(r"(?:/etc/(?:passwd|shadow)|/bin/(?:ba)?sh\b|\$\([^)]{1,60}\)|`[^`]{1,60}`|;\s*(?:cat|ls|id|whoami|wget|curl|nc|bash|sh)\b|\|\s*(?:cat|ls|id|whoami|wget|curl|nc|bash|sh)\b|\bcmd\.exe\b|powershell\s+-)", re.I)
_BOT = re.compile(r"(?:sqlmap|nikto|nmap|masscan|zgrab|nuclei|gobuster|dirbuster|wpscan|acunetix|nessus|openvas|havij|whatweb|arachni|w3af|fimap|metasploit|python-requests/0\.|libwww-perl|zmeu)", re.I)


@dataclass(frozen=True)
class Hit:
    rule: str
    detail: str


def _norm(s: str) -> str:
    """Строка запроса в читаемом виде (дважды декодированная): так нельзя скрыть признак двойным кодированием."""
    try:
        return urllib.parse.unquote(urllib.parse.unquote_plus(s))
    except Exception:  # noqa: BLE001
        return s


def inspect(method: str, path: str, raw_query: bytes | str, user_agent: str) -> list[Hit]:
    """Признаки по метаданным запроса. Тела не читаются."""
    q = raw_query.decode("latin-1") if isinstance(raw_query, (bytes, bytearray)) else (raw_query or "")
    hits: list[Hit] = []
    if method.upper() not in ALLOWED_METHODS:
        hits.append(Hit("bad_method", method[:20]))
    if _TRAV.search(path) or _TRAV.search(q):
        hits.append(Hit("path_traversal", (path + ("?" + q if q else ""))[:160]))
    if _SCAN.search(path):
        hits.append(Hit("scanner_path", path[:160]))
    dq = _norm(q)
    dpath = _norm(path)
    blob = dq + " " + dpath
    if blob.strip():
        if _SQLI.search(blob):
            hits.append(Hit("sqli_query", blob[:160]))
        if _XSS.search(blob):
            hits.append(Hit("xss_query", blob[:160]))
        if _CMD.search(blob):
            hits.append(Hit("cmd_query", blob[:160]))
    if user_agent and _BOT.search(user_agent):
        hits.append(Hit("bot_ua", user_agent[:100]))
    return hits


def hour_key(now: float | None = None) -> str:
    return time.strftime("%Y%m%d%H", time.gmtime(now or time.time()))
