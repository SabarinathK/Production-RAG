import datetime
import logging
import redis.asyncio as aioredis
from typing import AsyncGenerator, Optional
from sqlalchemy import String, Integer, DateTime, Text, Float, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance

from app.config import settings

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# SQLAlchemy Models & Engine
# -------------------------------------------------------------------
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    )

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    )
    user_id: Mapped[str] = mapped_column(String(100), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(String(10))
    status_code: Mapped[int] = mapped_column(Integer)
    ip_address: Mapped[str] = mapped_column(String(50), nullable=True)
    execution_time_ms: Mapped[float] = mapped_column(Float, nullable=True)
    details: Mapped[str] = mapped_column(Text, nullable=True)


class DocumentMetadata(Base):
    __tablename__ = "document_metadata"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    s3_key: Mapped[str] = mapped_column(String(500))
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    )
    status: Mapped[str] = mapped_column(String(50), default="PROCESSING")  # PROCESSING, COMPLETED, FAILED, DUPLICATE_SKIPPED
    error_detail: Mapped[str] = mapped_column(Text, nullable=True)
    meta_info: Mapped[dict] = mapped_column(JSON, nullable=True)

import os as _os
db_url = settings.get_database_url()
_use_null_pool = _os.environ.get("SQLALCHEMY_NULLPOOL", "false").lower() == "true"
if "sqlite" in db_url or "postgresql" not in db_url:
    from sqlalchemy.pool import StaticPool
    async_engine = create_async_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
elif _use_null_pool:
    from sqlalchemy.pool import NullPool
    async_engine = create_async_engine(db_url, echo=False, poolclass=NullPool)
else:
    async_engine = create_async_engine(db_url, echo=False, pool_size=10, max_overflow=20)

AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

import asyncio

async def init_db(max_retries: int = 5, delay: float = 2.0):
    """
    Wait for the database to be reachable, then run Alembic migrations.
    Falls back to create_all for SQLite / test environments.
    """
    global async_engine, AsyncSessionLocal

    for attempt in range(1, max_retries + 1):
        try:
            async with async_engine.begin() as conn:
                # Verify connectivity
                from sqlalchemy import text
                await conn.execute(text("SELECT 1"))
            logger.info("Database connection verified.")
            break
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"Database connection attempt {attempt}/{max_retries} failed ({e}). Retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                logger.warning(f"PostgreSQL database unreachable ({e}). Falling back to SQLite in-memory database.")
                from sqlalchemy.pool import StaticPool
                sqlite_url = "sqlite+aiosqlite:///file:rag_test_mem?mode=memory&cache=shared"
                async_engine = create_async_engine(
                    sqlite_url,
                    echo=False,
                    connect_args={"check_same_thread": False, "uri": True},
                    poolclass=StaticPool
                )
                AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
                async with async_engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                    from sqlalchemy import text
                    try:
                        await conn.execute(text("ALTER TABLE document_metadata ADD COLUMN error_detail TEXT;"))
                    except Exception:
                        pass
                    try:
                        await conn.execute(text("ALTER TABLE document_metadata ADD COLUMN user_id VARCHAR(100);"))
                    except Exception:
                        pass
                logger.info("SQLite in-memory fallback database initialized.")
                await seed_default_user()
                return

    db_url_str = settings.get_database_url()
    if "postgresql" in db_url_str:
        # Run Alembic migrations for PostgreSQL environments
        try:
            from alembic.config import Config
            from alembic import command
            import os

            alembic_cfg = Config(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"))
            alembic_cfg.set_main_option("sqlalchemy.url", db_url_str)
            # Run in a thread since Alembic uses sync engine internally
            await asyncio.get_event_loop().run_in_executor(None, command.upgrade, alembic_cfg, "head")
            logger.info("Alembic migrations applied successfully (upgrade head).")
            # Ensure schema compatibility for missing columns on existing tables
            async with async_engine.begin() as conn:
                from sqlalchemy import text
                await conn.execute(text("ALTER TABLE document_metadata ADD COLUMN IF NOT EXISTS error_detail TEXT;"))
                await conn.execute(text("ALTER TABLE document_metadata ADD COLUMN IF NOT EXISTS user_id VARCHAR(100);"))
        except (ImportError, ModuleNotFoundError) as e:
            logger.warning(f"Alembic import unavailable ({e}). Initializing tables via create_all fallback.")
            async with async_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                from sqlalchemy import text
                await conn.execute(text("ALTER TABLE document_metadata ADD COLUMN IF NOT EXISTS error_detail TEXT;"))
                await conn.execute(text("ALTER TABLE document_metadata ADD COLUMN IF NOT EXISTS user_id VARCHAR(100);"))
        except Exception as e:
            logger.error(f"Alembic migration failed: {e}")
            raise
    else:
        # SQLite / test mode — use create_all for simplicity
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            from sqlalchemy import text
            try:
                await conn.execute(text("ALTER TABLE document_metadata ADD COLUMN error_detail TEXT;"))
            except Exception:
                pass
            try:
                await conn.execute(text("ALTER TABLE document_metadata ADD COLUMN user_id VARCHAR(100);"))
            except Exception:
                pass
        logger.info("Database tables initialized via create_all (non-PostgreSQL mode).")

    await seed_default_user()


async def seed_default_user():
    """Seed a default user ('tester' / 'password') if users table is empty."""
    from sqlalchemy import select
    from app.core.auth import hash_password
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.username == "tester"))
            tester_user = result.scalars().first()
            if not tester_user:
                default_user = User(
                    id="usr_tester",
                    username="tester",
                    email="tester@example.com",
                    password_hash=hash_password("password"),
                    role="admin",
                    is_active=True
                )
                session.add(default_user)
                await session.commit()
                logger.info("Default user 'tester' seeded successfully.")
    except Exception as e:
        logger.warning(f"Note: Seed user check/creation encountered: {e}")



