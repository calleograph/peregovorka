"""Точка входа ASR-сервиса: python -m app.main"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import signal

import uvicorn
from redis.asyncio import Redis

from .audio.vad import silero_factory
from .catalog import load_catalog
from .config import AsrSettings
from .health import create_health_app
from .inference import InferenceQueue
from .logging_setup import configure_logging
from .manager import SessionManager
from .model_manager import ModelManager
from .publisher import SegmentPublisher, heartbeat_loop

log = logging.getLogger("asr")
DESIRED_MODEL_KEY = "asr:desired_model"  # желаемая модель из админки (пишет backend); ASR применяет её сам


def _livekit_sdk() -> str | None:
    try:
        from importlib.metadata import version

        return version("livekit")
    except Exception:  # noqa: BLE001
        return None


async def watch_desired_model(redis: Redis, models: ModelManager, interval: float = 5.0) -> None:
    """Применяет выбор администратора: backend пишет желаемую модель в Redis, ASR переключается сам.
    Неудавшийся выбор повторно не загружается, пока выбор не изменится (иначе — цикл неудачных загрузок)."""
    while True:
        try:
            want = await redis.get(DESIRED_MODEL_KEY)
            if want and want != models.active_id and want != models.failed_desired and models.loading_id is None:
                await models.activate(want)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("Ошибка применения выбранной модели", exc_info=True)
        await asyncio.sleep(interval)


async def amain() -> None:
    settings = AsrSettings()
    configure_logging(settings.log_level, settings.log_format)
    redis = Redis.from_url(settings.effective_redis_url, decode_responses=True)
    holder: dict = {}

    def busy_reason() -> str | None:
        m = holder.get("manager")
        return "Идёт встреча: тест нагрузит распознавание и исказит цифры. Повторите после встречи." if m and m.active_meetings else None

    models = ModelManager(settings, load_catalog(settings.asr_model_dir, settings.asr_model_name), busy_reason=busy_reason)
    queue = InferenceQueue(models, max_concurrent=settings.asr_max_concurrent_inference, queue_size=settings.asr_queue_size, language=settings.asr_language)
    publisher = SegmentPublisher(redis, lambda: models.info)
    manager = SessionManager(settings, redis, queue, publisher, silero_factory())
    holder["manager"] = manager

    def snapshot() -> dict:
        st = models.status()
        return {
            "model_loaded": models.is_ready(), "model_error": next((m["error"] for m in st["models"] if m["status"] == "error" and m["error"]), None),
            "provider": models.info.as_dict(), "active_model": st["active_id"], "loading_model": st["loading_id"],
            "models": [{k: m[k] for k in ("id", "title", "runtime", "quant", "status", "present", "size_bytes", "error")} for m in st["models"]],
            "queue_depth": queue.depth, "queue_capacity": settings.asr_queue_size,
            "inference_busy": queue.busy, "max_concurrent": settings.asr_max_concurrent_inference,
            "processed": queue.processed, "dropped": queue.dropped, "errors": queue.errors, **queue.latency_stats(),
            "torch_threads": models.threads.get("intra"), "torch_interop_threads": models.threads.get("interop"), "livekit_sdk": _livekit_sdk(),
            "active_meetings": manager.active_meetings, "version": settings.app_version, "commit": settings.app_git_commit, "built_at": settings.app_built_at,
        }

    async def selftest() -> dict:
        import time as _t

        import numpy as _np

        started = _t.monotonic()
        res = await asyncio.to_thread(models.transcribe, _np.zeros(16000, dtype=_np.int16), language=settings.asr_language)
        return {"ok": True, "ms": int((_t.monotonic() - started) * 1000), "provider": models.info.as_dict(), "text_len": len(res.text)}

    def authorized(token: str) -> bool:
        return not settings.internal_api_token or hmac.compare_digest(token, settings.internal_api_token)

    # health-сервер поднимаем СРАЗУ: /healthz отвечает, пока модель ещё грузится.
    server = uvicorn.Server(uvicorn.Config(create_health_app(snapshot, selftest, models=models, authorized=authorized), host="0.0.0.0",
                                           port=settings.health_port, log_level="warning", access_log=False))
    health_task = asyncio.create_task(server.serve(), name="health")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    tasks: list[asyncio.Task] = []
    try:
        wanted = await redis.get(DESIRED_MODEL_KEY) or settings.asr_model_id
        res = await models.activate(wanted)  # ошибка загрузки НЕ останавливает сервис: админ увидит причину и сможет выбрать другую модель
        if not res.get("ok"):
            log.error("Стартовая модель не загружена — сервис остаётся not-ready, выбор модели доступен в админке", extra={"error": res.get("error"), "model": wanted})
        queue.start()
        tasks.append(asyncio.create_task(heartbeat_loop(redis, snapshot), name="heartbeat"))
        tasks.append(asyncio.create_task(watch_desired_model(redis, models), name="model-watch"))
        tasks.append(asyncio.create_task(manager.run(), name="manager"))
        log.info("ASR-сервис запущен", extra={"model": models.active_id, "ready": models.is_ready(), "device": settings.asr_device})
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await manager.stop_all()
        with contextlib.suppress(Exception):
            await queue.stop()
        with contextlib.suppress(Exception):
            await redis.delete("asr:heartbeat")
            await redis.aclose()
        server.should_exit = True
        with contextlib.suppress(Exception):
            await health_task


if __name__ == "__main__":
    asyncio.run(amain())
