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

from .services.avatars import AvatarStore
from .services.branding import BrandingStore
from .profiles.service import ProfileEnrichment
from .publicapi import ids as public_ids
from .publicapi.jobs import JobRunner
from .publicapi.requestlog import RequestLogWriter
from .publicapi.webhooks import WebhookService
from .publicapi.routes import router as public_router
from .api import media as media_api, storage_admin as storage_admin_api, site as site_api, admin, admin_bitrix, admin_public_api, admin_webhooks, admin_asr, admin_access, admin_mail, admin_storage_sync, delivery as delivery_api, meeting_settings, maps as maps_api, admin_journal, admin_llm, admin_sip, telephony, admin_system, admin_updates, auth, client, collab, guest, health, internal, profile, meetings, moderation, room_manage, rooms, templates, ws
from .auth.directory import DirectoryClient
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
from .services.events import EventHub
from .services.chat_files import ChatFilesService
from .services.journal import Journal, run_journal_retention
from .security.secretbox import SecretBox, SecretBoxError
from .services.meetings import MeetingService
from .services.conv_map import MapService
from .services.protocols import ProtocolService
from .services.ca_bundle import CaBundleService
from .services.audit import write_audit
from .services.mail import MailService
from .services.reconcile import Reconciler
from .services.mail_delivery import DeliveryService
from .services.ldap_profiles import LdapService, ProfileDirectory
from .services.legacy_ldap import LegacyLdapMigrator
from .services.sip import SipGateway, SipRouting, SipService
from .util_errors import describe_error
from .services.settings import SettingsService
from .workers.asr_sync import run_asr_sync
from .workers.mail_queue import run_mail_queue
from .workers.storage_sync import run_storage_sync
from .workers.reaper import run_reaper
from .workers.autoupdate import run_autoupdate
from .services.autoupdate import AutoUpdater
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
        ca = CaBundleService(settings.data_dir, settings.ldap_ca_file)
        ldap_service = LdapService(settings_svc, ca)
        directory = directory_factory(settings) if directory_factory else ProfileDirectory(settings, ldap_service)
        protocols = ProtocolService(settings, session_maker, settings_svc, transports=getattr(app.state, "test_transports", None))
        meetings_svc.settings_svc = settings_svc
        protocols.journal = journal
        protocols.profiles = profiles
        protocols.maps = MapService(protocols)
        protocols.maps.journal = journal
        app.state.maps = protocols.maps
        meetings_svc.on_ended = lambda mid: protocols.spawn(protocols.finalize(mid), f"finalize-{mid}")

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_maker = session_maker
        app.state.redis = redis
        app.state.event_hub = EventHub(redis)             # единый подписчик Redis для всех WebSocket (services/events.py)
        app.state.event_hub.start()
        app.state.directory = directory
        app.state.bridge = bridge
        app.state.meetings = meetings_svc
        from .services.stage import StageService  # noqa: PLC0415
        app.state.stage = StageService(redis, meetings_svc)                  # сцена ведущего (Spotlight) для всех участников встречи
        app.state.sessions = sessions
        app.state.guest_sessions = GuestSessionStore(redis)
        app.state.settings_svc = settings_svc
        app.state.protocols = protocols
        app.state.avatars = AvatarStore(settings.data_dir)
        app.state.branding = BrandingStore(settings.data_dir)
        app.state.enrichment = ProfileEnrichment(session_maker, settings_svc, app.state.avatars, journal, transports=getattr(app.state, "test_transports", None), ca_file=settings.ldap_ca_file or None)
        app.state.local_llm = protocols.local_llm
        app.state.api_log = RequestLogWriter(session_maker, settings_svc)
        app.state.api_log.start()
        # публичный API, этап 2: события (webhooks) и фоновые задачи; воркеры стартуют ниже вместе с остальными
        webhooks = WebhookService(session_maker, settings_svc, journal, transports=getattr(app.state, "test_transports", None), ca_file=settings.ldap_ca_file or None)
        app.state.webhooks = webhooks
        app.state.jobs = JobRunner(session_maker, settings_svc, protocols, webhooks, journal)

        def _meeting_event(event: str, m) -> None:
            r = m.room
            data = {"meeting_id": public_ids.pub("meeting", m.id), "room": {"id": public_ids.pub("room", r.id), "slug": r.slug, "name": r.name}, "started_at": m.started_at.isoformat()}
            if event == "meeting.ended":
                data.update(ended_at=m.ended_at.isoformat() if m.ended_at else None, end_reason=m.end_reason)
            webhooks.emit_safe(event, data, room_id=r.id)

        def _document_event(kind: str, doc_id, meeting_id, room_id, status: str, error) -> None:
            data = {"meeting_id": public_ids.pub("meeting", meeting_id), "kind": kind, "document_id": public_ids.pub("map" if kind == "map" else "protocol", doc_id)}
            if status != "ready":
                data["error"] = (error or "")[:300]
            webhooks.emit_safe("document.ready" if status == "ready" else "document.failed", data, room_id=room_id)

        meetings_svc.on_event = _meeting_event
        protocols.on_document = _document_event
        # SIP-телефония (LiveKit SIP): профили, шлюз к LiveKit API, исходящие звонки и маршрутизация входящих
        app.state.autoupdate = AutoUpdater(session_maker, settings_svc, redis, settings.data_dir, settings.app_version)
        app.state.sip = SipService(settings_svc)
        app.state.sip_gateway = SipGateway(settings)
        app.state.sip_routing = SipRouting(app.state.sip, app.state.sip_gateway, session_maker)
        app.state.files = protocols.files
        protocols.chat_files = ChatFilesService(protocols.files, settings_svc, journal)
        app.state.chat_files = protocols.chat_files
        app.state.journal = journal
        app.state.sip_routing.journal = journal
        meetings_svc.on_started = lambda mid: asyncio.get_running_loop().create_task(app.state.sip_routing.on_meeting_started(mid))
        meetings_svc.on_ended_extra = lambda mid: asyncio.get_running_loop().create_task(app.state.sip_routing.on_meeting_ended(mid))
        app.state.profiles = profiles
        mail = MailService(settings_svc, ca)
        delivery = DeliveryService(session_maker, settings_svc, mail, protocols, directory, settings.app_public_url, journal)
        protocols.after_finalize = delivery.run_auto
        async def _sync_audit(db, *, actor, action, target_id, details):
            await write_audit(db, actor_user_id=None, actor_name=actor, action=action, target_type="storage", target_id=target_id, details=details)

        reconciler = Reconciler(session_maker, protocols.files, settings.recordings_path, journal, _sync_audit)
        app.state.reconciler = reconciler
        from .services.storage_stats import StorageStats  # noqa: PLC0415
        from .services.transfer import TransferService  # noqa: PLC0415
        app.state.storage_stats = StorageStats(session_maker, protocols, redis)
        app.state.transfers = TransferService(session_maker, protocols, journal, _sync_audit)
        from .services.waveforms import WaveformService  # noqa: PLC0415
        protocols.waveforms = app.state.waveforms = WaveformService(session_maker, protocols)
        app.state.mail = mail
        app.state.delivery = delivery
        app.state.ca = ca
        app.state.ldap = ldap_service
        app.state.auth = AuthService(settings, directory, LoginThrottle(redis, settings), sessions)
        app.state.auth.settings_svc = settings_svc

        # Набор CA и подключения к каталогу из базы. Два РАЗНЫХ шага: сбой перестройки набора CA (например, нет прав на каталог /data/ca)
        # не должен лишать приложение каталога — иначе вход по домену молча превращается в «not_configured» (реальный сбой при обновлении до 0.3.0).
        # Причина сбоя пишется в журнал безопасным описанием (класс ошибки, ошибка ОС, путь) — без секретов.
        app.state.boot_errors = {}
        legacy = LegacyLdapMigrator(settings, ldap_service, ca, settings_svc)
        app.state.legacy_ldap = legacy
        try:
            async with session_maker() as boot_db:
                try:
                    await ca.rebuild(boot_db)
                except Exception as exc:  # noqa: BLE001
                    app.state.boot_errors["ca"] = describe_error(exc)
                    log.error("Не удалось собрать набор CA: %s (проверьте права на каталог данных; администратору: Администрирование → Состояние системы → «Исправить автоматически»)",
                              app.state.boot_errors["ca"], extra={"error": "boot_ca_failed"})
                try:
                    if hasattr(directory, "reload"):
                        await directory.reload(boot_db)
                except Exception as exc:  # noqa: BLE001
                    app.state.boot_errors["directory"] = describe_error(exc)
                    log.error("Не удалось загрузить подключения к каталогу: %s", app.state.boot_errors["directory"], extra={"error": "boot_directory_failed"})
        except Exception as exc:  # noqa: BLE001
            app.state.boot_errors["database"] = describe_error(exc)
            log.warning("Не удалось загрузить подключения к каталогу: база недоступна (%s; при первом запуске до миграций это нормально)", app.state.boot_errors["database"],
                        extra={"error": "boot_load_failed"})
        tasks: list[asyncio.Task] = []
        journal.start()
        set_journal_sink(journal.emit)
        if start_workers:
            tasks.append(asyncio.create_task(legacy.auto_import(session_maker, redis, directory), name="legacy-ldap-import"))
            tasks.append(asyncio.create_task(run_consumer(redis, session_maker, block_ms=settings.segment_consumer_block_ms), name="segment-consumer"))
            tasks.append(asyncio.create_task(run_reaper(session_maker, meetings_svc), name="meeting-reaper"))
            tasks.append(asyncio.create_task(run_autoupdate(app.state.autoupdate), name="auto-update"))
            tasks.append(asyncio.create_task(run_retention(session_maker, protocols), name="retention"))
            tasks.append(asyncio.create_task(run_asr_sync(session_maker, settings_svc, redis), name="asr-model-sync"))
            tasks.append(asyncio.create_task(run_journal_retention(journal), name="journal-retention"))
            tasks.append(asyncio.create_task(run_mail_queue(session_maker, delivery), name="mail-queue"))
            webhooks.start()
            app.state.jobs.start()
            tasks.append(asyncio.create_task(run_storage_sync(session_maker, settings_svc, reconciler, redis), name="storage-sync"))
            tasks.append(asyncio.create_task(app.state.storage_stats.run(), name="storage-stats"))
            tasks.append(asyncio.create_task(app.state.transfers.run(), name="storage-transfer"))
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
            await app.state.event_hub.stop()
            await protocols.shutdown()
            await app.state.jobs.stop()
            await app.state.webhooks.stop()
            await app.state.api_log.stop()
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
    for r in (site_api.router, media_api.router, storage_admin_api.router, auth.router, admin_bitrix.router, admin_public_api.router, admin_webhooks.router, profile.router, rooms.router, meetings.router, collab.router, guest.router, templates.router, client.router, moderation.router, room_manage.router, admin.router, admin_access.router, admin_mail.router, admin_storage_sync.router, delivery_api.router, delivery_api.templates_router, meeting_settings.router, maps_api.router, admin_system.router, admin_llm.router, admin_sip.router, telephony.router, admin_journal.router, admin_updates.router, admin_asr.router, health.router, ws.router):
        app.include_router(r, prefix=prefix)
    app.include_router(internal.router)
    app.include_router(public_router)     # публичный API: /api/public/v1 (свой формат ошибок, ключи вместо cookie-сессии)
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
