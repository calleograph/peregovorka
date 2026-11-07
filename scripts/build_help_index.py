#!/usr/bin/env python3
"""Индекс справки в приложении из вики: вопросы со страницы «Поиск-по-вопросам», все статьи, роли из страниц «Маршрут-…». --check сверяет готовый файл и не пишет."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
OUT = ROOT / "frontend" / "src" / "help" / "helpIndex.json"
LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)")
ROLES = {"сотрудника": "Сотрудник", "организатора": "Организатор", "руководителя-комнаты": "Руководитель комнаты", "гостя": "Гость", "ведущего-трансляции": "Ведущий эфира",
         "зрителя": "Зритель", "администратора": "Администратор сервера", "поддержки": "Поддержка", "интегратора": "Интегратор"}


def roles_of() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for slug, role in ROLES.items():
        for page in LINK.findall((WIKI / f"Маршрут-{slug}.md").read_text(encoding="utf8")):
            out.setdefault(page, []).append(role)
    return {k: sorted(set(v)) for k, v in out.items()}


def build() -> dict:
    roles, items = roles_of(), []
    section = ""
    for line in (WIKI / "Поиск-по-вопросам.md").read_text(encoding="utf8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Вопрос", "---"):
            page = LINK.findall(cells[2])
            if page:
                items.append({"q": cells[0], "alt": cells[1], "page": page[0], "sec": section, "roles": roles.get(page[0], [])})
    asked = {i["page"] for i in items}
    for path in sorted(WIKI.glob("*.md")):
        if path.stem.startswith("_") or path.stem in ("Home", "Поиск-по-вопросам"):
            continue
        text = path.read_text(encoding="utf8")
        title = text.splitlines()[0].lstrip("# ").strip()
        heads = " ".join(h for h in re.findall(r"^##+ (.+)$", text, re.M) if h not in ("Зачем это нужно", "Когда пригодится", "Что потребуется", "Где найти", "Как это сделать", "Что получится", "Если не получается", "Что дальше"))
        items.append({"q": title, "alt": heads[:300], "page": path.stem, "sec": "Статьи", "roles": roles.get(path.stem, []), **({"also": True} if path.stem in asked else {})})
    home = (WIKI / "Home.md").read_text(encoding="utf8")
    block = home.split("## Популярные вопросы", 1)[1].split("\n## ", 1)[0]
    return {"popular": [{"q": q, "page": p} for q, p in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", block)], "items": items}


def main() -> int:
    text = json.dumps(build(), ensure_ascii=False, separators=(",", ":")) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf8") != text:
            print("Индекс справки устарел: выполните python3 scripts/build_help_index.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
