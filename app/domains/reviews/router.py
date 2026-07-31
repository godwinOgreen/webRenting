"""
domains/reviews/router.py

HTTP layer for property reviews and agent reviews. No business logic.

Review creation only requires get_current_user (not require_subscription
or require_kyc) — by the time a booking exists and is COMPLETED, the
user already passed those gates when the booking was created (Rule 3).
Re-checking subscription/KYC status at review time would be redundant
and could incorrectly block a review if the user's subscription lapsed
AFTER completing the booking but BEFORE leaving a review — access to
review your own completed booking should not depend on a still-active
subscription.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.domains.reviews.schemas import (
    AgentReviewCreate,
    AgentReviewRead,
    RatingSummary,
    ReviewCreate,
    ReviewRead,
)
from app.domains.reviews.service import ReviewService
from app.domains.users.models import User
from app.shared.schemas import PaginatedResponse, SuccessResponse

router = APIRouter(tags=["reviews"])


# ── Property reviews ─────────────────────────────────────────────────────────


@router.post(
    "/reviews",
    response_model=SuccessResponse[ReviewRead],
    status_code=201,
    summary="Review a property (requires a completed booking)",
)
async def create_review(
    data: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[ReviewRead]:
    svc = ReviewService(db)
    return SuccessResponse.ok(
        data=await svc.create_review(current_user, data),
        message="Review submitted",
    )


@router.get(
    "/properties/{property_id}/reviews",
    response_model=PaginatedResponse[ReviewRead],
    summary="List reviews for a property (public)",
)
async def list_property_reviews(
    property_id: uuid.UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ReviewRead]:
    svc = ReviewService(db)
    return await svc.list_for_property(property_id, page, per_page)


@router.get(
    "/properties/{property_id}/reviews/summary",
    response_model=SuccessResponse[RatingSummary],
    summary="Get average rating + review count for a property (public)",
)
async def get_property_rating_summary(
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[RatingSummary]:
    svc = ReviewService(db)
    return SuccessResponse.ok(data=await svc.get_property_rating_summary(property_id))


# ── Agent reviews ─────────────────────────────────────────────────────────────


@router.post(
    "/agent-reviews",
    response_model=SuccessResponse[AgentReviewRead],
    status_code=201,
    summary="Review an agent (requires a completed booking)",
)
async def create_agent_review(
    data: AgentReviewCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[AgentReviewRead]:
    svc = ReviewService(db)
    return SuccessResponse.ok(
        data=await svc.create_agent_review(current_user, data),
        message="Agent review submitted",
    )


@router.get(
    "/agents/{agent_id}/reviews",
    response_model=PaginatedResponse[AgentReviewRead],
    summary="List reviews for an agent (public)",
)
async def list_agent_reviews(
    agent_id: uuid.UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AgentReviewRead]:
    svc = ReviewService(db)
    return await svc.list_for_agent(agent_id, page, per_page)


@router.get(
    "/agents/{agent_id}/reviews/summary",
    response_model=SuccessResponse[RatingSummary],
    summary="Get average rating + review count for an agent (public)",
)
async def get_agent_rating_summary(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[RatingSummary]:
    svc = ReviewService(db)
    return SuccessResponse.ok(data=await svc.get_agent_rating_summary(agent_id))
