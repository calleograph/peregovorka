"""Health-эндпоинты ASR. Тяжёлого распознавания НЕ запускают."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def create_health_app(snapshot: Callable[[], dict], selftest: Callable[[], Awaitable[dict]] | None = None) -> FastAPI:
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

    return app
