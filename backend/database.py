from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from config import settings


# ── Engine ────────────────────────────────────────────────────────────────────
# NullPool is used so connections are not held across requests.
# For high-throughput production, swap to AsyncAdaptedQueuePool with pool_size.
engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",  # SQL logging in dev only
    future=True,
    poolclass=NullPool,
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # prevent lazy-load errors after commit
    autoflush=False,
    autocommit=False,
)


# ── Base class for all ORM models ─────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency ────────────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async DB session per request.

    Usage in a router:
        @router.get("/")
        async def my_route(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Startup / shutdown helpers ────────────────────────────────────────────────
async def init_db() -> None:
    """
    Create all tables that don't yet exist.
    In production, prefer Alembic migrations over this.
    Called from main.py lifespan on startup.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the connection pool on shutdown."""
    await engine.dispose()