"""CRUD operations for properties."""

import pytest
from httpx import AsyncClient


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


PROPERTY_PAYLOAD = {
    "title": "3 Bedroom Flat in Lekki",
    "description": "Spacious flat with BQ",
    "price": "3500000",
    "currency": "NGN",
    "property_type": "apartment",
    "status": "rent",
    "city": "Lagos",
    "state": "Lagos",
    "latitude": 6.4474,
    "longitude": 3.4712,
    "bedrooms": 3,
    "bathrooms": 2,
}


@pytest.mark.asyncio
async def test_create_property(client: AsyncClient, agent_token: str):
    resp = await client.post(
        "/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(agent_token),
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["title"] == "3 Bedroom Flat in Lekki"
    assert data["approval_status"] == "draft"
    assert data["owner_id"] is not None
    assert data["currency"] == "NGN"


@pytest.mark.asyncio
async def test_renter_cannot_create_property(client: AsyncClient, renter_token: str):
    resp = await client.post(
        "/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(renter_token),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_own_property(client: AsyncClient, agent_token: str):
    # Create
    resp = await client.post(
        "/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(agent_token),
    )
    prop_id = resp.json()["data"]["id"]

    # Read
    resp = await client.get(
        f"/properties/mine/{prop_id}",
        headers=auth(agent_token),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == prop_id
    assert resp.json()["data"]["latitude"] == 6.4474


@pytest.mark.asyncio
async def test_update_draft_property(client: AsyncClient, agent_token: str):
    resp = await client.post(
        "/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(agent_token),
    )
    prop_id = resp.json()["data"]["id"]

    resp = await client.patch(
        f"/properties/{prop_id}",
        json={"title": "Updated 5 Bedroom Duplex in VI"},
        headers=auth(agent_token),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == "Updated 5 Bedroom Duplex in VI"


@pytest.mark.asyncio
async def test_update_rejects_invalid_property_type(client: AsyncClient, agent_token: str):
    resp = await client.post(
        "/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(agent_token),
    )
    prop_id = resp.json()["data"]["id"]

    resp = await client.patch(
        f"/properties/{prop_id}",
        json={"property_type": "mansion"},
        headers=auth(agent_token),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_rejects_missing_required_fields(client: AsyncClient, agent_token: str):
    resp = await client.post(
        "/properties",
        json={"title": "Short"},
        headers=auth(agent_token),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_rejects_invalid_currency(client: AsyncClient, agent_token: str):
    payload = {**PROPERTY_PAYLOAD, "currency": "INVALID"}
    resp = await client.post(
        "/properties",
        json=payload,
        headers=auth(agent_token),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_own_properties(client: AsyncClient, agent_token: str):
    # Create two properties
    await client.post(
        "/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(agent_token),
    )
    await client.post(
        "/properties",
        json={**PROPERTY_PAYLOAD, "title": "Second Property Title Here"},
        headers=auth(agent_token),
    )

    resp = await client.get("/properties/mine", headers=auth(agent_token))
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