async def check_db_health() -> bool:
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        return False

# -------------------------------------------------------------------
# Redis Connection Pool
# -------------------------------------------------------------------
redis_pool = None
_fake_redis_client = None

class FakeRedis:
    def __init__(self):
        self.store = {}
    async def get(self, key: str):
        return self.store.get(key)
    async def set(self, key: str, value: str, ex: Optional[int] = None, **kwargs):
        self.store[key] = value
    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value
    async def ping(self):
        return True

async def get_redis_client():
    global redis_pool, _fake_redis_client
    if redis_pool is not None and not isinstance(redis_pool, FakeRedis):
        try:
            current_loop = asyncio.get_running_loop()
            pool_loop = getattr(getattr(redis_pool, "connection_pool", None), "_loop", None)
            if pool_loop is not None and pool_loop != current_loop:
                redis_pool = None
        except Exception:
            redis_pool = None

    if redis_pool is None:
        try:
            client = aioredis.from_url(
                settings.get_redis_url(),
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1.0
            )
            await client.ping()
            redis_pool = client
        except Exception as e:
            logger.warning(f"Real Redis connection unavailable ({e}). Utilizing in-memory Redis fallback.")
            if _fake_redis_client is None:
                _fake_redis_client = FakeRedis()
            redis_pool = _fake_redis_client
            return _fake_redis_client
    return redis_pool

async def check_redis_health() -> bool:
    try:
        client = await get_redis_client()
        return await client.ping()
    except Exception as e:
        logger.warning(f"Redis health check failed: {e}")
        return False

# -------------------------------------------------------------------
# Qdrant Client Initialization
# -------------------------------------------------------------------
_qdrant_client = None

def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        if settings.QDRANT_HOST == ":memory:":
            _qdrant_client = QdrantClient(location=":memory:")
        else:
            try:
                client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT, timeout=5.0, check_compatibility=False)
                client.get_collections()
                _qdrant_client = client
            except Exception as e:
                logger.error(f"Qdrant connection error at {settings.QDRANT_HOST}:{settings.QDRANT_PORT}: {e}")
                raise RuntimeError(f"Qdrant vector database unreachable: {e}")
    return _qdrant_client

def check_qdrant_health() -> bool:
    try:
        client = get_qdrant_client()
        client.get_collections()
        return True
    except Exception as e:
        logger.warning(f"Qdrant health check failed: {e}")
        return False

def init_qdrant_collection(collection_name: str = settings.QDRANT_COLLECTION, vector_size: int = 3072):
    """Ensure vector collection exists in Qdrant with correct vector dimension"""
    try:
        client = get_qdrant_client()
        collections = [c.name for c in client.get_collections().collections]
        if collection_name in collections:
            col_info = client.get_collection(collection_name=collection_name)
            existing_size = col_info.config.params.vectors.size if hasattr(col_info.config.params.vectors, "size") else None
            if existing_size and existing_size != vector_size:
                logger.warning(f"Recreating Qdrant collection '{collection_name}' due to vector size mismatch (existing: {existing_size}, target: {vector_size})")
                client.delete_collection(collection_name=collection_name)
                collections.remove(collection_name)

        if collection_name not in collections:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE)
            )
            logger.info(f"Qdrant collection '{collection_name}' created successfully (vector size: {vector_size}).")
    except Exception as e:
        logger.warning(f"Qdrant initialization note: {e}")
