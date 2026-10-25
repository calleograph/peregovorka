#!/usr/bin/env python3
"""Проверка исходников вики (каталог wiki/): ссылки между страницами, шапка «Относится к версии …», блок «Что дальше», версия в шапке и в подвале,
частные данные и секреты, типичные опечатки. Запуск: python3 scripts/check_wiki.py [--strict]. Код 1 — найдены ошибки (предупреждения не считаются, пока нет --strict)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
SERVICE = {"_Sidebar", "_Footer"}
NO_NEXT = SERVICE | {"Home"}          # у них нет блока «Что дальше»
LINK = re.compile(r"\[\[([^\]]+)\]\]")
MD_LINK = re.compile(r"(?<!\!)\[[^\]]+\]\(([^)\s]+)\)")
HEADER = re.compile(r"^> Относится к (?:версии|приложению)[^\n]*проверено \d{4}-\d{2}-\d{2}[^\n]*уровень проверки", re.M)
SECRETS = [re.compile(p, re.I) for p in (r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY", r"pgk_[A-Za-z0-9]{8}_[A-Za-z0-9]{20,}", r"password\s*[:=]\s*(?!YOUR|CHANGE|ПАРОЛЬ|\*)[^\s`]{6,}")]
DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:ru|su|рф|local|corp|lan|internal)\b", re.I)
ALLOWED_DOMAINS = re.compile(r"(?:example\.(?:org|com|test|local)|corp\.example\.org|meet\.example\.org|opensmodel\.sberdevices\.ru|sberdevices\.ru|huggingface\.co)$", re.I)
IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ALLOWED_IP = re.compile(r"^(?:192\.0\.2\.|127\.|0\.0\.0\.0|198\.51\.100\.|203\.0\.113\.)")


def pages() -> dict[str, Path]:
    return {p.stem: p for p in sorted(WIKI.glob("*.md"))}


def main() -> int:
    strict = "--strict" in sys.argv
    errs: list[str] = []
    warns: list[str] = []
    ps = pages()
    version = (ROOT / "VERSION").read_text(encoding="utf8").strip()
    inbound: dict[str, int] = {n: 0 for n in ps}
    for name, path in ps.items():
        text = path.read_text(encoding="utf8")
        plain = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", text, flags=re.S))      # ссылки в примерах кода не проверяются
        # --- ссылки
        for m in LINK.finditer(plain):
            target = m.group(1).split("|")[-1].strip().replace(" ", "-")
            target = target.split("#")[0]
            if target not in ps:
                errs.append(f"{name}: ссылка на несуществующую страницу «{m.group(1)}»")
            else:
                inbound[target] += 1
        for m in MD_LINK.finditer(plain):
            url = m.group(1)
            if url.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if url.endswith(".md"):
                errs.append(f"{name}: ссылка с расширением .md ({url}) откроется как файл, а не как страница")
            elif url.split("#")[0] not in ps and not url.startswith("images/"):
                errs.append(f"{name}: относительная ссылка на неизвестную страницу ({url})")
        if name in SERVICE:
            continue
        # --- шапка и структура
        if not HEADER.search(text):
            errs.append(f"{name}: нет строки «> Относится к версии …, проверено ГГГГ-ММ-ДД, уровень проверки …»")
        if not text.startswith("# "):
            errs.append(f"{name}: первая строка должна быть заголовком «# …»")
        if name not in NO_NEXT and "## Что дальше" not in text:
            errs.append(f"{name}: нет блока «Что дальше»")
        if name == "Home" and f"{version}" not in text:
            warns.append(f"Home: версия сервера {version} не найдена на странице")
        if re.search(r"```(?:bash|powershell|sh)", text) and "не запуска" not in text and "не запускал" not in text:
            errs.append(f"{name}: есть команды, но нет пометки «не запускалась» (команды, которые не выполнялись, помечаются)")
        # --- безопасность
        for rx in SECRETS:
            if rx.search(text):
                errs.append(f"{name}: похоже на секрет ({rx.pattern})")
        for d in DOMAIN.findall(text):
            if not ALLOWED_DOMAINS.search(d):
                errs.append(f"{name}: домен «{d}» не из списка разрешённых примеров")
        for ip in IP.findall(text):
            if not ALLOWED_IP.match(ip):
                errs.append(f"{name}: адрес {ip} не из тестовых диапазонов")
        for rx, msg in ((re.compile(r"\bTODO\b|\bFIXME\b|XXX"), "служебная пометка"), (re.compile(r"  +\n\n\n"), "лишние пустые строки")):
            if rx.search(text):
                warns.append(f"{name}: {msg}")
        if len(text) < 600:
            warns.append(f"{name}: очень короткая страница ({len(text)} знаков)")
        # --- версия в шапке
        m = re.search(r"версии (?:сервера )?(\d+\.\d+\.\d+)", text.split("\n", 4)[2] if len(text.split("\n")) > 3 else "")
        if m and m.group(1) != version:
            warns.append(f"{name}: версия в шапке {m.group(1)}, текущая {version} (проверьте страницу и обновите шапку)")
    footer = (WIKI / "_Footer.md").read_text(encoding="utf8") if (WIKI / "_Footer.md").exists() else ""
    if version not in footer:
        warns.append(f"_Footer: версия {version} не указана")
    side = (WIKI / "_Sidebar.md").read_text(encoding="utf8") if (WIKI / "_Sidebar.md").exists() else ""
    for name in ps:
        if name in SERVICE or name == "Home":
            continue
        if name not in side.replace(" ", "-"):
            errs.append(f"{name}: страницы нет в _Sidebar.md")
    for name, n in inbound.items():
        if n == 0 and name not in SERVICE and name != "Home":
            warns.append(f"{name}: на страницу никто не ссылается")
    for w in warns:
        print("WARN", w)
    for e in errs:
        print("FAIL", e)
    print(f"страниц: {len([n for n in ps if n not in SERVICE])}, ошибок: {len(errs)}, предупреждений: {len(warns)}")
    return 1 if errs or (strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
