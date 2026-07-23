# app/domains/subscriptions/schemas.py

"""
Read-only schemas for the subscriptions domain. There is no
SubscriptionCreate here — subscriptions are never created directly via
an API call. They're created exactly one way: payments/service.py's
handle_webhook() calls Subscription.create_from_payment() after a
successful Paystack charge.

This domain therefore only exposes:
  - reading the current/active subscription
  - reading subscription history
  - cancelling (which does NOT delete or end access early — see
    SubscriptionCancelRequest docstring)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domains.subscriptions.models import SubscriptionPlan, SubscriptionStatus


class SubscriptionRead(BaseModel):
    """
    Public representation of a user subscription period.
    Mapped directly from the Subscription ORM model via from_attributes=True.
    """

    id: uuid.UUID
    plan_type: SubscriptionPlan
    status: SubscriptionStatus
    started_at: datetime
    expires_at: datetime
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None

    # Mapped from model @property / @hybrid_property methods
    is_active: bool
    days_until_expiry: int
    is_expiring_soon: bool
    renewal_amount_naira: int

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "plan_type": "agent",
                "status": "active",
                "started_at": "2026-07-01T00:00:00Z",
                "expires_at": "2026-07-31T00:00:00Z",
                "cancelled_at": None,
                "cancellation_reason": None,
                "is_active": True,
                "days_until_expiry": 8,
                "is_expiring_soon": False,
                "renewal_amount_naira": 10000,
            }
        },
    )


class SubscriptionCancelRequest(BaseModel):
    """
    Cancelling a subscription does NOT immediately revoke access.
    Per Subscription.is_active (subscriptions/models.py), a CANCELLED
    subscription remains active until expires_at — standard SaaS
    behaviour. cancellation_reason is optional.
    """

    cancellation_reason: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional reason for cancellation.",
    )