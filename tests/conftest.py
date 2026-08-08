"""
Shared test fixtures for the entire test suite.
"""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.domains.subscriptions.models import Subscription, SubscriptionPlan, SubscriptionStatus
from app.domains.users.models import KycStatus, User, UserRole
from app.main import app

# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------

load_dotenv(".env.test")

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not configured. Create .env.test or set TEST_DATABASE_URL."
    )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
)


@pytest_asyncio.fixture(scope="session")
async def setup_database():
    """Create all database tables for the test session."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(
    setup_database,
) -> AsyncGenerator[AsyncSession]:
    """Provide a database session that rolls back after each test."""
    async with engine.connect() as conn:
        transaction = await conn.begin()

        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
        )

        async def override_get_db():
            yield session

        app.dependency_overrides[get_db] = override_get_db

        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient]:
    """Provide an async HTTP client for the FastAPI application."""
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Authentication fixtures
# ---------------------------------------------------------------------------


async def _activate_user_access(
    db_session: AsyncSession,
    email: str,
    role: UserRole,
    plan_type: SubscriptionPlan,
) -> None:
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()

    user.kyc_status = KycStatus.VERIFIED
    user.role = role

    subscription = Subscription(
        user_id=user.id,
        plan_type=plan_type,
        status=SubscriptionStatus.ACTIVE,
        started_at=datetime.now(tz=UTC) - timedelta(days=1),
        expires_at=datetime.now(tz=UTC) + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.commit()
    await db_session.refresh(user)


@pytest_asyncio.fixture
async def agent_token(client: AsyncClient, db_session: AsyncSession) -> str:
    """Register an agent and return a valid access token."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "agent@test.com",
            "password": "AgentPass123!",
            "first_name": "Test",
            "last_name": "Agent",
            "role": "agent",
            "accept_terms": True,
        },
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "agent@test.com",
            "password": "AgentPass123!",
        },
    )

    await _activate_user_access(
        db_session,
        "agent@test.com",
        UserRole.AGENT,
        SubscriptionPlan.AGENT,
    )

    return response.json()["data"]["access_token"]


@pytest_asyncio.fixture
async def renter_token(client: AsyncClient, db_session: AsyncSession) -> str:
    """Register a renter and return a valid access token."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "renter@test.com",
            "password": "RenterPass123!",
            "first_name": "Test",
            "last_name": "Renter",
            "role": "renter",
            "accept_terms": True,
        },
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "renter@test.com",
            "password": "RenterPass123!",
        },
    )

    await _activate_user_access(
        db_session,
        "renter@test.com",
        UserRole.RENTER,
        SubscriptionPlan.RENTER,
    )

    return response.json()["data"]["access_token"]


@pytest_asyncio.fixture
async def admin_token(db_session: AsyncSession) -> str:
    """Create an admin user directly and return a valid access token."""
    user = User(
        email="admin@test.com",
        password_hash=hash_password("AdminPass123!"),
        first_name="Test",
        last_name="Admin",
        role=UserRole.ADMIN,
        kyc_status=KycStatus.NOT_SUBMITTED,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    return create_access_token(user.id)


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_user(
    client: AsyncClient,
    db_session: AsyncSession,
) -> User:
    """Register a renter and return the database user."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "testuser@test.com",
            "password": "TestPass123!",
            "first_name": "Test",
            "last_name": "User",
            "role": "renter",
            "accept_terms": True,
        },
    )

    result = await db_session.execute(select(User).where(User.email == "testuser@test.com"))

    return result.scalar_one()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a user and return valid JWT authorization headers."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "authuser@test.com",
            "password": "AuthPass123!",
            "first_name": "Auth",
            "last_name": "User",
            "role": "renter",
            "accept_terms": True,
        },
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "authuser@test.com",
            "password": "AuthPass123!",
        },
    )

    token = response.json()["data"]["access_token"]

    return {
        "Authorization": f"Bearer {token}",
    }
