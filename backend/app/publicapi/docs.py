"""Описание публичного API: OpenAPI 3.1 (JSON) и справочная страница, собранная на сервере (без скриптов и внешних ресурсов — работает под строгим CSP)."""
from __future__ import annotations

import html

from fastapi import Request
from fastapi.openapi.utils import get_openapi

from .auth import Access
from .scopes import RATE_CLASSES, SCOPES

PREFIX = "/api/public/v1"
INTRO = (
    "Публичный API для интеграций. Авторизация — ключ сервисной учётной записи в заголовке `Authorization: Bearer pgk_…`. "
    "Права ключа (scopes) и область комнат задаёт администратор. Ошибки — единый формат `{\"error\": {\"code\", \"message\", \"request_id\"}}`. "
    "Списки постраничные: параметр `after` принимает `next_cursor` из предыдущего ответа. Лимиты запросов видны в заголовках `X-RateLimit-*`; при превышении — 429 и `Retry-After`."
)


def _public_routes(request: Request) -> list:
    from .routes import router  # noqa: PLC0415 — маршруты берём из самого роутера: приложение хранит подключённые роутеры лениво (FastAPI ≥ 0.14x)

    return [r for r in router.routes if getattr(r, "path", "").startswith(PREFIX + "/")]


def _scope_of(route) -> tuple[str | None, str | None]:
    dep = getattr(route, "dependant", None)
    for d in getattr(dep, "dependencies", []) or []:
        if isinstance(d.call, Access):
            return d.call.scope, d.call.rate
    return None, None


def build_spec(request: Request) -> dict:
    cached = getattr(request.app.state, "api_spec", None)
    if cached:
        return cached
    routes = _public_routes(request)
    spec = get_openapi(title="Peregovorka Public API", version="1", openapi_version="3.1.0", description=INTRO, routes=routes,
                       servers=[{"url": str(request.base_url).rstrip("/")}])
    spec["components"].setdefault("securitySchemes", {})["ApiKey"] = {"type": "http", "scheme": "bearer", "bearerFormat": "pgk_<id>_<secret>",
                                                                       "description": "Ключ сервисной учётной записи; выдаётся администратором, показывается один раз."}
    spec["security"] = [{"ApiKey": []}]
    spec["x-scopes"] = SCOPES
    spec["x-rate-classes"] = list(RATE_CLASSES)
    by_op = {}
    for r in routes:
        scope, rate = _scope_of(r)
        for m in getattr(r, "methods", None) or []:
            by_op[(r.path, m.lower())] = (scope, rate)
    for path, item in spec["paths"].items():
        for method, op in item.items():
            scope, rate = by_op.get((path, method), (None, None))
            op["x-required-scope"] = scope
            op["x-rate-class"] = rate
            if scope:
                op["description"] = f"**Требуется право:** `{scope}`.\n\n" + (op.get("description") or "")
    request.app.state.api_spec = spec
    return spec


CSS = """body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:0 auto;padding:1rem 1.2rem;color:#1a2233;background:#fff}
code{background:#eef1f6;padding:.05rem .3rem;border-radius:4px}section{border-top:1px solid #dde3ee;padding:.4rem 0}h3{margin:.6rem 0 .2rem}
.m{display:inline-block;min-width:3.6rem;text-align:center;border-radius:4px;color:#fff;font-size:.8rem;padding:.1rem .3rem;background:#2563eb}.m.post{background:#16a34a}.m.delete{background:#dc2626}
.sc,.c{color:#556;font-size:.9rem}.d{white-space:pre-wrap}table{border-collapse:collapse}td{border:1px solid #dde3ee;padding:.25rem .5rem}
"""


def _e(s: object) -> str:
    return html.escape(str(s if s is not None else ""))


def render_html(spec: dict) -> str:
    rows = []
    for path, item in sorted(spec["paths"].items()):
        for method, op in item.items():
            params = "".join(f"<li><code>{_e(p['name'])}</code> ({_e(p['in'])}{', обязательный' if p.get('required') else ''}) — {_e(p.get('description') or '')}</li>" for p in op.get("parameters", []))
            codes = ", ".join(sorted(op.get("responses", {})))
            rows.append(
                f"<section id='{_e(op.get('operationId'))}'><h3><span class='m {_e(method)}'>{_e(method.upper())}</span> <code>{_e(path)}</code></h3>"
                f"<p>{_e(op.get('summary'))}</p>"
                + (f"<p class='sc'>Право: <code>{_e(op['x-required-scope'])}</code> · класс лимита: <code>{_e(op.get('x-rate-class'))}</code></p>" if op.get("x-required-scope") else "")
                + (f"<p class='d'>{_e(op.get('description'))}</p>" if op.get("description") else "")
                + (f"<ul>{params}</ul>" if params else "") + f"<p class='c'>Ответы: {_e(codes)}</p></section>")
    scopes = "".join(f"<tr><td><code>{_e(k)}</code></td><td>{_e(v)}</td></tr>" for k, v in SCOPES.items())
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Peregovorka Public API</title>
<link rel="stylesheet" href="{PREFIX}/docs.css"></head>
<body><h1>Peregovorka Public API</h1><p>{_e(INTRO)}</p>
<p>Машиночитаемое описание: <a href="{PREFIX}/openapi.json">openapi.json</a> (OpenAPI 3.1).</p>
<h2>Права ключа</h2><table>{scopes}</table><h2>Методы</h2>{''.join(rows)}</body></html>"""
