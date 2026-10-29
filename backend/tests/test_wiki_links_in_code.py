"""Ссылки «Подробнее» и «Как решить» из интерфейса и сервера ведут на существующие страницы вики (wiki/*.md): переименование страницы без правки ссылки ловится тестом."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"github\.com/calleograph/peregovorka/wiki/([A-Za-zА-Яа-яЁё0-9_-]+)")
NAMED = re.compile(r"""(?:WIKI\s*\+\s*["']|\$\{WIKI\})([A-Za-zА-Яа-яЁё0-9_-]{4,})""")
DOC_KEY = re.compile(r'"doc":\s*"([A-Za-zА-Яа-яЁё0-9_-]+)"')


def test_every_wiki_link_in_code_points_to_an_existing_page():
    pages = {p.stem for p in (ROOT / "wiki").glob("*.md")}
    found: dict[str, str] = {}
    files = [*ROOT.glob("frontend/src/**/*.ts"), *ROOT.glob("frontend/src/**/*.tsx"), *ROOT.glob("backend/app/**/*.py")]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for rx in (LINK, NAMED, DOC_KEY):
            for m in rx.finditer(text):
                found.setdefault(m.group(1), str(f.relative_to(ROOT)))
    assert len(found) >= 10, "ссылки на вики не найдены — проверьте выражения теста"
    missing = {k: v for k, v in found.items() if k not in pages}
    assert not missing, f"ссылки на несуществующие страницы вики: {missing}"
