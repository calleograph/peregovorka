"""Health-эндпоинты ASR и управление моделями. Тяжёлого распознавания health НЕ запускает."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse


def create_health_app(snapshot: Callable[[], dict], selftest: Callable[[], Awaitable[dict]] | None = None, *,
                      models: Any = None, authorized: Callable[[str], bool] = lambda _t: True) -> FastAPI:
    app = FastAPI(title="asr-health", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz():
        """Процесс жив (используется docker healthcheck, не зависит от загрузки модели)."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        """Готовность: модель загружена, redis доступен. 503, пока не готов."""
        snap = snapshot()
        ready = bool(snap.get("model_loaded")) and bool(snap.get("redis_ok", True))
        return JSONResponse({"ready": ready, **snap}, status_code=200 if ready else 503)

    @app.post("/selftest")
    async def run_selftest():
        """Однократная проверка инференса на синтетическом аудио (1 с тишины). НЕ healthcheck: вызывается вручную/smoke-тестом."""
        if selftest is None or not snapshot().get("model_loaded"):
            return JSONResponse({"ok": False, "error": "model_not_ready"}, status_code=503)
        try:
            return await selftest()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": type(exc).__name__}, status_code=500)

    if models is not None:
        def guard(token: str) -> JSONResponse | None:
            return None if authorized(token) else JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

        @app.get("/models")
        async def list_models():
            """Каталог моделей: статус, runtime, размер, устройство, активная (без секретов)."""
            return models.status()

        @app.post("/models/activate")
        async def activate(request: Request, x_internal_token: str = Header(default="")):
            if (r := guard(x_internal_token)) is not None:
                return r
            body = await request.json()
            res = await models.activate(str(body.get("model_id", "")))
            return JSONResponse(res, status_code=200 if res.get("ok") else 409)

        @app.post("/models/test")
        async def test_model(request: Request, x_internal_token: str = Header(default="")):
            if (r := guard(x_internal_token)) is not None:
                return r
            body = await request.json()
            res = await models.run_test(body.get("model_id"), repeat=int(body.get("repeat", 3)), force=bool(body.get("force")))
            return JSONResponse(res, status_code=200 if res.get("ok") or res.get("error") else 500)

        @app.post("/models/compare")
        async def compare(request: Request, x_internal_token: str = Header(default="")):
            if (r := guard(x_internal_token)) is not None:
                return r
            body = await request.json()
            res = await models.compare(body.get("model_ids"), repeat=int(body.get("repeat", 3)), force=bool(body.get("force")))
            return JSONResponse(res, status_code=200 if res.get("ok") else 409)

    return app
