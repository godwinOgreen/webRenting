"""
User registration tests.

Tests user account creation via the /auth/register endpoint.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_user_register_renter(client: AsyncClient):
    """Test registering a new renter user."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "renter@example.com",
            "password": "SecurePass123!",
            "first_name": "John",
            "last_name": "Doe",
            "role": "renter",
            "accept_terms": True,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert "user_id" in data or "id" in data


@pytest.mark.asyncio
async def test_user_register_agent(client: AsyncClient):
    """Test registering a new agent user."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "agent@example.com",
            "password": "SecurePass123!",
            "first_name": "Jane",
            "last_name": "Smith",
            "role": "agent",
            "accept_terms": True,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert "user_id" in data or "id" in data


@pytest.mark.asyncio
async def test_user_register_missing_required_fields(client: AsyncClient):
    """Test registration fails without required fields."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "incomplete@example.com",
            "password": "SecurePass123!",
            # Missing first_name, last_name, role, accept_terms
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_register_weak_password(client: AsyncClient):
    """Test registration fails with weak password."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "weak@example.com",
            "password": "123",  # Too weak
            "first_name": "Test",
            "last_name": "User",
            "role": "renter",
            "accept_terms": True,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_register_invalid_email(client: AsyncClient):
    """Test registration fails with invalid email."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": "SecurePass123!",
            "first_name": "Test",
            "last_name": "User",
            "role": "renter",
            "accept_terms": True,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_register_duplicate_email(client: AsyncClient):
    """Test registration fails with duplicate email."""
    # Register first user
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "duplicate@example.com",
            "password": "SecurePass123!",
            "first_name": "First",
            "last_name": "User",
            "role": "renter",
            "accept_terms": True,
        },
    )

    # Try to register again with same email
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "duplicate@example.com",
            "password": "SecurePass123!",
            "first_name": "Second",
            "last_name": "User",
            "role": "renter",
            "accept_terms": True,
        },
    )
    assert response.status_code in [400, 409]  # Bad Request or Conflict


@pytest.mark.asyncio
async def test_user_register_terms_not_accepted(client: AsyncClient):
    """Test registration fails when terms not accepted."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "noterms@example.com",
            "password": "SecurePass123!",
            "first_name": "Test",
            "last_name": "User",
            "role": "renter",
            "accept_terms": False,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_register_invalid_role(client: AsyncClient):
    """Test registration fails with invalid role."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "invalid_role@example.com",
            "password": "SecurePass123!",
            "first_name": "Test",
            "last_name": "User",
            "role": "superadmin",  # Invalid role
            "accept_terms": True,
        },
    )
    assert response.status_code == 422
