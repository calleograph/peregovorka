#!/usr/bin/env python3
"""Проверка репозитория на утечку частной конфигурации (внутренние домены, имена хостов, адреса, e-mail, IP).

Проект публичный и универсальный: он знает, КАК подключить API, LDAP или SMTP, но не знает, какие именно использует конкретный сервер.
Поэтому проверка построена «от обратного» — не по списку чужих секретов (его негде хранить), а по списку РАЗРЕШЁННОГО:

  * каждое доменное имя и адрес электронной почты в отслеживаемых git файлах должен оканчиваться на запись из `scripts/private-data.allow`
    (example.com, *.test, corp.local, github.com …) — любой другой домен считается возможной частной настройкой и валит проверку;
  * IP-адреса — только из разрешённых диапазонов (loopback, документационные, тестовые числа из `ip:` в том же файле);
  * дополнительно можно указать СВОИ запрещённые шаблоны вне репозитория: `PRIVATE_DENY_FILE=/путь/к/файлу` (по регулярному выражению в строке) —
    например, имена вашей организации; сам файл в git не кладётся.

Запуск: `python3 scripts/check_private_data.py` (код 1 — найдено; 0 — чисто). В CI вызывается отдельным шагом. Флаг `--history` дополнительно просматривает
добавленные строки всей истории git (долго; для разовой проверки перед публичным релизом).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_FILE = ROOT / "scripts" / "private-data.allow"
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".pdf", ".zip", ".gz", ".onnx", ".gguf", ".wav", ".mp3", ".lock"}
SKIP_NAMES = {"package-lock.json", "private-data.allow"}
SELF = "scripts/check_private_data.py"

TLDS = "com|org|net|ru|su|io|ai|dev|app|cloud|local|lan|corp|internal|intranet|home|test|invalid|example"
DOMAIN = re.compile(r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:%s))(?![\w-]|\.[a-z])" % TLDS, re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
# имена файлов/кода, похожие на домен (app.state, config.ru-…): отсеиваются по расширению первого/последнего компонента
CODE_LIKE = re.compile(r"^(?:ws|self|this|app|window|document|os|sys|req|res|settings|config|cfg|state|ctx|request|response|result|data|obj|item|row)\.", re.I)


def load_allow() -> tuple[list[str], list[re.Pattern[str]]]:
    domains: list[str] = []
    ips: list[re.Pattern[str]] = []
    for raw in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("ip:"):
            ips.append(re.compile(line[3:].strip()))
        else:
            domains.append(line.lower().lstrip("*."))
    return domains, ips


def domain_ok(d: str, allowed: list[str]) -> bool:
    d = d.lower()
    return any(d == a or d.endswith("." + a) for a in allowed)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return [f for f in out.split("\0") if f]


def scan_text(name: str, text: str, allowed: list[str], ips: list[re.Pattern[str]], deny: list[re.Pattern[str]]) -> list[str]:
    found: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        for rx in deny:
            m = rx.search(line)
            if m:
                found.append(f"{name}:{n}: запрещённый шаблон «{m.group(0)}»")
        for m in DOMAIN.finditer(line):
            d = m.group(1)
            if CODE_LIKE.match(d) or domain_ok(d, allowed):
                continue
            found.append(f"{name}:{n}: домен «{d}» не из списка разрешённых")
        for m in EMAIL.finditer(line):
            if not domain_ok(m.group(1), allowed):
                found.append(f"{name}:{n}: адрес e-mail на домене «{m.group(1)}» не из списка разрешённых")
        for m in IPV4.finditer(line):
            ip = m.group(1)
            parts = ip.split(".")
            if any(int(p) > 255 for p in parts):
                continue                                   # версия (1.2.3.456), а не адрес
            if not any(rx.fullmatch(ip) for rx in ips):
                found.append(f"{name}:{n}: IP-адрес {ip} не из списка разрешённых")
    return found


def main() -> int:
    allowed, ips = load_allow()
    deny: list[re.Pattern[str]] = []
    extra = os.environ.get("PRIVATE_DENY_FILE")
    if extra and Path(extra).is_file():
        deny = [re.compile(ln.strip(), re.I) for ln in Path(extra).read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    problems: list[str] = []
    for f in tracked_files():
        p = ROOT / f
        if f == SELF or p.name in SKIP_NAMES or p.suffix.lower() in SKIP_SUFFIX or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        problems += scan_text(f, text, allowed, ips, deny)
    if "--history" in sys.argv:
        log = subprocess.run(["git", "log", "--all", "-p", "--no-color", "--format=commit %h", "-U0"], cwd=ROOT, capture_output=True).stdout.decode("utf-8", "replace")
        commit, cur = "?", ""
        seen: set[str] = set()
        for line in log.splitlines():
            if line.startswith("commit "):
                commit = line[7:]
            elif line.startswith("+++ "):
                cur = line[6:] if line.startswith("+++ b/") else line[4:]
            elif line.startswith("+") and not line.startswith("+++") and cur and not cur.endswith(tuple(SKIP_SUFFIX)) and cur.split("/")[-1] not in SKIP_NAMES:
                for item in scan_text(f"{commit}:{cur}", line[1:], allowed, ips, deny):
                    key = item.split(": ", 1)[-1]
                    if key not in seen:
                        seen.add(key)
                        problems.append(item)
    if problems:
        print("Найдены возможные частные данные (проверьте и замените на универсальные значения вроде example.com, либо внесите безобидное в scripts/private-data.allow):")
        limit = None if "--all" in sys.argv else 200
        for item in problems[:limit]:
            print("  " + item)
        if limit and len(problems) > limit:
            print(f"  … и ещё {len(problems) - limit} (все: --all)")
        return 1
    print("Частных данных не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
