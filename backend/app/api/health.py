from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live():
    """Процесс жив. Внешних зависимостей не проверяет."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request):
    """PostgreSQL, Redis и LiveKit обязательны (503 при сбое). ASR необязателен: без него
    комнаты работают, нет только транскрипции — тогда статус degraded (200)."""
    app = request.app
    checks: dict[str, dict] = {}

    try:
        async with app.state.session_maker() as db:
            await db.execute(text("SELECT 1"))
        checks["postgres"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = {"ok": False, "error": type(exc).__name__}

    try:
        await app.state.redis.ping()
        checks["redis"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = {"ok": False, "error": type(exc).__name__}

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(app.state.settings.livekit_http_url + "/")
        checks["livekit"] = {"ok": r.status_code == 200}
    except Exception as exc:  # noqa: BLE001
        checks["livekit"] = {"ok": False, "error": type(exc).__name__}

    asr = None
    if checks["redis"]["ok"]:
        try:
            asr = await app.state.bridge.heartbeat()
        except Exception:  # noqa: BLE001
            asr = None
    checks["asr"] = {"ok": bool(asr and asr.get("model_loaded")), "queue_depth": (asr or {}).get("queue_depth"),
                     "active_meetings": (asr or {}).get("active_meetings")}

    required_ok = all(checks[k]["ok"] for k in ("postgres", "redis", "livekit"))
    status = "ready" if required_ok and checks["asr"]["ok"] else ("degraded" if required_ok else "unavailable")
    return JSONResponse({"status": status, "checks": checks}, status_code=200 if required_ok else 503)


@router.get("/version")
async def version(request: Request):
    s = request.app.state.settings
    return {"version": s.app_version, "commit": s.app_git_commit}
