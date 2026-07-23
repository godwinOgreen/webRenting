# app/domains/payments/models.py

"""
Domain: Payments
Tables: payments
Enums: PaymentStatus

Payment is a transaction record — what was paid, when, and by whom.
It is NOT the same as Subscription (Decision 5 / Rule 13).

  PAYMENT      = "you paid ₦10,000 on 1st Jan"  (immutable transaction record)
  SUBSCRIPTION = "your access is active until 31st Jan" (mutable access record)

A successful Payment creates a Subscription. Checking PAYMENTS to gate
features is wrong — always check SUBSCRIPTIONS.status = active instead.

Key Paystack flow:
  1. Frontend calls POST /payments/initialize → backend calls Paystack API
  2. Paystack returns authorization_url → user pays on Paystack's page
  3. Paystack sends webhook → POST /payments/webhook
  4. Webhook handler verifies HMAC signature, finds payment by paystack_reference
  5. Updates status = successful → creates Subscription row

The paystack_reference UNIQUE constraint prevents duplicate webhook processing.

Amount is stored in kobo (Paystack's native unit). 100 kobo = ₦1.
Use amount_in_naira for display. Schema accepts naira, service converts.

Why subscription_type is a VARCHAR and not a FK or PostgreSQL ENUM:
  The subscription hasn't been created yet when the payment row is first
  inserted. We store the intended plan type as a string for reference.
  Validated by Pydantic SubscriptionPlanType enum at the API boundary.
  Database stores plain strings. No migration needed to add plans.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, String, text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.domains.subscriptions.models import Subscription
    from app.domains.users.models import User


# ─── Enums ───────────────────────────────────────────────────────────────────

class PaymentStatus(str, enum.Enum):
    """
    Lifecycle of a single Paystack transaction.

    PENDING    → created, waiting for user to complete payment on Paystack
    SUCCESSFUL → Paystack webhook confirmed payment received
    FAILED     → Paystack webhook reported failure (card declined, timeout, etc.)
    REFUNDED   → manual refund issued (via Paystack dashboard or API)
    """
    PENDING = "pending"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    REFUNDED = "refunded"


# ─── SQLAlchemy Enum column types ────────────────────────────────────────────

_payment_status_col = sa.Enum(
    PaymentStatus,
    name="payment_status",
    values_callable=lambda obj: [e.value for e in obj],
)


# ─── Model ───────────────────────────────────────────────────────────────────

class Payment(Base, UUIDMixin, TimestampMixin):
    """
    Transaction record for Paystack payments.

    Table: payments

    Uses TimestampMixin:
      - created_at = when the payment was initiated (row inserted, before user pays)
      - paid_at    = when Paystack confirmed payment received (webhook)

    Amount stored in kobo (Paystack's native unit). 100 kobo = ₦1.
    Schema accepts naira from the client, service converts to kobo.
    Use amount_in_naira property for display.
    """

    __tablename__ = "payments"

    # ── Who paid ──────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # ── What they paid for ────────────────────────────────────────────────────
    subscription_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment=(
            "Intended plan: 'renter' or 'agent'. String, not FK or PostgreSQL enum. "
            "Validated by Pydantic at API boundary. "
            "The Subscription row is created after webhook confirms success."
        ),
    )

    # ── Transaction details ───────────────────────────────────────────────────
    amount: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
        comment="Amount in kobo (Paystack native unit). 100000 = ₦1,000.",
    )
    status: Mapped[PaymentStatus] = mapped_column(
        _payment_status_col, nullable=False,
        server_default=text("'pending'"),
        default=PaymentStatus.PENDING,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False,
        server_default=text("'paystack'"),
        default="paystack",
        comment="Payment gateway name. Currently always 'paystack'.",
    )

    # ── Paystack-specific fields ──────────────────────────────────────────────
    paystack_reference: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
        unique=True,
        index=True,
        comment=(
            "Paystack transaction reference. UNIQUE prevents duplicate webhook handling. "
            "Set when payment is initialized. Used to match inbound webhooks."
        ),
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Set by webhook handler when Paystack confirms payment received.",
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Payment link/session expiry if applicable.",
    )

    # ── Constraints ───────────────────────────────────────────────────────────
    __table_args__ = (
        CheckConstraint("amount > 0", name="chk_payment_amount"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="payments",
    )

    subscriptions: Mapped[list[Subscription]] = relationship(
        "Subscription",
        back_populates="payment",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_successful(self) -> bool:
        return self.status == PaymentStatus.SUCCESSFUL

    @is_successful.expression
    def is_successful(cls):
        return cls.status == PaymentStatus.SUCCESSFUL

    @hybrid_property
    def is_pending(self) -> bool:
        return self.status == PaymentStatus.PENDING

    @is_pending.expression
    def is_pending(cls):
        return cls.status == PaymentStatus.PENDING

    @hybrid_property
    def is_failed(self) -> bool:
        return self.status == PaymentStatus.FAILED

    @is_failed.expression
    def is_failed(cls):
        return cls.status == PaymentStatus.FAILED

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def amount_in_naira(self) -> Decimal:
        """Convert kobo to naira for display. 100 kobo = ₦1."""
        return Decimal(self.amount) / 100

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Payment id={self.id} "
            f"status={self.status.value!r} "
            f"amount={self.amount} "
            f"ref={self.paystack_reference!r}>"
        )