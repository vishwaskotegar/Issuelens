"""Database engine, session management, and connectivity diagnostics."""

import asyncio
import re
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from issuelens.config import Settings, get_settings
from issuelens.core.errors import ConfigurationError
from issuelens.core.logging import get_logger
from issuelens.persistence.models import Base

logger = get_logger("issuelens.persistence.db")

# On Windows, psycopg async requires a Selector event loop instead of the default Proactor loop
if sys.platform == "win32":
    try:
        from asyncio import WindowsSelectorEventLoopPolicy

        if not isinstance(asyncio.get_event_loop_policy(), WindowsSelectorEventLoopPolicy):
            asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

_ENGINE: AsyncEngine | None = None
_SESSION_FACTORY: async_sessionmaker[AsyncSession] | None = None


def normalize_database_url(raw_url: str) -> str:
    """Normalize connection string to an async-compatible SQLAlchemy driver.

    Converts:
    - postgresql:// -> postgresql+psycopg://
    - postgres:// -> postgresql+psycopg://
    - sqlite:// -> sqlite+aiosqlite:// (unless already using aiosqlite)
    """
    url = raw_url.strip()
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("sqlite://") and not url.startswith("sqlite+aiosqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    return url


def redact_database_url(url: str) -> str:
    """Mask credentials in database URLs for safe logging and error reporting."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


def get_default_database_url(settings: Settings) -> str:
    """Resolve database URL from settings or fall back to local SQLite storage."""
    if settings.app_env == "test":
        if settings.database_url and "sqlite" in settings.database_url:
            return normalize_database_url(settings.database_url)
        return "sqlite+aiosqlite:///:memory:"

    if settings.database_url:
        return normalize_database_url(settings.database_url)
    if settings.app_env == "production":
        raise ConfigurationError(
            "DATABASE_URL must be configured when APP_ENV=production; "
            "refusing to fall back to local SQLite storage."
        )
    return "sqlite+aiosqlite:///.cache/issuelens/issuelens.db"


def init_engine(
    url: str | None = None,
    echo: bool = False,
    pool_pre_ping: bool = True,
    **kwargs: Any,
) -> AsyncEngine:
    """Initialize global AsyncEngine singleton."""
    global _ENGINE, _SESSION_FACTORY

    if url is None:
        settings = get_settings()
        url = get_default_database_url(settings)

    normalized_url = normalize_database_url(url)
    safe_url = redact_database_url(normalized_url)
    logger.info("Initializing database engine with %s", safe_url)

    engine_kwargs: dict[str, Any] = {
        "echo": echo,
        "pool_pre_ping": pool_pre_ping,
        **kwargs,
    }

    # SQLite specific options: in-memory databases need StaticPool to share state across sessions
    if "sqlite" in normalized_url and ":memory:" in normalized_url:
        engine_kwargs["poolclass"] = StaticPool
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    _ENGINE = create_async_engine(normalized_url, **engine_kwargs)
    _SESSION_FACTORY = async_sessionmaker(
        bind=_ENGINE,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _ENGINE


def get_engine() -> AsyncEngine:
    """Return the active global AsyncEngine, initializing with defaults if needed."""
    global _ENGINE
    if _ENGINE is None:
        init_engine()
    assert _ENGINE is not None
    return _ENGINE


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return active sessionmaker, initializing engine if needed."""
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        init_engine()
    assert _SESSION_FACTORY is not None
    return _SESSION_FACTORY


async def close_engine() -> None:
    """Gracefully dispose global database engine."""
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None:
        await _ENGINE.dispose()
        _ENGINE = None
        _SESSION_FACTORY = None
        logger.info("Database engine disposed.")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session per request."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def db_session_scope(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Context manager for explicit transaction boundary control."""
    factory = session_factory or get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all_tables(engine: AsyncEngine) -> None:
    """Create all declared tables (for testing and initialization)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all_tables(engine: AsyncEngine) -> None:
    """Drop all tables (for teardown in test suites)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def check_database_health(
    engine: AsyncEngine | None = None,
) -> tuple[bool, str]:
    """Execute a lightweight probe query (`SELECT 1`) to verify database health.

    Returns:
        tuple[bool, str]: (is_healthy, detail_message)
    """
    active_engine = engine or get_engine()
    try:
        async with active_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            scalar = result.scalar()
            if scalar == 1:
                return True, "ok"
            return False, f"Unexpected probe scalar: {scalar}"
    except Exception as exc:
        safe_msg = redact_database_url(str(exc))
        logger.warning("Database health check probe failed: %s", safe_msg)
        return False, safe_msg
