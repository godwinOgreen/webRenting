"""
User authentication/login tests.

Tests user login via the /auth/login endpoint.
"""

import pytest
from httpx import AsyncClient


def build_login_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "email": "login@example.com",
        "password": "SecurePass123!",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_user_login_success(client: AsyncClient):
    """Test successful login."""
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

    response = await client.post(
        "/api/v1/auth/login",
        json=build_login_payload(),
    )

    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Login successful"
    assert body["data"]["access_token"]
    assert body["data"]["token_type"] == "bearer"

    user = body["data"]["user"]
    assert user["email"] == "login@example.com"
    assert user["role"] == "renter"


@pytest.mark.asyncio
async def test_user_login_invalid_credentials(client: AsyncClient):
    """Test login fails with wrong password."""
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

    response = await client.post(
        "/api/v1/auth/login",
        json=build_login_payload(email="wrong@example.com", password="WrongPassword123!"),
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "invalid_credentials"
    assert body["message"] == "Incorrect email or password"


@pytest.mark.asyncio
async def test_user_login_nonexistent_user(client: AsyncClient):
    """Test login fails for nonexistent user."""
    response = await client.post(
        "/api/v1/auth/login",
        json=build_login_payload(email="nonexistent@example.com", password="AnyPassword123!"),
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "invalid_credentials"
    assert body["message"] == "Incorrect email or password"


@pytest.mark.asyncio
async def test_user_login_missing_email(client: AsyncClient):
    """Test login fails without email."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"password": "SecurePass123!"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "schema_validation_error"
    assert body["errors"]["email"]


@pytest.mark.asyncio
async def test_user_login_missing_password(client: AsyncClient):
    """Test login fails without password."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "schema_validation_error"
    assert body["errors"]["password"]
