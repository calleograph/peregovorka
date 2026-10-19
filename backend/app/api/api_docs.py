"""Общедоступная документация API: `/api/docs` (страница) и `/api/docs/openapi.json` (OpenAPI 3.1).

Документация открыта без входа; **выполнение** запросов остаётся под обычной аутентификацией и правами: здесь описаны публичный API интеграций (ключи сервисных учётных записей)
и публичные ресурсы установки надстройки Outlook (манифест, пакет, сведения о TLS). Внутренние, административные и пользовательские методы не публикуются.
"""
from __future__ import annotations

import copy

from fastapi import APIRouter, Request, Response
from fastapi.openapi.utils import get_openapi

from ..publicapi.docs import build_spec, render_html

router = APIRouter(include_in_schema=False)
OUTLOOK_PUBLIC = {"/api/v1/outlook/config", "/api/v1/outlook/manifest.xml", "/api/v1/outlook/package", "/api/v1/outlook/center", "/api/v1/outlook/tls/{name}", "/api/v1/outlook/public/{code}"}
CSP = "default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'"


def merged_spec(request: Request) -> dict:
    spec = copy.deepcopy(build_spec(request))
    from .outlook import router as outlook_router  # noqa: PLC0415 — роутер берём напрямую: приложение подключает роутеры лениво

    routes = [r for r in outlook_router.routes if "/api/v1" + getattr(r, "path", "") in OUTLOOK_PUBLIC]
    if routes:
        ol = get_openapi(title="outlook", version="1", openapi_version="3.1.0", routes=routes)
        for path, item in ol["paths"].items():
            for op in item.values():
                op["tags"] = ["outlook"]
                op["x-required-scope"] = None
                op["security"] = []                                # публичные ресурсы установки: без входа и без ключа
                op["description"] = ("**Без авторизации.** " + (op.get("description") or "")).strip()
            spec["paths"]["/api/v1" + path] = item
        spec["components"].setdefault("schemas", {}).update(ol.get("components", {}).get("schemas", {}))
    spec["info"]["description"] = (spec["info"].get("description", "") + "\n\nТакже описаны публичные ресурсы установки надстройки Outlook (`/api/v1/outlook/…`): они доступны без входа. "
                                   "Остальные методы приложения требуют входа и здесь не публикуются.")
    return spec


@router.get("/api/docs")
async def docs(request: Request):
    return Response(render_html(merged_spec(request)), media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": CSP, "Cache-Control": "no-cache"})


@router.get("/api/docs/openapi.json")
async def docs_openapi(request: Request):
    return merged_spec(request)
