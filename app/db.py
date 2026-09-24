"""Async SQLAlchemy engine. SQLite locally, Postgres when DATABASE_URL says so."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("sqlite://") and not url.startswith("sqlite+aiosqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    return url


def build_engine(url: str) -> AsyncEngine:
    normalized = normalize_database_url(url)
    kwargs: dict = {}
    if normalized.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_async_engine(normalized, **kwargs)
    if normalized.startswith("sqlite"):
        _enable_sqlite_locks(engine)
    return engine


def _enable_sqlite_locks(engine: AsyncEngine) -> None:
    """Wait on the writer lock instead of failing the second concurrent checkout."""

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


async def init_models(engine: AsyncEngine) -> None:
    # Import registers tables on Base.metadata.
    import app.reservations.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
