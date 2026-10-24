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
from starlette.datastructures import MutableHeaders

from .services.avatars import AvatarStore
from .services.room_icons import RoomIconStore
from .services.branding import BrandingStore
from .profiles.service import ProfileEnrichment
from .publicapi import ids as public_ids
from .publicapi.jobs import JobRunner
from .publicapi.requestlog import RequestLogWriter
from .publicapi.webhooks import WebhookService
from .publicapi.routes import router as public_router
from .api import chats as chats_api, dictation as dictation_api, meeting_access, room_icons as room_icons_api, room_journal, api_docs, config_backup as config_backup_api, media as media_api, storage_admin as storage_admin_api, site as site_api, admin, admin_bitrix, admin_public_api, admin_webhooks, admin_asr, admin_access, admin_mail, admin_storage_sync, admin_auto_backup, admin_crypto, auth_totp, outlook as outlook_api, admin_outlook, peer_rooms as peer_rooms_api, captcha as captcha_api, delivery as delivery_api, meeting_settings, maps as maps_api, admin_journal, admin_llm, admin_sip, telephony, admin_system, admin_performance, admin_updates, auth, client, collab, guest, health, internal, profile, meetings, moderation, room_manage, rooms, templates, ws
from .auth.deps import client_ip
from .auth.directory import DirectoryClient
from .auth.service import AuthService
from .auth.guests import GuestSessionStore
from .auth.sessions import SessionStore
from .auth.throttle import LoginThrottle
from .config import Settings, get_settings
from .db import create_engine, create_health_engine, create_session_maker, pool_plan
from .services import db_health
from .services.leader import Leader
from .logging_setup import configure_logging, request_id_var
from .services.api_profiles import ProfileService
from .services.asr_bridge import AsrBridge
from .services.audit import audit_ctx, session_fingerprint, set_audit_key, set_journal_sink
from .services.events import EventHub
from .services.perf import PerfHub
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
from .services.outlook_sync import SyncRunner
from .util_errors import describe_error
from .services.settings import SettingsService
from .workers.asr_sync import run_asr_sync
from .workers.mail_queue import run_mail_queue
from .workers.storage_sync import run_storage_sync
from .workers.reaper import run_reaper
from .workers.autoupdate import run_autoupdate
from .services.auto_backup import AutoBackup, run_auto_backup
from .services.autoupdate import AutoUpdater
from .workers.retention import run_retention
from .workers.segment_consumer import run_consumer

log = logging.getLogger("app")
_RID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def make_redis(settings: Settings) -> Redis:
    """Клиент Redis с ОЖИДАЮЩИМ пулом: при пике запросов команды ждут свободное соединение (до `db_pool_timeout_s`), а не падают с MaxConnectionsError (раньше это были HTTP 500).
    Обрыв соединения (перезапуск или переключение Redis) — до 6 повторов с паузой 0,1→1 с: перезапуск проходит без ошибок у пользователей (замер: 0 ошибок против
    ~500 на 10 тыс. команд без повторов). Дольше — ответ 503 (redis_unavailable)."""
    from redis.asyncio import BlockingConnectionPool  # noqa: PLC0415
    from redis.asyncio.retry import Retry  # noqa: PLC0415
    from redis.backoff import ExponentialBackoff  # noqa: PLC0415
    from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError  # noqa: PLC0415

    plan = pool_plan(settings, background=False)
    limit = settings.redis_max_connections or max(200, 3 * (plan["pool_size"] + plan["max_overflow"]))
    return Redis(connection_pool=BlockingConnectionPool.from_url(settings.effective_redis_url, max_connections=limit, timeout=settings.db_pool_timeout_s, decode_responses=True,
                                                               retry=Retry(ExponentialBackoff(cap=1.0, base=0.1), 6),
                                                               retry_on_error=[RedisConnectionError, RedisTimeoutError]))


