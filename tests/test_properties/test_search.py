"""Public property search and filtering."""

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


async def _create_and_publish(client: AsyncClient, agent_token: str, admin_token: str) -> str:
    """Helper: create → submit → approve → publish. Returns property ID."""
    resp = await client.post(
        "/api/v1/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(agent_token),
    )
    prop_id = resp.json()["data"]["id"]

    await client.post(f"/api/v1/properties/{prop_id}/submit", headers=auth(agent_token))
    await client.post(f"/api/v1/properties/{prop_id}/approve", headers=auth(admin_token))
    await client.post(f"/api/v1/properties/{prop_id}/publish", headers=auth(agent_token))

    return prop_id


@pytest.mark.asyncio
async def test_search_excludes_drafts(client: AsyncClient, agent_token: str):
    await client.post(
        "/api/v1/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(agent_token),
    )

    resp = await client.get("/api/v1/properties")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_includes_published(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    await _create_and_publish(client, agent_token, admin_token)

    resp = await client.get("/api/v1/properties")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["title"] == "3 Bedroom Flat in Lekki"


@pytest.mark.asyncio
async def test_search_filter_by_city(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    await _create_and_publish(client, agent_token, admin_token)

    resp = await client.get("/api/v1/properties?city=Lagos")
    assert resp.json()["total"] == 1

    resp = await client.get("/api/v1/properties?city=Abuja")
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_filter_by_price_range(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    await _create_and_publish(client, agent_token, admin_token)

    resp = await client.get("/api/v1/properties?min_price=3000000&max_price=4000000")
    assert resp.json()["total"] == 1

    resp = await client.get("/api/v1/properties?min_price=10000000")
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_filter_by_property_type(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    await _create_and_publish(client, agent_token, admin_token)

    resp = await client.get("/api/v1/properties?property_type=apartment")
    assert resp.json()["total"] == 1

    resp = await client.get("/api/v1/properties?property_type=duplex")
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_invalid_property_type(client: AsyncClient):
    resp = await client.get("/api/v1/properties?property_type=mansion")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_pagination(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    for i in range(3):
        payload = {**PROPERTY_PAYLOAD, "title": f"Property Listing Number {i}"}
        resp = await client.post(
            "/api/v1/properties",
            json=payload,
            headers=auth(agent_token),
        )
        pid = resp.json()["data"]["id"]
        await client.post(f"/api/v1/properties/{pid}/submit", headers=auth(agent_token))
        await client.post(f"/api/v1/properties/{pid}/approve", headers=auth(admin_token))
        await client.post(f"/api/v1/properties/{pid}/publish", headers=auth(agent_token))

    resp = await client.get("/api/v1/properties?per_page=2&page=1")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2
    assert resp.json()["total"] == 3

    resp = await client.get("/api/v1/properties?per_page=2&page=2")
    assert len(resp.json()["items"]) == 1


@pytest.mark.asyncio
async def test_search_returns_card_format(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    await _create_and_publish(client, agent_token, admin_token)

    resp = await client.get("/api/v1/properties")
    card = resp.json()["items"][0]

    assert "id" in card
    assert "title" in card
    assert "price" in card
    assert "primary_image_url" in card

    assert "description" not in card
    assert "latitude" not in card
    assert "longitude" not in card
