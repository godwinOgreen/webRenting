# app/domains/subscriptions/models.py

"""
Domain: Subscriptions
Tables: subscriptions
Enums: SubscriptionPlan, SubscriptionStatus

Subscription is the ACCESS record — "your access is active until this date."
It is NOT the same as Payment (Decision 5 / Rule 13).

ALWAYS check Subscription to gate features. Never check Payment.

Why they are separate tables:
  A user may have a successful Payment but an expired Subscription (they paid
  30 days ago and haven't renewed). Checking PAYMENTS.status = 'successful'
  would wrongly grant them access. SUBSCRIPTIONS.status = 'active' AND
  expires_at > now() is the correct gate.

Subscription lifecycle:
  Payment webhook confirms success
      ↓
  Subscription row created: status=active, expires_at=now()+30days
      ↓
  Celery subscription_check runs hourly:
      if expires_at <= now() + 3 days → send renewal reminder
      if expires_at < now() → set status=expired → revoke access
      ↓
  User renews → new Payment → new Subscription row (old row kept for history)

One row per period:
  Renewal creates a NEW row. Old rows kept for history.
  This gives a complete audit trail of all subscription periods per user.
  Querying the latest active subscription:
    SELECT * FROM subscriptions
    WHERE user_id = ? AND (status = 'active' OR status = 'cancelled')
      AND expires_at > NOW()
    ORDER BY created_at DESC LIMIT 1

Pricing (from constants.py):
  Renter plan: ₦1,000/month (100,000 kobo)
  Agent plan:  ₦10,000/month (10,000,000 kobo)

payment_id is nullable:
  Admin may grant a free subscription (testing, promotions, compensation).
  When payment_id IS NULL, reason must be provided (enforced in service layer).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, String, Text, text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.domains.payments.models import Payment
    from app.domains.users.models import User


# ─── Enums ───────────────────────────────────────────────────────────────────

class SubscriptionPlan(str, enum.Enum):
    """
    The two subscription tiers on the platform.

    RENTER: ₦1,000/month — unlocks search, messaging, booking, saved search alerts
    AGENT:  ₦10,000/month — unlocks listing creation, receiving leads, analytics
    """
    RENTER = "renter"
    AGENT = "agent"


class SubscriptionStatus(str, enum.Enum):
    """
    Current state of a subscription period.

    ACTIVE    → user has full access to gated features
    EXPIRED   → expires_at has passed; Celery sets this hourly
    CANCELLED → user cancelled before expires_at (access continues until expires_at)

    Important: A CANCELLED subscription may still grant access if expires_at
    is in the future. Check is_active property, not just status.
    """
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# ─── SQLAlchemy Enum column types ────────────────────────────────────────────

_subscription_plan_col = sa.Enum(
    SubscriptionPlan,
    name="subscription_plan",
    values_callable=lambda obj: [e.value for e in obj],
)

_subscription_status_col = sa.Enum(
    SubscriptionStatus,
    name="subscription_status",
    values_callable=lambda obj: [e.value for e in obj],
)


# ─── Model ───────────────────────────────────────────────────────────────────

class Subscription(Base, UUIDMixin, TimestampMixin):
    """
    Access record for a single subscription period.

    Table: subscriptions

    Gate check (in permissions/guards.py require_subscription()):
        stmt = select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.is_active,
        )
        sub = (await db.execute(stmt)).scalar_one_or_none()
        if sub is None:
            raise SubscriptionRequiredException()
    """

    __tablename__ = "subscriptions"

    # ── Foreign keys ──────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "The payment that created this subscription period. "
            "Nullable: admin may grant free subscriptions (testing, promotions). "
            "When NULL, free_reason must be provided (enforced in service)."
        ),
    )

    # ── Plan & status ─────────────────────────────────────────────────────────
    plan_type: Mapped[SubscriptionPlan] = mapped_column(
        _subscription_plan_col, nullable=False,
        comment="renter (₦1,000/mo) or agent (₦10,000/mo).",
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        _subscription_status_col, nullable=False,
        server_default=text("'active'"),
        default=SubscriptionStatus.ACTIVE,
        index=True,
        comment=(
            "Updated by Celery subscription_check task (runs hourly). "
            "Do NOT use this alone to check access — use is_active hybrid_property "
            "which also validates expires_at > now()."
        ),
    )

    # ── Time bounds ───────────────────────────────────────────────────────────
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="When this subscription period began (= payment confirmed at).",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        index=True,
        comment=(
            "When this period ends (= started_at + 30 days). "
            "Celery checks this hourly to send warnings and expire subscriptions."
        ),
    )

    # ── Cancellation ─────────────────────────────────────────────────────────
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Set when user cancels. Access continues until expires_at.",
    )
    cancellation_reason: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True,
    )

    # ── Free subscription reason ──────────────────────────────────────────────
    free_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment=(
            "Required when payment_id IS NULL. "
            "Explains why this subscription was granted without payment. "
            "e.g. 'Admin promotional grant', 'Testing account', 'Compensation for downtime'. "
            "Enforced in service layer, not DB constraint."
        ),
    )

    # ── Constraints ───────────────────────────────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "expires_at > started_at",
            name="chk_subscription_dates",
        ),
        CheckConstraint(
            "cancelled_at IS NULL OR cancelled_at >= started_at",
            name="chk_cancellation_date",
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="subscriptions",
    )
    payment: Mapped[Optional[Payment]] = relationship(
        "Payment",
        back_populates="subscriptions",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_active(self) -> bool:
        """
        The definitive access check. Use this everywhere.

        Checks BOTH:
          1. status in (active, cancelled) — cancelled still grants access
          2. expires_at > now() — real-time check regardless of Celery lag

        A cancelled subscription is still active until expires_at.
        This matches standard SaaS behaviour — users keep access until period ends.
        """
        now = datetime.now(tz=timezone.utc)
        return (
            self.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED)
            and self.expires_at > now
        )

    @is_active.expression
    def is_active(cls):
        """SQL: WHERE Subscription.is_active"""
        return sa.and_(
            cls.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED]),
            cls.expires_at > func.now(),
        )

    @hybrid_property
    def is_expired(self) -> bool:
        """True if expires_at has passed, regardless of status field."""
        return datetime.now(tz=timezone.utc) > self.expires_at

    @is_expired.expression
    def is_expired(cls):
        """SQL: WHERE Subscription.is_expired"""
        return cls.expires_at < func.now()

    @hybrid_property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    @is_cancelled.expression
    def is_cancelled(cls):
        return cls.cancelled_at.isnot(None)

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def days_until_expiry(self) -> int:
        """
        Days remaining in this subscription period.
        Returns 0 if already expired (never negative).
        Used by Celery to decide when to send renewal warnings.
        """
        delta = self.expires_at - datetime.now(tz=timezone.utc)
        return max(0, delta.days)

    @property
    def is_expiring_soon(self) -> bool:
        """
        True when expiry is within the warning window (default: 3 days).
        Celery subscription_check uses this to send renewal reminder notifications.
        """
        from app.constants import SUBSCRIPTION_WARNING_DAYS
        return self.is_active and self.days_until_expiry <= SUBSCRIPTION_WARNING_DAYS

    @property
    def renewal_amount_kobo(self) -> int:
        """
        Expected renewal amount in kobo based on plan type.
        Used when creating the renewal payment intent.

        Renter: ₦1,000 = 100,000 kobo
        Agent:  ₦10,000 = 10,000,000 kobo
        """
        from app.constants import RENTER_PLAN_PRICE_KOBO, AGENT_PLAN_PRICE_KOBO
        return (
            RENTER_PLAN_PRICE_KOBO
            if self.plan_type == SubscriptionPlan.RENTER
            else AGENT_PLAN_PRICE_KOBO
        )

    @property
    def renewal_amount_naira(self) -> int:
        """Renewal amount in naira for display."""
        return self.renewal_amount_kobo // 100

    # ── Factory method ────────────────────────────────────────────────────────

    @classmethod
    def create_from_payment(
        cls,
        payment: Payment,
        plan_type: SubscriptionPlan,
        duration_days: int = 30,
    ) -> Subscription:
        """
        Factory method — creates a Subscription from a confirmed Payment.
        Called by payment_service.py after Paystack webhook confirms success.

        Usage:
            sub = Subscription.create_from_payment(payment, SubscriptionPlan.AGENT)
            db.add(sub)

        For free subscriptions (no payment), construct manually:
            sub = Subscription(
                user_id=admin_id,
                plan_type=SubscriptionPlan.AGENT,
                status=SubscriptionStatus.ACTIVE,
                started_at=now,
                expires_at=now + timedelta(days=30),
                free_reason="Admin promotional grant",
            )
        """
        now = datetime.now(tz=timezone.utc)
        return cls(
            user_id=payment.user_id,
            payment_id=payment.id,
            plan_type=plan_type,
            status=SubscriptionStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=duration_days),
        )

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Subscription id={self.id} "
            f"user_id={self.user_id} "
            f"plan={self.plan_type.value!r} "
            f"status={self.status.value!r} "
            f"expires={self.expires_at.date()}>"
        )
