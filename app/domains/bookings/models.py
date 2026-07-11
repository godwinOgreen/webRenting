# app/domains/bookings/models.py

"""
Domain: Bookings
Tables: bookings, agent_availability
Enums: BookingType, BookingStatus

Two models in this file:
  - AgentAvailability — calendar slots agents create to define when they're free
  - Booking           — a renter's request to visit a property in a given slot

Why AgentAvailability lives in the bookings domain (not properties):
  It only exists to serve the booking flow. Without bookings, it has no purpose.
  The property domain doesn't need to know about it directly.

No-double-booking rule (Decision 13 / Rule 6):
  Before creating a Booking, booking_service checks:
      slot.is_booked == False
  On booking confirmation (agent confirms):
      slot.is_booked = True   → slot is now locked
  On rejection or cancellation:
      slot.is_booked = False  → slot freed for another renter

  There is intentionally NO FK from bookings → agent_availability.
  The service coordinates them via property_id + slot_start/end matching.
  This keeps the schema simple and avoids a circular dependency between
  the two tables in this file.

Bookings are FREE (Rule 6 / Decision 6):
  payment_id is nullable. A renter only needs an active subscription.
  The field is retained for possible future paid priority-booking feature.

Rejection requires a reason (Rule 7):
  Enforced in booking_service.reject(), not as a DB constraint.
  rejection_reason = None when status ≠ rejected.

Reviews after completion (Rule 16):
  Reviews (property + agent) are only possible after status = completed.
  Enforced by the UNIQUE (user_id, booking_id) constraint on reviews table
  and the booking_id FK on review tables.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer,
    String, Text, text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.domains.payments.models import Payment
    from app.domains.properties.models import Property
    from app.domains.reviews.models import AgentReview, Review
    from app.domains.users.models import User


# ─── Enums ───────────────────────────────────────────────────────────────────

class BookingType(str, enum.Enum):
    """
    How the visit takes place.

    PHYSICAL_INSPECTION — renter meets agent at the property in person.
    VIRTUAL             — renter joins a video call or 360° tour session.

    Both go through the same booking → confirm/reject → complete flow.
    The difference only matters for how the agent prepares and what
    the notification messages say.
    """
    PHYSICAL_INSPECTION = "physical_inspection"
    VIRTUAL = "virtual"


class BookingStatus(str, enum.Enum):
    """
    Full lifecycle of a booking request.

    Valid transitions (enforced in booking_service.py):
      PENDING   → CONFIRMED  (agent confirms — locks the availability slot)
      PENDING   → REJECTED   (agent rejects — rejection_reason required)
      PENDING   → CANCELLED  (renter cancels before agent responds)
      CONFIRMED → COMPLETED  (system marks after visit_time passes)
      CONFIRMED → CANCELLED  (renter cancels — frees the availability slot)

    Terminal states: REJECTED, COMPLETED.
    CANCELLED from CONFIRMED: agent slot is freed (is_booked → False).
    """
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


# ─── SQLAlchemy Enum column types ────────────────────────────────────────────

_booking_type_col = sa.Enum(
    BookingType,
    name="booking_type",
    values_callable=lambda obj: [e.value for e in obj],
)

_booking_status_col = sa.Enum(
    BookingStatus,
    name="booking_status",
    values_callable=lambda obj: [e.value for e in obj],
)


# ─── AgentAvailability Model ─────────────────────────────────────────────────

class AgentAvailability(Base, UUIDMixin, TimestampMixin):
    """
    A single time slot an agent has opened for property viewings.

    Agents create slots (e.g. "Saturday 10:00–11:00 for Flat 3A").
    Renters pick from available (is_booked=False) slots when booking.
    Confirmed booking → is_booked=True → slot locked.
    updated_at timestamps exactly when is_booked flips (v9.2 fix).

    Table: agent_availability

    One agent can have many slots across many properties.
    One property can have many slots across different times.
    One slot belongs to exactly one property.

    Invariant enforced by CHECK constraint: slot_end > slot_start.

    ondelete choices:
      agent_id    → RESTRICT  Prevent agent deletion while they have upcoming slots
      property_id → CASCADE   Property deleted → availability for that property is meaningless
    """

    __tablename__ = "agent_availability"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="The agent who owns this slot. RESTRICT: cannot delete agent with upcoming slots.",
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="CASCADE: property deleted → its availability slots are meaningless.",
    )
    slot_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="Start of the available window (timezone-aware).",
    )
    slot_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="End of the available window. Must be after slot_start.",
    )
    is_booked: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("false"),
        default=False,
        index=True,
        comment=(
            "False = slot is open. True = a confirmed booking holds this slot. "
            "updated_at timestamps exactly when this flips. "
            "booking_service checks is_booked=False before allowing a booking."
        ),
    )

    __table_args__ = (
        CheckConstraint(
            "slot_end > slot_start",
            name="chk_slot_order",
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    agent: Mapped[User] = relationship(
        "User",
        back_populates="availability_slots",
    )
    listing: Mapped[Property] = relationship(
        "Property",
        back_populates="availability_slots",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_available(self) -> bool:
        """Slot is open and in the future."""
        return (
            not self.is_booked
            and self.slot_start > datetime.now(tz=timezone.utc)
        )

    @is_available.expression
    def is_available(cls):
        """
        SQL: WHERE AgentAvailability.is_available

        This is THE most queried expression in the booking flow:
          stmt = select(AgentAvailability).where(
              AgentAvailability.property_id == property_id,
              AgentAvailability.is_available,
          )
        """
        return sa.and_(
            cls.is_booked == False,  # noqa: E712
            cls.slot_start > func.now(),
        )

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def is_in_past(self) -> bool:
        return self.slot_start < datetime.now(tz=timezone.utc)

    @property
    def duration_minutes(self) -> int:
        """Length of the slot in minutes."""
        delta = self.slot_end - self.slot_start
        return int(delta.total_seconds() / 60)

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<AgentAvailability id={self.id} "
            f"agent_id={self.agent_id} "
            f"slot_start={self.slot_start.isoformat()} "
            f"is_booked={self.is_booked}>"
        )


# ─── Booking Model ──────────────────────────────────────────────────────────

class Booking(Base, UUIDMixin, TimestampMixin):
    """
    A renter's request to visit a property at a specific time.

    Created when a subscriber renter picks an available AgentAvailability slot.
    The agent then confirms or rejects the request.

    Table: bookings

    Key rules:
      - payment_id is nullable — bookings are free (Rule 6 / Decision 6).
      - rejection_reason is required when status = rejected (Rule 7).
        Enforced in booking_service.reject(), not as a DB constraint.
      - Reviews (property + agent) are only possible after status = completed.
        Enforced by the UNIQUE (user_id, booking_id) constraint on reviews table
        and the booking_id FK on review tables.

    Relationship to AgentAvailability:
      No FK column here. The service coordinates the two:
        1. Find slot with is_booked=False for this property + time
        2. INSERT booking
        3. UPDATE agent_availability SET is_booked=True WHERE id=slot_id
      This is intentional — keeps the schema simpler and avoids a circular
      dependency between the two models in this file.
    """

    __tablename__ = "bookings"

    # ── Foreign keys ──────────────────────────────────────────────────────────
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="The renter making the booking.",
    )
    payment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "Nullable because bookings are free (Decision 6). "
            "Only an active subscription is required. "
            "Retained for possible future paid priority-booking feature."
        ),
    )

    # ── Visit details ─────────────────────────────────────────────────────────
    visit_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        index=True,
        comment="The agreed visit time. Must match an AgentAvailability slot.",
    )
    booking_type: Mapped[BookingType] = mapped_column(
        _booking_type_col, nullable=False,
        comment="physical_inspection (in-person) or virtual (video/360° tour).",
    )

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    status: Mapped[BookingStatus] = mapped_column(
        _booking_status_col, nullable=False,
        server_default=text("'pending'"),
        default=BookingStatus.PENDING,
        index=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True,
        comment=(
            "Required when status = rejected (Rule 7). "
            "Enforced by booking_service.reject() — not a DB constraint. "
            "Shown to the renter so they understand why the booking was declined."
        ),
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Optional message from the renter to the agent when booking.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    listing: Mapped[Property] = relationship(
        "Property",
        back_populates="bookings",
    )
    user: Mapped[User] = relationship(
        "User",
        back_populates="bookings",
        foreign_keys=[user_id],
    )
    payment: Mapped[Optional[Payment]] = relationship(
        "Payment",
        uselist=False,
    )

    # Reviews enabled by a completed booking
    # UNIQUE (user_id, booking_id) on reviews ensures one property review per booking (v9.2)
    reviews: Mapped[list[Review]] = relationship(
        "Review",
        back_populates="booking",
    )

    # UNIQUE (reviewer_id, booking_id) on agent_reviews ensures
    # one agent review per booking (v9.2)
    agent_reviews: Mapped[list[AgentReview]] = relationship(
        "AgentReview",
        back_populates="booking",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_pending(self) -> bool:
        return self.status == BookingStatus.PENDING

    @is_pending.expression
    def is_pending(cls):
        return cls.status == BookingStatus.PENDING

    @hybrid_property
    def is_confirmed(self) -> bool:
        return self.status == BookingStatus.CONFIRMED

    @is_confirmed.expression
    def is_confirmed(cls):
        return cls.status == BookingStatus.CONFIRMED

    @hybrid_property
    def is_active(self) -> bool:
        """
        Booking is in a state that still requires attention.
        Used in admin dashboard and agent's booking list.
        """
        return self.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED)

    @is_active.expression
    def is_active(cls):
        return cls.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED])

    @hybrid_property
    def visit_is_past(self) -> bool:
        """
        Used by Celery to auto-complete confirmed bookings after visit_time.
        A confirmed booking whose visit_time has passed is marked completed.

        Celery task:
            stmt = select(Booking).where(Booking.visit_is_past)
            for booking in (await db.execute(stmt)).scalars():
                booking.status = BookingStatus.COMPLETED
        """
        return (
            self.status == BookingStatus.CONFIRMED
            and self.visit_time < datetime.now(tz=timezone.utc)
        )

    @visit_is_past.expression
    def visit_is_past(cls):
        return sa.and_(
            cls.status == BookingStatus.CONFIRMED,
            cls.visit_time < func.now(),
        )

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def is_rejected(self) -> bool:
        return self.status == BookingStatus.REJECTED

    @property
    def is_cancelled(self) -> bool:
        return self.status == BookingStatus.CANCELLED

    @property
    def is_completed(self) -> bool:
        return self.status == BookingStatus.COMPLETED

    @property
    def is_terminal(self) -> bool:
        """
        Booking has reached a final state — no further transitions possible.
        Terminal bookings can still have reviews written against them.
        """
        return self.status in (BookingStatus.REJECTED, BookingStatus.COMPLETED)

    @property
    def can_be_reviewed(self) -> bool:
        """
        A renter can write a property review or agent review only after
        the booking is completed. Service layer checks this before
        allowing review creation.
        """
        return self.is_completed

    @property
    def visit_is_upcoming(self) -> bool:
        """True if the visit hasn't happened yet."""
        return self.visit_time > datetime.now(tz=timezone.utc)

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Booking id={self.id} "
            f"property_id={self.property_id} "
            f"user_id={self.user_id} "
            f"status={self.status.value!r} "
            f"visit={self.visit_time.date()}>"
        )
