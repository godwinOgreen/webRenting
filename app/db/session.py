# app/db/session.py

"""
Async database engine and session factory.

Uses SQLAlchemy 2.0 async engine with psycopg 3 driver.

Architecture rule:
    get_db() is the ONLY place that calls db.commit() and db.rollback().
    Repositories use db.flush(). Services never touch db directly.

Pool settings:
    pool_size=10        Persistent connections kept open
    max_overflow=20     Extra connections allowed under burst load
    pool_recycle=3600   Recycle connections after 1 hour (prevents
                        stale connections from PostgreSQL or OS timeouts)
    pool_pre_ping=True  Verify connection is alive before using it
                        (catches connections killed by DB restarts)
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import all models so SQLAlchemy mapper can resolve relationships.
# Without this, models that reference each other (e.g. User ↔ Property)
# fail with "name 'Property' is not defined" at first query.
import app.db.base_all  # noqa: F401
from app.core.config import settings

# ─── Async Engine ─────────────────────────────────────────────────────────────

DATABASE_URL = settings.DATABASE_URL

engine = create_async_engine(
    DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)


# ─── Async Session Factory ────────────────────────────────────────────────────

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


# ─── FastAPI Dependency ───────────────────────────────────────────────────────


async def get_db() -> AsyncGenerator[AsyncSession]:
    """
    FastAPI async dependency — provides a database session per request.

    This is the ONLY place that calls commit() and rollback().

    Flow:
        1. Create async session
        2. Yield to endpoint (endpoint runs, calls services, services call repos)
        3. If no exception: await db.commit() (all repo flushes become permanent)
        4. If exception: await db.rollback() (all repo flushes are undone)
        5. Close session (returned to pool)

    Usage in endpoints:
        @router.get("/")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_factory() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()
