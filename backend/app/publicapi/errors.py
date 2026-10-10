"""Единый формат ошибок публичного API: {"error": {"code", "message", "request_id", ...}}. Трассировки и внутренние детали наружу не выдаются.

Публичные маршруты живут в том же приложении, что и интерфейс (общее состояние, миграции, воркеры), поэтому формат ошибок задаётся не глобальными
обработчиками (они поменяли бы ответы внутреннего API), а классом маршрута: он ловит любые исключения только своих маршрутов.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..logging_setup import request_id_var

log = logging.getLogger("app")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, headers: dict[str, str] | None = None, extra: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers, self.extra = status, code, message, headers or {}, extra or {}


def not_found(what: str = "Объект") -> ApiError:
    return ApiError(404, "not_found", f"{what} не найден.")


_DEFAULT_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed", 409: "conflict", 410: "gone",
                  413: "payload_too_large", 415: "unsupported_media_type", 422: "validation_error", 429: "rate_limited", 503: "unavailable"}


def body(code: str, message: str, extra: dict | None = None) -> dict:
    rid = request_id_var.get()
    return {"error": {"code": code, "message": message, "request_id": None if rid == "-" else rid, **(extra or {})}}


def _response(status: int, code: str, message: str, extra: dict | None = None, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body(code, message, extra), status_code=status, headers=headers)


class PublicRoute(APIRoute):
    """Маршрут публичного API: единый формат ошибок, заголовки ограничения частоты и запись в журнал обращений."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()
        template = self.path

        async def handler(request: Request) -> Response:
            started = time.perf_counter()
            code = None
            try:
                response = await original(request)
            except ApiError as exc:
                code, response = exc.code, _response(exc.status, exc.code, exc.message, exc.extra, exc.headers)
            except RequestValidationError as exc:
                fields = [{"field": ".".join(str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path")), "problem": str(e.get("msg", ""))[:160]} for e in exc.errors()[:20]]
                code, response = "validation_error", _response(422, "validation_error", "Параметры запроса некорректны.", {"fields": fields})
            except (HTTPException, StarletteHTTPException) as exc:
                code = _DEFAULT_CODES.get(exc.status_code, "error")
                response = _response(exc.status_code, code, str(exc.detail) if isinstance(exc.detail, str) else "Ошибка запроса.", headers=getattr(exc, "headers", None))
            except Exception:  # noqa: BLE001 — наружу только идентификатор запроса, подробности в журнале сервера
                log.exception("Необработанная ошибка публичного API", extra={"path": template})
                code, response = "internal_error", _response(500, "internal_error", "Внутренняя ошибка. Сообщите администратору идентификатор запроса.")
            rl = getattr(request.state, "rate_headers", None)
            if rl:
                for k, v in rl.items():
                    response.headers.setdefault(k, v)
            writer = getattr(request.app.state, "api_log", None)
            if writer is not None:
                p = getattr(request.state, "principal", None)
                from ..auth.deps import client_ip  # noqa: PLC0415

                writer.add(client_id=p.client_id if p else None, key_id=p.key_id if p else None, method=request.method, path=template, status=response.status_code,
                           ms=int((time.perf_counter() - started) * 1000), ip=client_ip(request), error_code=code)
            return response

        return handler
