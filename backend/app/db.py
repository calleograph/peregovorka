from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    url = settings.sqlalchemy_url
    kwargs: dict = {"pool_pre_ping": True}
    if str(url).startswith("sqlite"):
        kwargs = {}
    return create_async_engine(url, **kwargs)


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
