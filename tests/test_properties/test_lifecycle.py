"""Property lifecycle: submit → approve → publish."""
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
        "/properties", json=PROPERTY_PAYLOAD, headers=auth(token),
    )
    return resp.json()["data"]["id"]


@pytest.mark.asyncio
async def test_submit_for_review(client: AsyncClient, agent_token: str):
    prop_id = await _create_draft(client, agent_token)

    resp = await client.post(
        f"/properties/{prop_id}/submit", headers=auth(agent_token),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["approval_status"] == "pending_review"


@pytest.mark.asyncio
async def test_approve_and_publish(client: AsyncClient, agent_token: str, admin_token: str):
    prop_id = await _create_draft(client, agent_token)

    # Submit
    await client.post(
        f"/properties/{prop_id}/submit", headers=auth(agent_token),
    )

    # Approve (admin)
    resp = await client.post(
        f"/properties/{prop_id}/approve", headers=auth(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["approval_status"] == "approved"

    # Publish (agent)
    resp = await client.post(
        f"/properties/{prop_id}/publish", headers=auth(agent_token),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["approval_status"] == "published"
    assert data["expires_at"] is not None


@pytest.mark.asyncio
async def test_reject_with_reason(client: AsyncClient, agent_token: str, admin_token: str):
    prop_id = await _create_draft(client, agent_token)
    await client.post(
        f"/properties/{prop_id}/submit", headers=auth(agent_token),
    )

    resp = await client.post(
        f"/properties/{prop_id}/reject",
        json={"rejection_reason": "Missing property photos"},
        headers=auth(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["approval_status"] == "rejected"
    assert data["rejection_reason"] == "Missing property photos"


@pytest.mark.asyncio
async def test_resubmit_after_rejection(client: AsyncClient, agent_token: str, admin_token: str):
    prop_id = await _create_draft(client, agent_token)

    # Submit → Reject
    await client.post(f"/properties/{prop_id}/submit", headers=auth(agent_token))
    await client.post(
        f"/properties/{prop_id}/reject",
        json={"rejection_reason": "Bad photos"},
        headers=auth(admin_token),
    )

    # Resubmit
    resp = await client.post(
        f"/properties/{prop_id}/submit", headers=auth(agent_token),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["approval_status"] == "pending_review"
    assert data["rejection_reason"] is None  # cleared on resubmit


@pytest.mark.asyncio
async def test_revert_to_draft(client: AsyncClient, agent_token: str, admin_token: str):
    prop_id = await _create_draft(client, agent_token)
    await client.post(f"/properties/{prop_id}/submit", headers=auth(agent_token))
    await client.post(
        f"/properties/{prop_id}/approve", headers=auth(admin_token),
    )

    resp = await client.post(
        f"/properties/{prop_id}/revert-to-draft", headers=auth(agent_token),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["approval_status"] == "draft"