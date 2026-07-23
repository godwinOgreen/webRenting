# app/domains/subscriptions/router.py

"""
domains/subscriptions/router.py

HTTP layer for the subscriptions domain. No POST /subscriptions
creation endpoint — see schemas.py and service.py docstrings.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.core.redis_client import get_redis
from app.domains.subscriptions.schemas import (
    SubscriptionCancelRequest,
    SubscriptionRead,
)
from app.domains.subscriptions.service import SubscriptionService
from app.domains.users.models import User
from app.shared.schemas import PaginatedResponse, SuccessResponse

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


# ── GET /subscriptions/current ────────────────────────────────────────────────

@router.get(
    "/current",
    response_model=SuccessResponse[SubscriptionRead],
    summary="Get own most recent subscription (active or not)",
)
async def get_current(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[SubscriptionRead]:
    svc = SubscriptionService(db)
    return SuccessResponse.ok(data=await svc.get_current(current_user))


# ── GET /subscriptions/history ────────────────────────────────────────────────

@router.get(
    "/history",
    response_model=PaginatedResponse[SubscriptionRead],
    summary="List own subscription history",
)
async def list_history(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[SubscriptionRead]:
    svc = SubscriptionService(db)
    return await svc.list_history(current_user, page, per_page)


# ── POST /subscriptions/{id}/cancel ───────────────────────────────────────────

@router.post(
    "/{subscription_id}/cancel",
    response_model=SuccessResponse[SubscriptionRead],
    status_code=status.HTTP_200_OK,
    summary="Cancel own subscription (access continues until expiry)",
)
async def cancel(
    subscription_id: uuid.UUID,
    data: SubscriptionCancelRequest = SubscriptionCancelRequest(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> SuccessResponse[SubscriptionRead]:
    svc = SubscriptionService(db, redis)
    cancelled_sub = await svc.cancel(
        subscription_id, current_user, data.cancellation_reason
    )
    return SuccessResponse.ok(
        data=cancelled_sub,
        message=(
            "Subscription cancelled. You will retain access until "
            "the end of the current billing period."
        ),
    )