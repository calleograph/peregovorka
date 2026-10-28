#!/usr/bin/env python3
"""Копирует frontend/public/glassdoc.css в backend/app/services/glassdoc_css.py (константа GLASSDOC_CSS), чтобы сервер отдавал HTML в том же оформлении, что и приложение,
а также frontend/public/mapview/viewer.{css,js} в backend/app/assets/mapview (карта в письме и в выгрузке — тот же просмотр, что в приложении).
Источник правды — CSS во frontend; тест backend/tests/test_glass_html.py проверяет, что копия не отстала. Запуск: `python3 scripts/sync_glassdoc.py` (или `--check`)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "public" / "glassdoc.css"
DST = ROOT / "backend" / "app" / "services" / "glassdoc_css.py"
HEAD = '"""Оформление «Peregovorka Glass Documents» — КОПИЯ frontend/public/glassdoc.css. Не править вручную: `python3 scripts/sync_glassdoc.py`."""\n'


def render() -> str:
    css = SRC.read_text(encoding="utf-8")
    if '"""' in css or "\\" in css.replace("\\\\", ""):
        raise SystemExit("glassdoc.css не должен содержать тройных кавычек и обратных косых черт")
    return HEAD + 'GLASSDOC_CSS = """' + css.rstrip("\n") + '\n"""\n'


VIEWER = [(ROOT / "frontend" / "public" / "mapview" / n, ROOT / "backend" / "app" / "assets" / "mapview" / n) for n in ("viewer.css", "viewer.js")]


def main() -> int:
    want = render()
    if "--check" in sys.argv:
        ok = DST.exists() and DST.read_text(encoding="utf-8") == want and all(b.exists() and a.read_bytes() == b.read_bytes() for a, b in VIEWER)
        print("копии актуальны" if ok else "копии отстали от frontend/public — выполните scripts/sync_glassdoc.py")
        return 0 if ok else 1
    DST.write_text(want, encoding="utf-8")
    print("записано", DST.relative_to(ROOT))
    for a, b in VIEWER:
        b.write_bytes(a.read_bytes())
        print("записано", b.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
