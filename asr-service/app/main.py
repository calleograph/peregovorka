"""Точка входа ASR-сервиса: python -m app.main"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import uvicorn
from redis.asyncio import Redis

from .audio.vad import silero_factory
from .config import AsrSettings
from .health import create_health_app
from .inference import InferenceQueue
from .logging_setup import configure_logging
from .manager import SessionManager
from .providers.factory import build_provider
from .publisher import SegmentPublisher, heartbeat_loop

log = logging.getLogger("asr")


def _livekit_sdk() -> str | None:
    try:
        from importlib.metadata import version

        return version("livekit")
    except Exception:  # noqa: BLE001
        return None


async def amain() -> None:
    settings = AsrSettings()
    configure_logging(settings.log_level, settings.log_format)
    provider = build_provider(settings)
    redis = Redis.from_url(settings.effective_redis_url, decode_responses=True)
    queue = InferenceQueue(provider, max_concurrent=settings.asr_max_concurrent_inference,
                           queue_size=settings.asr_queue_size, language=settings.asr_language)
    publisher = SegmentPublisher(redis, lambda: provider.info)
    manager = SessionManager(settings, redis, queue, publisher, silero_factory())
    state = {"model_error": None}

    def snapshot() -> dict:
        return {
            "model_loaded": provider.is_ready(), "model_error": state["model_error"],
            "provider": provider.info.as_dict(), "queue_depth": queue.depth, "queue_capacity": settings.asr_queue_size,
            "inference_busy": queue.busy, "max_concurrent": settings.asr_max_concurrent_inference,
            "processed": queue.processed, "dropped": queue.dropped, "errors": queue.errors, **queue.latency_stats(),
            "torch_threads": getattr(provider, "threads", {}).get("intra"), "torch_interop_threads": getattr(provider, "threads", {}).get("interop"),
            "livekit_sdk": _livekit_sdk(),
            "active_meetings": manager.active_meetings, "version": settings.app_version, "commit": settings.app_git_commit, "built_at": settings.app_built_at,
        }

    async def selftest() -> dict:
        import time as _t

        import numpy as _np

        started = _t.monotonic()
        res = await asyncio.to_thread(provider.transcribe, _np.zeros(16000, dtype=_np.int16), language=settings.asr_language)
        return {"ok": True, "ms": int((_t.monotonic() - started) * 1000), "provider": provider.info.as_dict(), "text_len": len(res.text)}

    # health-сервер поднимаем СРАЗУ: /healthz отвечает, пока модель ещё грузится.
    server = uvicorn.Server(uvicorn.Config(create_health_app(snapshot, selftest), host="0.0.0.0", port=settings.health_port,
                                           log_level="warning", access_log=False))
    health_task = asyncio.create_task(server.serve(), name="health")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    tasks: list[asyncio.Task] = []
    try:
        try:
            await asyncio.to_thread(provider.load)  # модель — один раз на процесс
        except Exception as exc:  # noqa: BLE001
            state["model_error"] = f"{type(exc).__name__}: {exc}"
            log.error("Модель не загружена — сервис остаётся not-ready", extra={"error": state["model_error"]})
            await stop.wait()
            return
        queue.start()
        tasks.append(asyncio.create_task(heartbeat_loop(redis, snapshot), name="heartbeat"))
        tasks.append(asyncio.create_task(manager.run(), name="manager"))
        log.info("ASR-сервис готов", extra={"model": provider.info.name, "device": provider.info.device})
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
