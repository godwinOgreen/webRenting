"""
User registration tests.

Tests user account creation via the /auth/register endpoint.
"""

import pytest
from httpx import AsyncClient


def build_register_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "email": "user@example.com",
        "password": "SecurePass123!",
        "first_name": "Test",
        "last_name": "User",
        "role": "renter",
        "accept_terms": True,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_user_register_renter(client: AsyncClient):
    """Test registering a new renter user."""
    response = await client.post(
        "/api/v1/auth/register",
        json=build_register_payload(
            email="renter@example.com",
            role="renter",
        ),
    )

    assert response.status_code == 201

    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Registration successful"
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["access_token"]

    user = body["data"]["user"]
    assert user["id"]
    assert user["email"] == "renter@example.com"
    assert user["role"] == "renter"
    assert user["is_suspended"] is False


@pytest.mark.asyncio
async def test_user_register_agent(client: AsyncClient):
    """Test registering a new agent user."""
    response = await client.post(
        "/api/v1/auth/register",
        json=build_register_payload(
            email="agent@example.com",
            role="agent",
        ),
    )

    assert response.status_code == 201

    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Registration successful"

    user = body["data"]["user"]
    assert user["role"] == "agent"
    assert user["email"] == "agent@example.com"


@pytest.mark.asyncio
async def test_user_register_missing_required_fields(client: AsyncClient):
    """Test registration fails without required fields."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "incomplete@example.com",
            "password": "SecurePass123!",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "schema_validation_error"
    assert body["errors"]
    assert any(key in body["errors"] for key in ("first_name", "last_name", "accept_terms"))


@pytest.mark.asyncio
async def test_user_register_weak_password(client: AsyncClient):
    """Test registration fails with weak password."""
    response = await client.post(
        "/api/v1/auth/register",
        json=build_register_payload(email="weak@example.com", password="123"),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "schema_validation_error"
    assert body["errors"]["password"]


@pytest.mark.asyncio
async def test_user_register_invalid_email(client: AsyncClient):
    """Test registration fails with invalid email."""
    response = await client.post(
        "/api/v1/auth/register",
        json=build_register_payload(email="not-an-email"),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "schema_validation_error"
    assert body["errors"]["email"]


@pytest.mark.asyncio
async def test_user_register_duplicate_email(client: AsyncClient):
    """Test registration fails with duplicate email."""
    await client.post(
        "/api/v1/auth/register",
        json=build_register_payload(email="duplicate@example.com"),
    )

    response = await client.post(
        "/api/v1/auth/register",
        json=build_register_payload(
            email="duplicate@example.com",
            first_name="Second",
            last_name="User",
        ),
    )

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "email_taken"
    assert body["message"] == "An account with this email address already exists"


@pytest.mark.asyncio
async def test_user_register_terms_not_accepted(client: AsyncClient):
    """Test registration fails when terms not accepted."""
    response = await client.post(
        "/api/v1/auth/register",
        json=build_register_payload(email="noterms@example.com", accept_terms=False),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "schema_validation_error"
    assert body["errors"]["accept_terms"]


@pytest.mark.asyncio
async def test_user_register_invalid_role(client: AsyncClient):
    """Test registration fails with invalid role."""
    response = await client.post(
        "/api/v1/auth/register",
        json=build_register_payload(email="invalid_role@example.com", role="superadmin"),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "schema_validation_error"
    assert body["errors"]["role"]