class RequestContext:
    """Идентификатор запроса, контекст журнала аудита и базы, заголовки безопасности, строка журнала на каждый запрос API.
    Чистый ASGI, а не @app.middleware("http"): тот запускает обработчик отдельной задачей с потоками памяти — заметная доля цены лёгкого запроса."""

    def __init__(self, app, settings: Settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = scope["path"]
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _RID_RE.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        ctx_token = audit_ctx.set({"ip": client_ip(request), "user_agent": (request.headers.get("user-agent") or "")[:300] or None,
                                   "session": session_fingerprint(request.cookies.get(self.settings.cookie_name))})
        started = time.perf_counter()
        db_ctx = db_health.db_context.set(f"{scope['method']} {path}")
        status = 0

        async def send_with_headers(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                h = MutableHeaders(scope=message)
                h["X-Request-ID"] = rid
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("Referrer-Policy", "same-origin")
                if path.startswith("/api/"):
                    h.setdefault("Cache-Control", "no-store")  # ответы API (персональные данные, токены) не кэшируются
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            log.exception("Необработанная ошибка", extra={"path": path, "method": scope["method"]})
            raise
        finally:
            request_id_var.reset(token)
            audit_ctx.reset(ctx_token)
            db_health.db_context.reset(db_ctx)
        if path.startswith("/api/") and not path.startswith("/api/v1/health"):
            log.info("http", extra={"method": scope["method"], "path": path, "status": status,
                                    "ms": int((time.perf_counter() - started) * 1000), "request_id_echo": rid})


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
        engine = create_engine(settings)                              # запросы пользователей
        sqlite = str(settings.sqlalchemy_url).startswith("sqlite")
        bg_engine = engine if sqlite else create_engine(settings, background=True)       # фоновые задачи (LLM, экспорт, карты, рассылка): свой пул, чужие запросы не вытесняет
        health_engine = create_health_engine(settings)               # крошечный пул проверок готовности и статистики
        if not sqlite and (note := pool_plan(settings, background=False)["note"]):
            log.warning(note, extra={"error": "db_pool_over_limit"})
        db_stats = db_health.DbStats()
        db_health.install(engine, "api", db_stats, settings.db_hold_warn_s)
        if bg_engine is not engine:
            db_health.install(bg_engine, "background", db_stats, settings.db_hold_warn_s)
        session_maker = create_session_maker(engine)
        bg_sm = create_session_maker(bg_engine)
        redis = redis_factory(settings) if redis_factory else make_redis(settings)
        bridge = AsrBridge(redis)
        meetings_svc = MeetingService(settings, redis, bridge)
        sessions = SessionStore(redis, settings)
        try:
            box = SecretBox(settings.app_master_key) if settings.app_master_key else None
        except SecretBoxError:
            log.error("APP_MASTER_KEY некорректен — секретные настройки недоступны")
            box = None
        settings_svc = SettingsService(box)
        journal = Journal(bg_sm, settings_svc, settings.data_dir)
        profiles = ProfileService(settings_svc)
        ca = CaBundleService(settings.data_dir, settings.ldap_ca_file)
        ldap_service = LdapService(settings_svc, ca)
        directory = directory_factory(settings) if directory_factory else ProfileDirectory(settings, ldap_service)
        protocols = ProtocolService(settings, bg_sm, settings_svc, transports=getattr(app.state, "test_transports", None))
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
        app.state.bg_session_maker = bg_sm
        app.state.db_stats = db_stats
        app.state.health_engine = health_engine
        app.state.redis = redis
        protocols.redis = redis                           # реестр живых заданий (протоколы, карты): ход, отмена и «сироты» — во всех процессах
        app.state.event_hub = EventHub(redis)             # единый подписчик Redis для всех WebSocket (services/events.py)
        app.state.event_hub.start()
        app.state.perf = PerfHub(settings, redis, session_maker)          # показатели нагрузки и очередь тяжёлых фоновых задач (services/perf.py)
        app.state.directory = directory
        app.state.bridge = bridge
        app.state.meetings = meetings_svc
        from .services.stage import StageService  # noqa: PLC0415
        app.state.stage = StageService(redis, meetings_svc)                  # сцена ведущего (Spotlight) для всех участников встречи
        app.state.sessions = sessions
        app.state.guest_sessions = GuestSessionStore(redis)
        app.state.settings_svc = settings_svc
        app.state.protocols = protocols
        protocols.gate = app.state.perf.gate
        app.state.avatars = AvatarStore(settings.data_dir)
        app.state.room_icons = RoomIconStore(settings.data_dir)
        app.state.branding = BrandingStore(settings.data_dir)
        app.state.enrichment = ProfileEnrichment(bg_sm, settings_svc, app.state.avatars, journal, transports=getattr(app.state, "test_transports", None), ca_file=settings.ldap_ca_file or None)
        app.state.local_llm = protocols.local_llm
        app.state.api_log = RequestLogWriter(bg_sm, settings_svc)
        app.state.api_log.start()
        # публичный API, этап 2: события (webhooks) и фоновые задачи; воркеры стартуют ниже вместе с остальными
        webhooks = WebhookService(bg_sm, settings_svc, journal, transports=getattr(app.state, "test_transports", None), ca_file=settings.ldap_ca_file or None)
        app.state.webhooks = webhooks
        app.state.jobs = JobRunner(bg_sm, settings_svc, protocols, webhooks, journal)

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
        app.state.auto_backup = AutoBackup(app)
        app.state.autoupdate = AutoUpdater(bg_sm, settings_svc, redis, settings.data_dir, settings.app_version)
        app.state.sip = SipService(settings_svc)
        app.state.outlook_sync = SyncRunner(settings_svc, journal, getattr(app.state, "test_transports", {}).get("outlook_sync"))
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
        delivery = DeliveryService(bg_sm, settings_svc, mail, protocols, directory, settings.app_public_url, journal)
        delivery.redis = redis
        protocols.after_finalize = delivery.run_auto
        async def _sync_audit(db, *, actor, action, target_id, details):
            await write_audit(db, actor_user_id=None, actor_name=actor, action=action, target_type="storage", target_id=target_id, details=details)

        reconciler = Reconciler(bg_sm, protocols.files, settings.recordings_path, journal, _sync_audit)
        app.state.reconciler = reconciler
        from .services.storage_stats import StorageStats  # noqa: PLC0415
        from .services.transfer import TransferService  # noqa: PLC0415
        app.state.storage_stats = StorageStats(bg_sm, protocols, redis)
        app.state.transfers = TransferService(bg_sm, protocols, journal, _sync_audit)
        from .services.waveforms import WaveformService  # noqa: PLC0415
        protocols.waveforms = app.state.waveforms = WaveformService(bg_sm, protocols)
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
        try:
            await protocols.recover_orphans()            # документы «в работе» после перезапуска сервера закрываются понятным состоянием
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось закрыть прерванные формирования документов (%s)", describe_error(exc))
        tasks: list[asyncio.Task] = []
        journal.start()
        set_journal_sink(journal.emit)
        set_audit_key(settings.app_master_key)
        from .services import storage_encryption as _se  # noqa: PLC0415

        try:
            app.state.storage_keys_loaded = await _se.init(session_maker, settings.app_master_key)       # ключи шифрования хранилища: загрузить (или создать первый)
        except Exception as exc:  # noqa: BLE001 — база может быть ещё не обновлена: приложение запускается, шифрование недоступно до миграции
            log.warning("Ключи шифрования хранилища не загружены (%s)", type(exc).__name__)
        from .services import totp as _totp  # noqa: PLC0415

        _totp.set_pepper(settings.app_master_key)
        from .services import captcha as _captcha  # noqa: PLC0415

        _captcha.set_captcha_key(settings.app_master_key)
        if start_workers:
            tasks.append(asyncio.create_task(run_consumer(redis, bg_sm, block_ms=settings.segment_consumer_block_ms), name="segment-consumer"))      # группа потребителей Redis: безопасно на каждом экземпляре
            # Одиночные фоновые задачи выполняет только «ведущий» экземпляр (аренда в Redis): при нескольких экземплярах backend нет дублей писем, заданий и синхронизаций.
            leader = Leader(redis, settings.leader_lease_s)
            app.state.leader = leader

            def _task(name, make):
                return lambda: asyncio.create_task(make(), name=name)

            leader.register("legacy-ldap-import", _task("legacy-ldap-import", lambda: legacy.auto_import(bg_sm, redis, directory)))
            leader.register("meeting-reaper", _task("meeting-reaper", lambda: run_reaper(bg_sm, meetings_svc, sip=app.state.sip_routing, outlook_sync=app.state.outlook_sync)))
            leader.register("auto-update", _task("auto-update", lambda: run_autoupdate(app.state.autoupdate)))
            leader.register("auto-backup", _task("auto-backup", lambda: run_auto_backup(app.state.auto_backup)))
            leader.register("retention", _task("retention", lambda: run_retention(bg_sm, protocols)))

            async def _job_orphans():
                while True:                                   # процесс-владелец упал: его ключ истёк — документ закрывается как прерванный (раньше — только при перезапуске)
                    await asyncio.sleep(60)
                    try:
                        await protocols.recover_orphans()
                    except Exception:  # noqa: BLE001
                        log.warning("Не удалось закрыть прерванные формирования документов", exc_info=True)
            leader.register("job-orphans", _task("job-orphans", _job_orphans))
            leader.register("asr-model-sync", _task("asr-model-sync", lambda: run_asr_sync(bg_sm, settings_svc, redis)))
            leader.register("journal-retention", _task("journal-retention", lambda: run_journal_retention(journal)))
            leader.register("mail-queue", _task("mail-queue", lambda: run_mail_queue(bg_sm, delivery)))
            leader.register("webhooks", lambda: webhooks.start(), lambda: webhooks.stop())
            leader.register("jobs", lambda: app.state.jobs.start(), lambda: app.state.jobs.stop())
            leader.register("storage-sync", _task("storage-sync", lambda: run_storage_sync(bg_sm, settings_svc, reconciler, redis)))
            leader.register("storage-stats", _task("storage-stats", lambda: app.state.storage_stats.run()))
            leader.register("storage-transfer", _task("storage-transfer", lambda: app.state.transfers.run()))
            tasks.append(asyncio.create_task(leader.run(), name="background-leader"))
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
            if bg_engine is not engine:
                await bg_engine.dispose()
            if health_engine is not None:
                await health_engine.dispose()

    app = FastAPI(
        title="Peregovorka API", version=settings.app_version, lifespan=lifespan,
        docs_url="/api/v1/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json" if settings.docs_enabled else None,
    )

    app.add_middleware(RequestContext, settings=settings)

    from fastapi.responses import JSONResponse as _JSON
    from sqlalchemy import exc as sa_exc

    @app.exception_handler(sa_exc.TimeoutError)
    async def pool_exhausted(request: Request, exc: Exception):
        stats = getattr(request.app.state, "db_stats", None)
        if stats is not None:
            stats.pool_timeouts += 1
        log.error("Нет свободных соединений с базой: запрос отклонён 503", extra={"path": request.url.path, "method": request.method})
        return _JSON({"detail": "Сервер перегружен: нет свободных соединений с базой данных. Повторите через несколько секунд."}, status_code=503, headers={"Retry-After": "5"})

    from redis.exceptions import ConnectionError as _RedisConnError, TimeoutError as _RedisTimeout

    @app.exception_handler(_RedisConnError)
    @app.exception_handler(_RedisTimeout)
    async def redis_unavailable(request: Request, exc: Exception):
        stats = getattr(request.app.state, "db_stats", None)
        if stats is not None:
            stats.redis_unavailable += 1
        log.error("Redis недоступен: запрос отклонён 503", extra={"path": request.url.path, "error": type(exc).__name__})
        return _JSON({"detail": "Сервис временно недоступен (хранилище сеансов). Повторите через несколько секунд."}, status_code=503, headers={"Retry-After": "5"})

    @app.exception_handler(sa_exc.DBAPIError)
    async def db_unavailable(request: Request, exc: sa_exc.DBAPIError):
        if not (exc.connection_invalidated or isinstance(exc, (sa_exc.OperationalError, sa_exc.InterfaceError))):
            raise exc
        stats = getattr(request.app.state, "db_stats", None)
        if stats is not None:
            stats.db_unavailable += 1
        log.error("База данных недоступна или соединение потеряно: запрос отклонён 503", extra={"path": request.url.path, "error": type(exc.orig).__name__ if exc.orig else type(exc).__name__})
        return _JSON({"detail": "База данных временно недоступна. Повторите через несколько секунд."}, status_code=503, headers={"Retry-After": "5"})

    prefix = "/api/v1"
    for r in (chats_api.router, dictation_api.router, site_api.router, admin_outlook.router, admin_crypto.router, outlook_api.router, peer_rooms_api.router, config_backup_api.router, media_api.router, storage_admin_api.router, auth.router, admin_bitrix.router, admin_public_api.router, admin_webhooks.router, profile.router, rooms.router, meetings.router, collab.router, guest.router, templates.router, client.router, moderation.router, room_manage.router, admin.router, admin_access.router, admin_mail.router, admin_storage_sync.router, admin_auto_backup.router, auth_totp.router, captcha_api.router, delivery_api.router, delivery_api.templates_router, meeting_settings.router, meeting_access.router, room_icons_api.router, room_journal.router, maps_api.router, admin_system.router, admin_performance.router, admin_llm.router, admin_sip.router, telephony.router, admin_journal.router, admin_updates.router, admin_asr.router, health.router, ws.router):
        app.include_router(r, prefix=prefix)
    app.include_router(internal.router)
    app.include_router(api_docs.router)     # /api/docs: общедоступная документация (выполнение запросов остаётся под аутентификацией)
    app.include_router(public_router)     # публичный API: /api/public/v1 (свой формат ошибок, ключи вместо cookie-сессии)
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
