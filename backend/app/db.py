from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import Settings


PG_RESERVED = 10        # соединения PostgreSQL сверх пулов backend: служебный пул проверок, резервное копирование, администрирование


def pool_plan(settings: Settings, *, background: bool) -> dict:
    """Размеры пулов: явные значения из настроек, иначе — по числу ядер. Запросы пользователей и фоновые задачи — разные пулы.
    Подобранные автоматически размеры ужимаются, если пулы всех процессов (uvicorn_workers) не помещаются в postgres_max_connections;
    явно заданные не меняются (ошибку конфигурации показывает `note`)."""
    cpus = os.cpu_count() or 2
    api = settings.db_pool_size or max(10, min(40, cpus * 3))
    api_over = settings.db_max_overflow or api
    bg = settings.db_bg_pool_size or max(4, min(12, cpus))
    bg_over = settings.db_bg_max_overflow or bg
    note = None
    if settings.postgres_max_connections:
        budget = (settings.postgres_max_connections - PG_RESERVED) // settings.uvicorn_workers - 2       # 2 — служебный пул проверок процесса
        need = api + api_over + bg + bg_over
        if need > budget:
            if settings.db_pool_size or settings.db_max_overflow or settings.db_bg_pool_size or settings.db_bg_max_overflow:
                note = f"Пулы backend ({need} на процесс × {settings.uvicorn_workers}) не помещаются в max_connections PostgreSQL ({settings.postgres_max_connections}): увеличьте POSTGRES_MAX_CONNECTIONS или уменьшите DB_*POOL*"
            else:
                k = max(budget, 8) / need
                api, api_over, bg, bg_over = max(3, int(api * k)), max(2, int(api_over * k)), max(2, int(bg * k)), max(1, int(bg_over * k))
                note = (f"Пулы backend уменьшены до {api}+{api_over} / {bg}+{bg_over} на процесс, чтобы {settings.uvicorn_workers} процесс(а) поместились в "
                        f"max_connections PostgreSQL ({settings.postgres_max_connections}); для полной производительности увеличьте POSTGRES_MAX_CONNECTIONS")
    plan = {"pool_size": bg, "max_overflow": bg_over} if background else {"pool_size": api, "max_overflow": api_over}
    return {**plan, "note": note}


def create_engine(settings: Settings, *, background: bool = False, pool: dict | None = None) -> AsyncEngine:
    url = settings.sqlalchemy_url
    if str(url).startswith("sqlite"):
        return create_async_engine(url)
    plan = pool or pool_plan(settings, background=background)
    server: dict[str, str] = {"application_name": "peregovorka-bg" if background else "peregovorka-api"}
    if settings.db_idle_in_tx_timeout_s:
        server["idle_in_transaction_session_timeout"] = str(settings.db_idle_in_tx_timeout_s * 1000)       # страховка: забытая транзакция не держит соединение вечно
    if settings.db_statement_timeout_s:
        server["statement_timeout"] = str(settings.db_statement_timeout_s * 1000)
    return create_async_engine(url, pool_pre_ping=True, pool_size=plan["pool_size"], max_overflow=plan["max_overflow"], pool_timeout=settings.db_pool_timeout_s,
                               pool_recycle=settings.db_pool_recycle_s or -1, connect_args={"server_settings": server})


def create_health_engine(settings: Settings) -> AsyncEngine | None:
    """Крошечный отдельный пул для проверок готовности и статистики: работает, даже когда основной пул исчерпан (иначе «здоров» не отличить от «завис»)."""
    url = settings.sqlalchemy_url
    if str(url).startswith("sqlite"):
        return None
    return create_async_engine(url, pool_size=1, max_overflow=1, pool_timeout=3, pool_pre_ping=True,
                               connect_args={"server_settings": {"application_name": "peregovorka-health", "statement_timeout": "5000"}, "timeout": 3})


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def release(db: AsyncSession) -> None:
    """Завершает транзакцию и возвращает соединение в пул ПЕРЕД долгой операцией без базы (LLM, экспорт, файловое хранилище, сеть). Вызывать, когда больше нечего читать/писать
    до окончания операции: сессия остаётся рабочей (expire_on_commit=False), следующий запрос откроет новую короткую транзакцию."""
    await db.commit()
