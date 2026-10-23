from __future__ import annotations
from ..services import asr_state

import asyncio
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live():
    """Процесс жив. Внешних зависимостей не проверяет."""
    return {"status": "ok"}


async def _postgres(app) -> dict:
    """PostgreSQL проверяется через ОТДЕЛЬНЫЙ крошечный пул: исчерпание основного пула не маскируется под «база жива», а недоступность базы не маскируется под «процесс здоров».
    Состояние: ok; saturated — база отвечает, но пул запросов занят более чем на 90 % (идёт деградация); idle_in_transaction — есть долго удерживаемые транзакции."""
    from ..services import db_health  # noqa: PLC0415

    he = getattr(app.state, "health_engine", None)
    try:
        if he is not None:
            async with he.connect() as c:
                await asyncio.wait_for(c.execute(text("SELECT 1")), 4)
        else:
            async with app.state.session_maker() as db:
                await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    pools = {k: db_health.pool_info(e) for k, e in getattr(app.state, "db_stats", db_health.DbStats()).engines.items()}
    sat = max((p.get("saturation", 0) for p in pools.values()), default=0)
    return {"ok": True, "saturated": sat >= 0.9, "pools": pools}


@router.get("/health/db")
async def db_ready(request: Request):
    """Готовность ядра: PostgreSQL и Redis (то, без чего не работают вход, комнаты, чат, история). Для проверки контейнера: «процесс жив» (/health/live) не значит «приложение работает»."""
    app = request.app
    pg = await _postgres(app)
    try:
        await app.state.redis.ping()
        rd = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        rd = {"ok": False, "error": type(exc).__name__}
    ok = pg["ok"] and rd["ok"]
    return JSONResponse({"status": ("degraded" if pg.get("saturated") else "ready") if ok else "unavailable", "postgres": pg, "redis": rd}, status_code=200 if ok else 503)


READY_TTL_S = 2.0      # результат общий для всех вызовов за это время


@router.get("/health/ready")
async def ready(request: Request):
    """PostgreSQL, Redis и LiveKit обязательны (503 при сбое). ASR необязателен: без него
    комнаты работают, нет только транскрипции — тогда статус degraded (200).
    Страница комнаты опрашивает этот адрес у каждого участника (готовность ASR): проверка выполняется не чаще раза в READY_TTL_S на процесс,
    пока проверка идёт, остальные получают прежний результат (ждёт только самый первый вызов) — иначе тысячи участников давали бы сотни обращений
    в секунду к LiveKit и служебному пулу базы, а медленная проверка задерживала бы всех."""
    app = request.app
    st = app.state
    if not hasattr(st, "ready_lock"):
        st.ready_lock, st.ready_cache = asyncio.Lock(), None
    stale = st.ready_cache is None or time.monotonic() - st.ready_cache[0] >= READY_TTL_S
    if stale and not (st.ready_lock.locked() and st.ready_cache is not None):      # проверка уже идёт — отдаём прежний результат, а не ждём её
        async with st.ready_lock:
            if st.ready_cache is None or time.monotonic() - st.ready_cache[0] >= READY_TTL_S:
                st.ready_cache = (time.monotonic(), *await _ready_check(app))
    _, body, code = st.ready_cache
    return JSONResponse(body, status_code=code)


async def _ready_check(app) -> tuple[dict, int]:
    checks: dict[str, dict] = {}

    checks["postgres"] = await _postgres(app)

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
    checks["asr"] = {**asr_state.state(asr), "queue_depth": (asr or {}).get("queue_depth"),
                     "active_meetings": (asr or {}).get("active_meetings")}

    required_ok = all(checks[k]["ok"] for k in ("postgres", "redis", "livekit"))
    degraded = (not checks["asr"]["ok"]) or bool(checks["postgres"].get("saturated"))
    status = "ready" if required_ok and not degraded else ("degraded" if required_ok else "unavailable")
    return {"status": status, "checks": checks}, 200 if required_ok else 503


@router.get("/public/privacy")
async def public_privacy(request: Request):
    """Тексты о cookie и обработке данных для страницы входа и страницы «Обработка данных» — без входа в систему. Только то, что администратор сам разместил для всех."""
    async with request.app.state.session_maker() as db:
        cfg = await request.app.state.settings_svc.get(db, "privacy")
    d = cfg.model_dump()                                           # type: ignore[attr-defined]
    return JSONResponse(d, headers={"Cache-Control": "public, max-age=60"})


@router.get("/version")
async def version(request: Request):
    s = request.app.state.settings
    return {"version": s.app_version, "commit": s.app_git_commit, "built_at": s.app_built_at}
