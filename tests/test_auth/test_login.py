"""
User authentication/login tests.

Tests user login via the /auth/login endpoint.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_user_login_success(client: AsyncClient):
    """Test successful login."""
    # Register first
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "login@example.com",
            "password": "SecurePass123!",
            "first_name": "Test",
            "last_name": "User",
            "role": "renter",
            "accept_terms": True,
        },
    )
    
    # Login
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "login@example.com",
            "password": "SecurePass123!",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data or "token" in data


@pytest.mark.asyncio
async def test_user_login_invalid_credentials(client: AsyncClient):
    """Test login fails with wrong password."""
    # Register first
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "wrong@example.com",
            "password": "SecurePass123!",
            "first_name": "Test",
            "last_name": "User",
            "role": "renter",
            "accept_terms": True,
        },
    )
    
    # Try to login with wrong password
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "wrong@example.com",
            "password": "WrongPassword123!",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_user_login_nonexistent_user(client: AsyncClient):
    """Test login fails for nonexistent user."""
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "nonexistent@example.com",
            "password": "AnyPassword123!",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_user_login_missing_email(client: AsyncClient):
    """Test login fails without email."""
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "password": "SecurePass123!",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_login_missing_password(client: AsyncClient):
    """Test login fails without password."""
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "user@example.com",
        },
    )
    assert response.status_code == 422

