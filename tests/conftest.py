"""Global pytest fixtures and environment configuration for IssueLens test suite."""

import asyncio
import os
import sys
from collections.abc import AsyncGenerator

import pytest_asyncio

# Force test environment and hermetic in-memory database
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

# On Windows, psycopg async requires WindowsSelectorEventLoopPolicy
if sys.platform == "win32":
    try:
        from asyncio import WindowsSelectorEventLoopPolicy

        if not isinstance(asyncio.get_event_loop_policy(), WindowsSelectorEventLoopPolicy):
            asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from issuelens.persistence.db import (
    close_engine,
    create_all_tables,
    drop_all_tables,
    init_engine,
)


@pytest_asyncio.fixture(autouse=True)
async def ensure_clean_test_database() -> AsyncGenerator[None, None]:
    """Ensure in-memory SQLite database has all tables created before every test."""
    engine = init_engine("sqlite+aiosqlite:///:memory:")
    await create_all_tables(engine)
    yield
    await drop_all_tables(engine)
    await close_engine()
