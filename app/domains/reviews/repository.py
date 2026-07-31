"""
domains/reviews/repository.py

Data access for property reviews and agent reviews.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.reviews.models import AgentReview, Review


class ReviewRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Property reviews ──────────────────────────────────────────────────────

    async def get_review_by_booking(self, booking_id: uuid.UUID) -> Review | None:
        """
        Checked before insert to give a clean ConflictException instead
        of relying solely on the UNIQUE(booking_id) constraint.
        """
        result = await self.db.execute(select(Review).where(Review.booking_id == booking_id))
        return result.scalar_one_or_none()

    async def list_for_property(
        self, property_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[Review], int]:
        base = (
            select(Review)
            .where(Review.property_id == property_id)
            .options(selectinload(Review.user))
        )
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            base.order_by(Review.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        )
        return list(result.scalars().all()), total

    async def get_rating_summary_for_property(
        self, property_id: uuid.UUID
    ) -> tuple[float | None, int]:
        """Returns (average_rating, review_count)."""
        result = await self.db.execute(
            select(func.avg(Review.rating), func.count(Review.id)).where(
                Review.property_id == property_id
            )
        )
        avg, count = result.one()
        return (round(float(avg), 1) if avg is not None else None), count

    async def create_review(
        self,
        user_id: uuid.UUID,
        property_id: uuid.UUID,
        booking_id: uuid.UUID,
        rating: int,
        body: str | None,
    ) -> Review:
        review = Review(
            user_id=user_id,
            property_id=property_id,
            booking_id=booking_id,
            rating=rating,
            body=body,
        )
        self.db.add(review)
        await self.db.flush()
        return review

    # ── Agent reviews ─────────────────────────────────────────────────────────

    async def get_agent_review_by_booking(self, booking_id: uuid.UUID) -> AgentReview | None:
        result = await self.db.execute(
            select(AgentReview).where(AgentReview.booking_id == booking_id)
        )
        return result.scalar_one_or_none()

    async def list_for_agent(
        self, agent_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[AgentReview], int]:
        base = (
            select(AgentReview)
            .where(AgentReview.agent_id == agent_id)
            .options(selectinload(AgentReview.reviewer))
        )
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            base.order_by(AgentReview.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total

    async def get_rating_summary_for_agent(self, agent_id: uuid.UUID) -> tuple[float | None, int]:
        result = await self.db.execute(
            select(func.avg(AgentReview.rating), func.count(AgentReview.id)).where(
                AgentReview.agent_id == agent_id
            )
        )
        avg, count = result.one()
        return (round(float(avg), 1) if avg is not None else None), count

    async def create_agent_review(
        self,
        reviewer_id: uuid.UUID,
        agent_id: uuid.UUID,
        booking_id: uuid.UUID,
        rating: int,
        body: str | None,
    ) -> AgentReview:
        review = AgentReview(
            reviewer_id=reviewer_id,
            agent_id=agent_id,
            booking_id=booking_id,
            rating=rating,
            body=body,
        )
        self.db.add(review)
        await self.db.flush()
        return review
