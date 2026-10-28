"""Карта разговора одним автономным HTML-файлом для письма: данные, стили и скрипт внутри, без входа в систему и без обращений к серверу.
Повторяет frontend/src/mapExport.ts; viewer.css/js — копия frontend/public/mapview (побайтовое совпадение проверяет тест)."""
from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from .glassdoc_css import GLASSDOC_CSS

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "mapview"
META_PUBLIC = ("warnings", "coverage_pct")        # в документе только то, что нужно читателю: предупреждение о неполноте; модель, токены и повторы — в диагностике администратора


def _json_for_script(value: object) -> str:
    return (json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def standalone_html(title: str, data: object, meta: dict | None) -> bytes:
    css = GLASSDOC_CSS + "\n" + (ASSETS / "viewer.css").read_text(encoding="utf-8")
    js = (ASSETS / "viewer.js").read_text(encoding="utf-8")
    body = {"data": data, "meta": {k: meta[k] for k in META_PUBLIC if k in meta} if meta else None}
    return f"""<!doctype html>
<html lang="ru" class="gd gd-html" data-gd-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title>
<style>
{re.sub(r"</(style)", r"<\\/\1", css, flags=re.I)}
</style>
</head>
<body>
<main id="app"></main>
<script type="application/json" id="pg-map-data">{_json_for_script(body)}</script>
<script>
{re.sub(r"</(script)", r"<\\/\1", js, flags=re.I)}
</script>
</body>
</html>
""".encode("utf-8")
