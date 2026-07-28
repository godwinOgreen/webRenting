"""
tests/domains/payments/test_router.py

Integration tests for Payments domain HTTP endpoints and Paystack Webhook processing.
"""

import hashlib
import hmac
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import RENTER_PLAN_PRICE_KOBO
from app.core.config import settings
from app.domains.payments.models import Payment, PaymentStatus
from app.domains.subscriptions.models import Subscription
from app.domains.users.models import User

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


def generate_paystack_signature(raw_body: bytes, secret: str) -> str:
    """Generate a valid HMAC-SHA512 signature for testing."""
    return hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()


def _make_webhook_payload(reference: str, amount: int) -> bytes:
    """Build a raw Paystack webhook JSON payload."""
    return f"""{{
        "event": "charge.success",
        "data": {{
            "reference": "{reference}",
            "status": "success",
            "amount": {amount}
        }}
    }}""".encode()


def _webhook_headers(raw_payload: bytes) -> dict:
    """Sign a payload and return headers ready for posting."""
    signature = generate_paystack_signature(raw_payload, settings.PAYSTACK_SECRET_KEY)
    return {
        "x-paystack-signature": signature,
        "content-type": "application/json",
    }


async def _create_pending_payment(
    db: AsyncSession,
    user: User,
    amount: int = RENTER_PLAN_PRICE_KOBO,
) -> Payment:
    """Insert a pending payment and flush (not commit)."""
    payment = Payment(
        user_id=user.id,
        amount=amount,
        status=PaymentStatus.PENDING,
        subscription_type="renter",
        paystack_reference=f"PLT-{uuid.uuid4().hex[:20]}",
    )
    db.add(payment)
    await db.flush()
    return payment


# ── Initialization Tests ──────────────────────────────────────────────────────


