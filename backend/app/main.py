"""Фабрика приложения FastAPI."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from redis.asyncio import Redis

from .api import admin, admin_asr, admin_journal, admin_system, admin_updates, auth, client, collab, guest, health, internal, meetings, moderation, room_manage, rooms, templates, ws
from .auth.directory import DirectoryClient, LdapDirectory
from .auth.service import AuthService
from .auth.guests import GuestSessionStore
from .auth.sessions import SessionStore
from .auth.throttle import LoginThrottle
from .config import Settings, get_settings
from .db import create_engine, create_session_maker
from .logging_setup import configure_logging, request_id_var
from .services.api_profiles import ProfileService
from .services.asr_bridge import AsrBridge
from .services.audit import set_journal_sink
from .services.chat_files import ChatFilesService
from .services.journal import Journal, run_journal_retention
from .security.secretbox import SecretBox, SecretBoxError
from .services.meetings import MeetingService
from .services.protocols import ProtocolService
from .services.settings import SettingsService
from .workers.asr_sync import run_asr_sync
from .workers.reaper import run_reaper
from .workers.retention import run_retention
from .workers.segment_consumer import run_consumer

log = logging.getLogger("app")
_RID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def create_app(
    settings: Settings | None = None,
    *,
    redis_factory: Callable[[Settings], Redis] | None = None,
    directory_factory: Callable[[Settings], DirectoryClient] | None = None,
    start_workers: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(settings)
        session_maker = create_session_maker(engine)
        redis = redis_factory(settings) if redis_factory else Redis.from_url(settings.effective_redis_url, decode_responses=True)
        directory = directory_factory(settings) if directory_factory else LdapDirectory(settings)
        bridge = AsrBridge(redis)
        meetings_svc = MeetingService(settings, redis, bridge)
        sessions = SessionStore(redis, settings)
        try:
            box = SecretBox(settings.app_master_key) if settings.app_master_key else None
        except SecretBoxError:
            log.error("APP_MASTER_KEY некорректен — секретные настройки недоступны")
            box = None
        settings_svc = SettingsService(box)
        journal = Journal(session_maker, settings_svc, settings.data_dir)
        profiles = ProfileService(settings_svc)
        protocols = ProtocolService(settings, session_maker, settings_svc, transports=getattr(app.state, "test_transports", None))
        meetings_svc.settings_svc = settings_svc
        protocols.journal = journal
        protocols.profiles = profiles
        meetings_svc.on_ended = lambda mid: protocols.spawn(protocols.finalize(mid), f"finalize-{mid}")

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_maker = session_maker
        app.state.redis = redis
        app.state.directory = directory
        app.state.bridge = bridge
        app.state.meetings = meetings_svc
        app.state.sessions = sessions
        app.state.guest_sessions = GuestSessionStore(redis)
        app.state.settings_svc = settings_svc
        app.state.protocols = protocols
        app.state.files = protocols.files
        protocols.chat_files = ChatFilesService(protocols.files, settings_svc, journal)
        app.state.chat_files = protocols.chat_files
        app.state.journal = journal
        app.state.profiles = profiles
        app.state.auth = AuthService(settings, directory, LoginThrottle(redis, settings), sessions)

        tasks: list[asyncio.Task] = []
        journal.start()
        set_journal_sink(journal.emit)
        if start_workers:
            tasks.append(asyncio.create_task(run_consumer(redis, session_maker, block_ms=settings.segment_consumer_block_ms), name="segment-consumer"))
            tasks.append(asyncio.create_task(run_reaper(session_maker, meetings_svc), name="meeting-reaper"))
            tasks.append(asyncio.create_task(run_retention(session_maker, protocols), name="retention"))
            tasks.append(asyncio.create_task(run_asr_sync(session_maker, settings_svc, redis), name="asr-model-sync"))
            tasks.append(asyncio.create_task(run_journal_retention(journal), name="journal-retention"))
        log.info("Приложение запущено", extra={"version": settings.app_version, "commit": settings.app_git_commit})
        journal.emit("system", "app_started", message=f"Версия {settings.app_version}, commit {settings.app_git_commit}")
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            await protocols.shutdown()
            await journal.stop()
            with contextlib.suppress(Exception):
                await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Peregovorka API", version=settings.app_version, lifespan=lifespan,
        docs_url="/api/v1/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json" if settings.docs_enabled else None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _RID_RE.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("Необработанная ошибка", extra={"path": request.url.path, "method": request.method})
            raise
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")  # ответы API (персональные данные, токены) не кэшируются
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/v1/health"):
            log.info("http", extra={"method": request.method, "path": request.url.path, "status": response.status_code,
                                    "ms": int((time.perf_counter() - started) * 1000), "request_id_echo": rid})
        return response

    prefix = "/api/v1"
    for r in (auth.router, rooms.router, meetings.router, collab.router, guest.router, templates.router, client.router, moderation.router, room_manage.router, admin.router, admin_system.router, admin_journal.router, admin_updates.router, admin_asr.router, health.router, ws.router):
        app.include_router(r, prefix=prefix)
    app.include_router(internal.router)
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
