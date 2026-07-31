"""
domains/reviews/schemas.py

Request/response schemas for property reviews and agent reviews.

Both review types share the same shape (rating + optional body) but
are kept as separate schema classes matching the separate Review and
AgentReview models — see reviews/models.py docstring (Phase 1) for
why they're not unified into one polymorphic table.

booking_id is required on both create schemas — the service validates
that the booking belongs to the requesting user AND is COMPLETED
(Booking.can_be_reviewed) before allowing the review, and that the
UNIQUE constraints haven't already been used.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.constants import MAX_RATING, MIN_RATING
from app.domains.users.schemas import UserPublicRead

# ── Property review ────────────────────────────────────────────────────────────


class ReviewCreate(BaseModel):
    """Request to create a property review for a completed booking."""

    booking_id: uuid.UUID
    rating: int = Field(..., ge=MIN_RATING, le=MAX_RATING)
    body: str | None = Field(None, max_length=3000)


class ReviewRead(BaseModel):
    """Read representation of a property review with reviewer info."""

    id: uuid.UUID
    property_id: uuid.UUID
    booking_id: uuid.UUID
    rating: int
    body: str | None = None
    reviewer: UserPublicRead
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Agent review ──────────────────────────────────────────────────────────────


class AgentReviewCreate(BaseModel):
    """Request to create an agent review for a completed booking."""

    booking_id: uuid.UUID
    rating: int = Field(..., ge=MIN_RATING, le=MAX_RATING)
    body: str | None = Field(None, max_length=3000)


class AgentReviewRead(BaseModel):
    """Read representation of an agent review with reviewer info."""

    id: uuid.UUID
    agent_id: uuid.UUID
    booking_id: uuid.UUID
    rating: int
    body: str | None = None
    reviewer: UserPublicRead
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Aggregate rating (embedded in property/agent public profiles) ─────────────


class RatingSummary(BaseModel):
    """
    Computed aggregate — not a stored column. Built by the service from
    a GROUP BY query (AVG(rating), COUNT(*)), used to show "4.5 ★ (23
    reviews)" on property and agent public profiles without the client
    fetching every individual review.
    """

    average_rating: float | None = None
    review_count: int = 0
