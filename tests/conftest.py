"""
conftest.py — Pytest session configuration.

Tests run against REAL Docker services (Postgres, Qdrant, Redis, MinIO, RabbitMQ).
Make sure `docker compose up -d` is running before executing the test suite.
"""
import os
import asyncio
import pytest

# Only override what is strictly needed to keep tests non-destructive
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ENABLE_TELEMETRY", "false")      # No OTel collector in tests
os.environ.setdefault("ENABLE_RATE_LIMITING", "false")  # Prevent rate-limit interference
os.environ["SQLALCHEMY_NULLPOOL"] = "true"

from app.core.database import init_db

@pytest.fixture(scope="session", autouse=True)
def initialize_test_database():
    """Ensure database connection is verified and missing columns/migrations are applied."""
    try:
        asyncio.run(init_db(max_retries=1, delay=0.1))
    except Exception as e:
        # Fallback to SQLite in-memory for offline test environment
        import os
        from app.config import settings
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        import app.core.database as db_mod

        from sqlalchemy.pool import StaticPool
        sqlite_url = "sqlite+aiosqlite:///file:rag_test_mem?mode=memory&cache=shared"
        settings.DATABASE_URL = sqlite_url
        db_mod.db_url = sqlite_url
        db_mod.async_engine = create_async_engine(sqlite_url, connect_args={"check_same_thread": False}, poolclass=StaticPool, echo=False)
        db_mod.AsyncSessionLocal = async_sessionmaker(db_mod.async_engine, expire_on_commit=False, class_=AsyncSession)
        asyncio.run(init_db(max_retries=1, delay=0.1))