async def test_initialize_payment_success(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    """Initializing a payment returns authorization URL and reference."""

    async def mock_initialize_transaction(email: str, amount_kobo: int, reference: str):
        return {
            "authorization_url": "https://checkout.paystack.com/mock-code",
            "access_code": "mock-access-code",
            "reference": reference,
        }

    monkeypatch.setattr(
        "app.integrations.paystack.initialize_transaction",
        mock_initialize_transaction,
    )

    response = await client.post(
        "/payments/initialize",
        json={"plan_type": "renter"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    res_data = response.json()

    assert res_data["success"] is True
    assert "authorization_url" in res_data["data"]
    assert "reference" in res_data["data"]
    assert res_data["data"]["reference"].startswith("PLT-")


async def test_initialize_payment_invalid_plan(
    client: AsyncClient,
    auth_headers: dict[str, str],
):
    """Invalid plan types are rejected by schema validation."""
    response = await client.post(
        "/payments/initialize",
        json={"plan_type": "super_vip_plan"},
        headers=auth_headers,
    )

    assert response.status_code == 400


# ── Read Endpoints Tests ──────────────────────────────────────────────────────


async def test_list_mine_payments(
    client: AsyncClient,
    auth_headers: dict[str, str],
    test_user: User,
    db: AsyncSession,
):
    """User can retrieve their payment history."""
    payment = await _create_pending_payment(db, test_user)

    response = await client.get(
        "/payments/mine?page=1&per_page=10",
        headers=auth_headers,
    )

    assert response.status_code == 200
    res = response.json()
    assert "items" in res
    assert len(res["items"]) >= 1
    assert res["items"][0]["id"] == str(payment.id)


async def test_get_payment_details_success(
    client: AsyncClient,
    auth_headers: dict[str, str],
    test_user: User,
    db: AsyncSession,
):
    """User can retrieve details for a payment they own."""
    payment = await _create_pending_payment(db, test_user)

    response = await client.get(
        f"/payments/{payment.id}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(payment.id)


async def test_get_payment_other_user_forbidden(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db: AsyncSession,
    test_user: User,
):
    """User cannot view another user's payment."""
    # Create a payment for a different user
    other_user = User(
        email="other@test.com",
        first_name="Other",
        last_name="User",
        role="renter",
    )
    db.add(other_user)
    await db.flush()

    payment = await _create_pending_payment(db, other_user)

    response = await client.get(
        f"/payments/{payment.id}",
        headers=auth_headers,
    )

    assert response.status_code == 404


# ── Webhook Security & Idempotency Tests ──────────────────────────────────────


async def test_webhook_invalid_signature_fails(client: AsyncClient):
    """Webhooks without a valid HMAC signature return 401."""
    raw_payload = b'{"event": "charge.success", "data": {}}'
    headers = {"x-paystack-signature": "invalid_signature_hash"}

    response = await client.post(
        "/payments/webhook",
        content=raw_payload,
        headers=headers,
    )

    assert response.status_code == 401


async def test_webhook_missing_signature_fails(client: AsyncClient):
    """Webhooks with no signature header return 401."""
    raw_payload = b'{"event": "charge.success", "data": {}}'

    response = await client.post(
        "/payments/webhook",
        content=raw_payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 401


async def test_webhook_charge_success_activates_subscription(
    client: AsyncClient,
    test_user: User,
    db: AsyncSession,
):
    """Valid webhook updates payment to SUCCESSFUL and creates subscription."""
    payment = await _create_pending_payment(db, test_user)

    raw_payload = _make_webhook_payload(payment.paystack_reference, payment.amount)
    headers = _webhook_headers(raw_payload)

    response = await client.post(
        "/payments/webhook",
        content=raw_payload,
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}

    # Verify payment status updated
    await db.refresh(payment)
    assert payment.status == PaymentStatus.SUCCESSFUL
    assert payment.paid_at is not None

    # Verify subscription created
    sub = (
        await db.execute(select(Subscription).where(Subscription.user_id == test_user.id))
    ).scalar_one_or_none()
    assert sub is not None
    assert sub.plan == "renter"


async def test_webhook_replay_attack_is_idempotent(
    client: AsyncClient,
    test_user: User,
    db: AsyncSession,
):
    """Sending the same webhook twice should NOT create duplicate subscriptions."""
    payment = await _create_pending_payment(db, test_user)

    raw_payload = _make_webhook_payload(payment.paystack_reference, payment.amount)
    headers = _webhook_headers(raw_payload)

    # First call — process normally
    res1 = await client.post(
        "/payments/webhook",
        content=raw_payload,
        headers=headers,
    )
    assert res1.status_code == 200

    # Second call (replay) — should be a no-op
    res2 = await client.post(
        "/payments/webhook",
        content=raw_payload,
        headers=headers,
    )
    assert res2.status_code == 200

    # Exactly one subscription
    subs = (
        (await db.execute(select(Subscription).where(Subscription.user_id == test_user.id)))
        .scalars()
        .all()
    )
    assert len(subs) == 1


async def test_webhook_amount_mismatch_fails_payment(
    client: AsyncClient,
    test_user: User,
    db: AsyncSession,
):
    """Webhook with wrong amount marks payment FAILED, no subscription created."""
    payment = await _create_pending_payment(db, test_user)

    # Webhook reports 100 kobo (₦1) instead of full price
    raw_payload = _make_webhook_payload(payment.paystack_reference, 100)
    headers = _webhook_headers(raw_payload)

    response = await client.post(
        "/payments/webhook",
        content=raw_payload,
        headers=headers,
    )

    assert response.status_code == 200

    # Payment marked failed
    await db.refresh(payment)
    assert payment.status == PaymentStatus.FAILED

    # No subscription
    sub = (
        await db.execute(select(Subscription).where(Subscription.user_id == test_user.id))
    ).scalar_one_or_none()
    assert sub is None


async def test_webhook_unknown_reference_ignored(client: AsyncClient):
    """Webhook with an unrecognised reference returns 200 but creates nothing."""
    raw_payload = _make_webhook_payload("PLT-nonexistent123", 100000)
    headers = _webhook_headers(raw_payload)

    response = await client.post(
        "/payments/webhook",
        content=raw_payload,
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}


async def test_webhook_non_charge_event_ignored(
    client: AsyncClient,
    test_user: User,
    db: AsyncSession,
):
    """Non-charge.success events are logged and ignored."""
    payment = await _create_pending_payment(db, test_user)

    raw_payload = f"""{{
        "event": "transfer.success",
        "data": {{
            "reference": "{payment.paystack_reference}",
            "status": "success",
            "amount": {payment.amount}
        }}
    }}""".encode()

    headers = _webhook_headers(raw_payload)

    response = await client.post(
        "/payments/webhook",
        content=raw_payload,
        headers=headers,
    )

    assert response.status_code == 200

    # Payment still pending — event type was ignored
    await db.refresh(payment)
    assert payment.status == PaymentStatus.PENDING
