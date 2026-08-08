"""State machine transition rules and guards."""

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


async def _create_draft(client: AsyncClient, token: str) -> str:
    resp = await client.post(
        "/api/v1/properties",
        json=PROPERTY_PAYLOAD,
        headers=auth(token),
    )
    return resp.json()["data"]["id"]


@pytest.mark.asyncio
async def test_cannot_edit_submitted(client: AsyncClient, agent_token: str):
    prop_id = await _create_draft(client, agent_token)
    await client.post(f"/api/v1/properties/{prop_id}/submit", headers=auth(agent_token))

    resp = await client.patch(
        f"/api/v1/properties/{prop_id}",
        json={"title": "Updated Title Here"},
        headers=auth(agent_token),
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "invalid_state_transition"


@pytest.mark.asyncio
async def test_cannot_publish_draft(client: AsyncClient, agent_token: str):
    prop_id = await _create_draft(client, agent_token)

    resp = await client.post(
        f"/api/v1/properties/{prop_id}/publish",
        headers=auth(agent_token),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_cannot_submit_already_pending(client: AsyncClient, agent_token: str):
    prop_id = await _create_draft(client, agent_token)
    await client.post(f"/api/v1/properties/{prop_id}/submit", headers=auth(agent_token))

    resp = await client.post(
        f"/api/v1/properties/{prop_id}/submit",
        headers=auth(agent_token),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_agent_cannot_approve(client: AsyncClient, agent_token: str):
    prop_id = await _create_draft(client, agent_token)
    await client.post(f"/api/v1/properties/{prop_id}/submit", headers=auth(agent_token))

    resp = await client.post(
        f"/api/v1/properties/{prop_id}/approve",
        headers=auth(agent_token),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_full_lifecycle_draft_to_published(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    prop_id = await _create_draft(client, agent_token)

    resp = await client.post(
        f"/api/v1/properties/{prop_id}/submit",
        headers=auth(agent_token),
    )
    assert resp.json()["data"]["approval_status"] == "pending_review"

    resp = await client.post(
        f"/api/v1/properties/{prop_id}/approve",
        headers=auth(admin_token),
    )
    assert resp.json()["data"]["approval_status"] == "approved"

    resp = await client.post(
        f"/api/v1/properties/{prop_id}/publish",
        headers=auth(agent_token),
    )
    data = resp.json()["data"]
    assert data["approval_status"] == "published"
    assert data["expires_at"] is not None


@pytest.mark.asyncio
async def test_full_lifecycle_with_rejection_and_resubmit(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    prop_id = await _create_draft(client, agent_token)

    await client.post(f"/api/v1/properties/{prop_id}/submit", headers=auth(agent_token))
    await client.post(
        f"/api/v1/properties/{prop_id}/reject",
        json={"rejection_reason": "Needs better photos"},
        headers=auth(admin_token),
    )

    resp = await client.get(
        f"/api/v1/properties/mine/{prop_id}",
        headers=auth(agent_token),
    )
    assert resp.json()["data"]["approval_status"] == "rejected"
    assert resp.json()["data"]["rejection_reason"] == "Needs better photos"

    await client.post(f"/api/v1/properties/{prop_id}/submit", headers=auth(agent_token))
    await client.post(f"/api/v1/properties/{prop_id}/approve", headers=auth(admin_token))
    resp = await client.post(
        f"/api/v1/properties/{prop_id}/publish",
        headers=auth(agent_token),
    )
    assert resp.json()["data"]["approval_status"] == "published"


@pytest.mark.asyncio
async def test_archive_and_republish(
    client: AsyncClient,
    agent_token: str,
    admin_token: str,
):
    prop_id = await _create_draft(client, agent_token)

    await client.post(f"/api/v1/properties/{prop_id}/submit", headers=auth(agent_token))
    await client.post(f"/api/v1/properties/{prop_id}/approve", headers=auth(admin_token))
    await client.post(f"/api/v1/properties/{prop_id}/publish", headers=auth(agent_token))

    resp = await client.post(
        f"/api/v1/properties/{prop_id}/archive",
        headers=auth(agent_token),
    )
    assert resp.json()["data"]["approval_status"] == "archived"

    resp = await client.post(
        f"/api/v1/properties/{prop_id}/submit",
        headers=auth(agent_token),
    )
    assert resp.json()["data"]["approval_status"] == "pending_review"
