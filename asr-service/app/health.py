"""Health-эндпоинты ASR. Тяжёлого распознавания НЕ запускают."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def create_health_app(snapshot: Callable[[], dict]) -> FastAPI:
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

    return app
