# app/domains/reviews/models.py

"""
Domain: Reviews
Tables: reviews, agent_reviews
No enums (rating is an Integer with CHECK constraint)

Two models that handle all review functionality on the platform.

Review       — a renter's review of a PROPERTY after a completed booking
AgentReview  — a renter's review of an AGENT after a completed booking

Why two separate tables instead of one polymorphic review table:
  1. Property reviews and agent reviews have different FK targets
     (property_id vs agent_id). Separate tables make FK constraints clean.
  2. Each has its own UNIQUE constraint preventing duplicates:
     reviews:         UNIQUE (user_id, booking_id)     — one property review per booking
     agent_reviews:   UNIQUE (reviewer_id, booking_id)  — one agent review per booking
  3. Queries are cleaner: "get all reviews for property X" vs
     "get all reviews where reviewable_type='property' AND reviewable_id=X"
  4. The two tables will never merge into one — they represent different domains.

Reviews require a completed booking (Rule 16):
  Both models have a booking_id FK to the bookings table.
  Before allowing review creation, the service checks:
    booking.status == BookingStatus.COMPLETED
  The UNIQUE constraint also prevents double-reviewing (v9.2 fix):
    - User can't write two property reviews for the same booking
    - User can't write two agent reviews for the same booking

Reviews are immutable:
  Uses CreatedAtMixin (not TimestampMixin).
  Once a review is written, it cannot be edited.
  If a user wants to change their review, they must contact support.
  This prevents agents from pressuring renters to change reviews.

Agent self-review prevention:
  CHECK constraint: agent_id <> reviewer_id
  An agent cannot review themselves. Enforced at database level.

Rating range:
  CHECK constraint: rating >= 1 AND rating <= 5
  Star ratings from 1 (worst) to 5 (best).
  No half-stars or decimals — integer only.

Average rating calculation (in review_service):
  Property average: SELECT AVG(rating) FROM reviews WHERE property_id = ?
  Agent average:    SELECT AVG(rating) FROM agent_reviews WHERE agent_id = ?
  Cached on the Property/User model (future optimization) or calculated on read.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import MAX_RATING, MIN_RATING
from app.db.base import Base
from app.db.mixins import CreatedAtMixin, UUIDMixin

if TYPE_CHECKING:
    from app.domains.bookings.models import Booking
    from app.domains.properties.models import Property
    from app.domains.users.models import User


# ─── Review Model ────────────────────────────────────────────────────────────


class Review(Base, UUIDMixin, CreatedAtMixin):
    """
    A renter's review of a PROPERTY after a completed booking.

    Table: reviews

    One review per booking per user (enforced by UNIQUE constraint).
    Requires a completed booking (enforced in review_service).
    Immutable once written (no updated_at).

    Average property rating (in repository):
        stmt = select(func.avg(Review.rating)).where(Review.property_id == property_id)
    """

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("user_id", "booking_id", name="uq_review_per_booking"),
        CheckConstraint(
            f"rating >= {MIN_RATING} AND rating <= {MAX_RATING}",
            name="chk_review_rating",
        ),
    )

    # ── Who wrote the review ──────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "The renter who wrote this review. "
            "RESTRICT: reviews are permanent records — cannot delete user who wrote them. "
            "Indexed: 'get all reviews by user X' queries."
        ),
    )

    # ── What is being reviewed ────────────────────────────────────────────────
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "The property being reviewed. "
            "RESTRICT: reviews are permanent records. "
            "Indexed: 'get all reviews for property X' queries (most common)."
        ),
    )

    # ── Which booking enabled this review ─────────────────────────────────────
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bookings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "The completed booking that enabled this review. "
            "RESTRICT: reviews are permanent records. "
            "Part of UNIQUE constraint (user_id, booking_id) — one review per booking."
        ),
    )

    # ── Review content ────────────────────────────────────────────────────────
    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            f"Star rating from {MIN_RATING} to {MAX_RATING}. "
            "Integer only — no half-stars. "
            "Enforced by chk_review_rating CHECK constraint."
        ),
    )
    body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Written review text. Optional — user may submit rating only.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="reviews_written",
    )
    listing: Mapped[Property] = relationship(
        "Property",
        back_populates="reviews",
    )
    booking: Mapped[Booking] = relationship(
        "Booking",
        back_populates="reviews",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_positive(self) -> bool:
        """Rating of 4 or 5 is considered positive."""
        return self.rating >= 4

    @is_positive.expression
    def is_positive(cls):
        """
        SQL: WHERE Review.is_positive

        Used in property quality score calculation:
            positive_count = select(func.count()).where(
                Review.property_id == property_id,
                Review.is_positive,
            )
        """
        return cls.rating >= 4

    @hybrid_property
    def is_negative(self) -> bool:
        """Rating of 1 or 2 is considered negative."""
        return self.rating <= 2

    @is_negative.expression
    def is_negative(cls):
        """SQL: WHERE Review.is_negative"""
        return cls.rating <= 2

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def has_body(self) -> bool:
        """True if the review includes written text (not just a star rating)."""
        return self.body is not None and len(self.body.strip()) > 0

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Review id={self.id} "
            f"property_id={self.property_id} "
            f"user_id={self.user_id} "
            f"rating={self.rating}>"
        )


# ─── AgentReview Model ───────────────────────────────────────────────────────


class AgentReview(Base, UUIDMixin, CreatedAtMixin):
    """
    A renter's review of an AGENT after a completed booking.

    Table: agent_reviews

    One agent review per booking per user (enforced by UNIQUE constraint).
    Agent cannot review themselves (enforced by CHECK constraint).
    Requires a completed booking (enforced in review_service).
    Immutable once written (no updated_at).

    Why separate from Review:
      Review is about the PROPERTY (location, condition, amenities).
      AgentReview is about the AGENT (responsiveness, professionalism, knowledge).
      A renter may love the property but hate the agent, or vice versa.
      Separating them allows independent ratings and different visibility rules.

    Why agent_id + reviewer_id instead of agent_id + user_id:
      The column is named reviewer_id (not user_id) to distinguish from
      the property review's user_id column. Both refer to the renter,
      but different names make queries unambiguous:
        Review.user_id         → the renter who reviewed the property
        AgentReview.reviewer_id → the renter who reviewed the agent
    """

    __tablename__ = "agent_reviews"
    __table_args__ = (
        UniqueConstraint("reviewer_id", "booking_id", name="uq_agent_review_per_booking"),
        CheckConstraint(
            f"rating >= {MIN_RATING} AND rating <= {MAX_RATING}",
            name="chk_agent_review_rating",
        ),
        CheckConstraint(
            "agent_id <> reviewer_id",
            name="chk_agent_not_self_review",
        ),
    )

    # ── Who is being reviewed ─────────────────────────────────────────────────
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "The agent being reviewed. "
            "RESTRICT: reviews are permanent records. "
            "Indexed: 'get all reviews for agent X' queries (most common — shown on agent profile)."
        ),
    )

    # ── Who wrote the review ──────────────────────────────────────────────────
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "The renter who wrote this review. Named reviewer_id (not user_id) "
            "to avoid ambiguity with Review.user_id. Both refer to the renter. "
            "Part of UNIQUE constraint (reviewer_id, booking_id) — one review per booking."
        ),
    )

    # ── Which booking enabled this review ─────────────────────────────────────
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bookings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "The completed booking that enabled this review. "
            "RESTRICT: reviews are permanent records. "
            "Part of UNIQUE constraint (reviewer_id, booking_id) — one review per booking."
        ),
    )

    # ── Review content ────────────────────────────────────────────────────────
    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            f"Star rating from {MIN_RATING} to {MAX_RATING}. "
            "Integer only — no half-stars. "
            "Enforced by chk_agent_review_rating CHECK constraint."
        ),
    )
    body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Written review text. Optional — user may submit rating only.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    agent: Mapped[User] = relationship(
        "User",
        back_populates="agent_reviews_received",
        foreign_keys=[agent_id],
    )
    reviewer: Mapped[User] = relationship(
        "User",
        back_populates="agent_reviews_written",
        foreign_keys=[reviewer_id],
    )
    booking: Mapped[Booking] = relationship(
        "Booking",
        back_populates="agent_reviews",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_positive(self) -> bool:
        """Rating of 4 or 5 is considered positive."""
        return self.rating >= 4

    @is_positive.expression
    def is_positive(cls):
        """
        SQL: WHERE AgentReview.is_positive

        Used in agent quality score calculation:
            positive_count = select(func.count()).where(
                AgentReview.agent_id == agent_id,
                AgentReview.is_positive,
            )
        """
        return cls.rating >= 4

    @hybrid_property
    def is_negative(self) -> bool:
        """Rating of 1 or 2 is considered negative."""
        return self.rating <= 2

    @is_negative.expression
    def is_negative(cls):
        """SQL: WHERE AgentReview.is_negative"""
        return cls.rating <= 2

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def has_body(self) -> bool:
        """True if the review includes written text (not just a star rating)."""
        return self.body is not None and len(self.body.strip()) > 0

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<AgentReview id={self.id} "
            f"agent_id={self.agent_id} "
            f"reviewer_id={self.reviewer_id} "
            f"rating={self.rating}>"
        )
