"""Database connection setup using SQLAlchemy 2 async."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from sqlalchemy import event

from config.settings import settings


# Async engine with SQLite locking fixes
# - connect_args={"timeout": 30}: wait up to 30s for lock instead of failing immediately
# - poolclass=NullPool: required for SQLite async (no connection pooling)
# - pool_pre_ping=True: verify connection before use
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args={"timeout": 30},
    poolclass=NullPool,
    pool_pre_ping=True,
)


# WAL mode setup for SQLite to handle concurrent access
# WAL allows concurrent reads while writing, reducing "database is locked" errors
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL mode for SQLite on connection."""
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.close()


# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a new database session as an async context manager.

    Usage:
        async with get_session() as session:
            # use session
            pass

    Yields:
        AsyncSession: Database session
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
