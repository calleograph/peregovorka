#!/usr/bin/env python3
"""Проверка исходников вики (каталог wiki/): ссылки между страницами, отсутствие служебных пометок на страницах для читателя, сведения о проверке
каждой страницы в wiki/_meta/Проверка-страниц.md, блок «Что дальше», версия в подвале, частные данные и секреты. Запуск: python3 scripts/check_wiki.py [--strict]. Код 1 — найдены ошибки (предупреждения не считаются, пока нет --strict)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
SERVICE = {"_Sidebar", "_Footer"}
NO_NEXT = SERVICE | {"Home"}          # у них нет блока «Что дальше»
ABOUT_MARKS = {"Сопровождение-вики"}  # страница для сопровождающих описывает сами служебные пометки
LINK = re.compile(r"\[\[([^\]]+)\]\]")
MD_LINK = re.compile(r"(?<!\!)\[[^\]]+\]\(([^)\s]+)\)")
META = WIKI / "_meta" / "Проверка-страниц.md"
# служебные пометки живут в META, а не на страницах: читатель видит обычный текст
SERVICE_MARKS = [(re.compile(p, re.I | re.M), msg) for p, msg in (
    (r"^> Относится к", "шапка «Относится к …» (перенесите в _meta/Проверка-страниц.md)"),
    (r"уровень проверки|уровн[ея] [0-4]\b|\bУ[0-4]\b", "уровень проверки"),
    (r"✔|не запускал(?:ась|ись)\b", "отметка о запуске команды"),
    (r"\bстенд", "слово «стенд» (пишите «тестовый экземпляр» или объясните ограничение словами)"),
    (r"\bисточник: код|\(уровень", "служебная ссылка на источник"),
)]
SECRETS = [re.compile(p, re.I) for p in (r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY", r"pgk_[A-Za-z0-9]{8}_[A-Za-z0-9]{20,}", r"password\s*[:=]\s*(?!YOUR|CHANGE|ПАРОЛЬ|\*)[^\s`]{6,}")]
DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:ru|su|рф|local|corp|lan|internal)\b", re.I)
ALLOWED_DOMAINS = re.compile(r"(?:example\.(?:org|com|test|local)|corp\.example\.org|meet\.example\.org|opensmodel\.sberdevices\.ru|sberdevices\.ru|huggingface\.co|rutube\.ru|vk\.ru|vkvideo\.ru|twitch\.tv|google\.com|youtube\.com)$", re.I)
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
    meta = META.read_text(encoding="utf8") if META.exists() else ""
    for name, path in ps.items():
        text = path.read_text(encoding="utf8")
        plain = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", text, flags=re.S))      # ссылки в примерах кода не проверяются
        # --- ссылки
        for m in LINK.finditer(plain):
            errs.append(f"{name}: ссылка «[[{m.group(1)}]]» — пишите [текст](Страница): «|» внутри таблицы ломает её")
        if re.search(r"\[\[(?![^\n]*\]\])", plain):
            errs.append(f"{name}: незакрытая ссылка «[[…» показывается читателю как текст")
        for m in MD_LINK.finditer(plain):
            url = m.group(1)
            if url.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if url.endswith(".md"):
                errs.append(f"{name}: ссылка с расширением .md ({url}) откроется как файл, а не как страница")
            elif url.split("#")[0] not in ps and not url.startswith("images/"):
                errs.append(f"{name}: относительная ссылка на неизвестную страницу ({url})")
            elif url.split("#")[0] in ps:
                inbound[url.split("#")[0]] += 1
        if name in SERVICE:
            continue
        # --- шапка и структура
        for rx, msg in SERVICE_MARKS:
            if name not in ABOUT_MARKS and rx.search(plain):
                errs.append(f"{name}: служебная пометка на странице для читателя — {msg}")
        if f"| {name} |" not in meta:
            errs.append(f"{name}: нет строки в _meta/Проверка-страниц.md (версия, дата, уровень проверки)")
        if not text.startswith("# "):
            errs.append(f"{name}: первая строка должна быть заголовком «# …»")
        if name not in NO_NEXT and "## Что дальше" not in text:
            errs.append(f"{name}: нет блока «Что дальше»")
        if name == "Home" and f"{version}" not in text:
            warns.append(f"Home: версия сервера {version} не найдена на странице")
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
