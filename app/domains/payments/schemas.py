"""
Request/response schemas for the payments domain.

This domain only deals with the Paystack transaction lifecycle.
Subscription state is handled in subscriptions/schemas.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Initialize payment ─────────────────────────────────────────────────────────


class InitializePaymentRequest(BaseModel):
    """
    Renter or agent starts a subscription purchase.
    Amount is looked up server-side from constants — client never
    supplies the amount (prevents price tampering).
    """

    plan_type: Literal["renter", "agent"]


class InitializePaymentResponse(BaseModel):
    """Returned so the client can redirect to Paystack's checkout page."""

    payment_id: uuid.UUID
    authorization_url: str
    access_code: str
    reference: str


# ── Payment read ──────────────────────────────────────────────────────────────


class PaymentRead(BaseModel):
    """Full payment transaction details."""

    id: uuid.UUID
    amount: int = Field(..., description="Amount in kobo (100000 = ₦1,000)")
    amount_in_naira: Decimal = Field(..., description="Amount in naira for display")
    status: str
    provider: str
    paystack_reference: str | None = None
    paid_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Paystack webhook ──────────────────────────────────────────────────────────


class PaystackWebhookPayload(BaseModel):
    """
    Minimal typed slice of Paystack's webhook payload — only the fields
    payment_service actually consumes. Full raw JSON body is passed to
    HMAC verification separately (needs exact raw bytes).

    Paystack webhook event shape (relevant subset):
        {
          "event": "charge.success",
          "data": {
            "reference": "abc123",
            "amount": 100000,
            "status": "success",
            "customer": {"email": "..."},
            ...many fields we don't use...
          }
        }
    """

    event: str
    reference: str
    amount: int  # kobo, from data.amount
    status: str  # "success" | "failed" | etc.

    @classmethod
    def from_paystack_payload(cls, raw: dict) -> PaystackWebhookPayload:
        """
        Extract fields we care about from Paystack's nested
        event/data structure. Raises KeyError if payload is malformed
        — better to reject loudly than silently process garbage.
        """
        data = raw["data"]
        return cls(
            event=raw["event"],
            reference=data["reference"],
            amount=data["amount"],
            status=data["status"],
        )
