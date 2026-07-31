"""
domains/reviews/service.py

Business logic for property reviews and agent reviews.


──────────────────────────────────────────────────────────────────────
Clarification on the cross-domain import rule as applied here:

  01_ARCHITECTURE.md §3 says:
    ✅ Service can import models from any domain
    ❌ Service CANNOT import repositories from other domains

  This service needs to check a Booking's ownership and completion
  status. Rather than importing BookingRepository (forbidden), it
  queries the Booking MODEL directly via this domain's own
  AsyncSession using SQLAlchemy select() — permitted, since services
  may use models from any domain with their own queries. What's
  forbidden is reaching into another domain's REPOSITORY or SERVICE
  class. This keeps ReviewService self-contained and compliant.
──────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.domains.bookings.models import Booking
from app.domains.properties.models import Property
from app.domains.reviews.models import AgentReview, Review
from app.domains.reviews.repository import ReviewRepository
from app.domains.reviews.schemas import (
    AgentReviewCreate,
    AgentReviewRead,
    RatingSummary,
    ReviewCreate,
    ReviewRead,
)
from app.domains.users.models import User
from app.domains.users.schemas import UserPublicRead
from app.shared.schemas import PaginatedResponse

logger = logging.getLogger(__name__)


class ReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = ReviewRepository(db)

    # ── Property reviews ──────────────────────────────────────────────────────

    async def create_review(self, user: User, data: ReviewCreate) -> ReviewRead:
        booking = await self._get_completed_booking_or_raise(data.booking_id, user.id)

        existing = await self.repo.get_review_by_booking(data.booking_id)
        if existing is not None:
            raise ConflictException(
                message="You have already reviewed this booking",
                error_code="already_reviewed",
            )

        review = await self.repo.create_review(
            user_id=user.id,
            property_id=booking.property_id,
            booking_id=data.booking_id,
            rating=data.rating,
            body=data.body,
        )
        logger.info(
            "Property review created",
            extra={
                "review_id": str(review.id),
                "property_id": str(booking.property_id),
            },
        )
        return self._review_to_read(review, user)

    async def list_for_property(
        self, property_id: uuid.UUID, page: int, per_page: int
    ) -> PaginatedResponse[ReviewRead]:
        reviews, total = await self.repo.list_for_property(property_id, page, per_page)
        items = [self._review_to_read(r, r.user) for r in reviews]
        return PaginatedResponse.paginate(items, total, page, per_page)

    async def get_property_rating_summary(self, property_id: uuid.UUID) -> RatingSummary:
        avg, count = await self.repo.get_rating_summary_for_property(property_id)
        return RatingSummary(average_rating=avg, review_count=count)

    # ── Agent reviews ─────────────────────────────────────────────────────────

    async def create_agent_review(self, user: User, data: AgentReviewCreate) -> AgentReviewRead:
        booking = await self._get_completed_booking_or_raise(data.booking_id, user.id)

        result = await self.db.execute(
            select(Property.owner_id).where(Property.id == booking.property_id)
        )
        agent_id = result.scalar_one()

        if agent_id == user.id:
            raise ForbiddenException(
                message="You cannot leave an agent review for yourself",
                error_code="self_review_not_allowed",
            )

        existing = await self.repo.get_agent_review_by_booking(data.booking_id)
        if existing is not None:
            raise ConflictException(
                message="You have already reviewed this agent for this booking",
                error_code="already_reviewed",
            )

        review = await self.repo.create_agent_review(
            reviewer_id=user.id,
            agent_id=agent_id,
            booking_id=data.booking_id,
            rating=data.rating,
            body=data.body,
        )
        logger.info(
            "Agent review created",
            extra={"review_id": str(review.id), "agent_id": str(agent_id)},
        )
        return self._agent_review_to_read(review, user)

    async def list_for_agent(
        self, agent_id: uuid.UUID, page: int, per_page: int
    ) -> PaginatedResponse[AgentReviewRead]:
        reviews, total = await self.repo.list_for_agent(agent_id, page, per_page)
        items = [self._agent_review_to_read(r, r.reviewer) for r in reviews]
        return PaginatedResponse.paginate(items, total, page, per_page)

    async def get_agent_rating_summary(self, agent_id: uuid.UUID) -> RatingSummary:
        avg, count = await self.repo.get_rating_summary_for_agent(agent_id)
        return RatingSummary(average_rating=avg, review_count=count)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_completed_booking_or_raise(
        self, booking_id: uuid.UUID, user_id: uuid.UUID
    ) -> Booking:
        result = await self.db.execute(
            select(Booking).where(Booking.id == booking_id, Booking.user_id == user_id)
        )
        booking = result.scalar_one_or_none()
        if booking is None:
            raise NotFoundException(message="Booking not found")
        if not booking.can_be_reviewed:
            raise ForbiddenException(
                message="This booking must be completed before it can be reviewed",
                error_code="booking_not_completed",
            )
        return booking

    def _review_to_read(self, review: Review, user: User) -> ReviewRead:
        return ReviewRead(
            id=review.id,
            property_id=review.property_id,
            booking_id=review.booking_id,
            rating=review.rating,
            body=review.body,
            reviewer=UserPublicRead.model_validate(user),
            created_at=review.created_at,
        )

    def _agent_review_to_read(self, review: AgentReview, user: User) -> AgentReviewRead:
        return AgentReviewRead(
            id=review.id,
            agent_id=review.agent_id,
            booking_id=review.booking_id,
            rating=review.rating,
            body=review.body,
            reviewer=UserPublicRead.model_validate(user),
            created_at=review.created_at,
        )
