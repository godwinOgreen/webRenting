"""
Shared test fixtures for the entire test suite.
"""
import asyncio
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.domains.users.models import User

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:g0dw1ndb@localhost:5432/webrenting_test"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def setup_database():
    """Create all tables once for the test session, drop after."""
    async with engine.begin() as conn:
        # Drop all tables first if they exist
        await conn.run_sync(Base.metadata.drop_all)
        # Then create them
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Clean up after tests
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(setup_database) -> AsyncGenerator[AsyncSession, None]:
    """Each test gets a transaction that rolls back after."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)

        async def override_get_db():
            yield session

        app.dependency_overrides[get_db] = override_get_db
        yield session
        await transaction.rollback()
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def agent_token(client: AsyncClient) -> str:
    await client.post("/api/v1/auth/register", json={
        "email": "agent@test.com",
        "password": "AgentPass123!",
        "first_name": "Test",
        "last_name": "Agent",
        "role": "agent",
        "accept_terms": True,
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "agent@test.com",
        "password": "AgentPass123!",
    })
    return resp.json()["data"]["access_token"]


@pytest_asyncio.fixture
async def renter_token(client: AsyncClient) -> str:
    await client.post("/api/v1/auth/register", json={
        "email": "renter@test.com",
        "password": "RenterPass123!",
        "first_name": "Test",
        "last_name": "Renter",
        "role": "renter",
        "accept_terms": True,
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "renter@test.com",
        "password": "RenterPass123!",
    })
    return resp.json()["data"]["access_token"]


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient) -> str:
    """
    Register and get an admin token.
    Update this if your app seeds admins differently.
    """
    await client.post("/api/v1/auth/register", json={
        "email": "admin@test.com",
        "password": "AdminPass123!",
        "first_name": "Test",
        "last_name": "Admin",
        "role": "admin",
        "accept_terms": True,
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "admin@test.com",
        "password": "AdminPass123!",
    })
    return resp.json()["data"]["access_token"]


@pytest_asyncio.fixture
async def test_user(client: AsyncClient, db_session: AsyncSession) -> User:
    """Register a renter and return the User model (from DB)."""
    await client.post("/api/v1/auth/register", json={
        "email": "testuser@test.com",
        "password": "TestPass123!",
        "first_name": "Test",
        "last_name": "User",
        "role": "renter",
        "accept_terms": True,
    })
    result = await db_session.execute(
        select(User).where(User.email == "testuser@test.com")
    )
    return result.scalar_one()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a user and return auth headers with valid JWT."""
    await client.post("/api/v1/auth/register", json={
        "email": "authuser@test.com",
        "password": "AuthPass123!",
        "first_name": "Auth",
        "last_name": "User",
        "role": "renter",
        "accept_terms": True,
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "authuser@test.com",
        "password": "AuthPass123!",
    })
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
