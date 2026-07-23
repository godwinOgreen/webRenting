"""
domains/subscriptions/service.py

Business logic for the subscriptions domain.

No create() — subscriptions are created by payments/service.py via
Subscription.create_from_payment() after a successful Paystack charge.
This service only reads and cancels.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException
from app.domains.subscriptions.models import Subscription, SubscriptionStatus
from app.domains.subscriptions.repository import SubscriptionRepository
from app.domains.subscriptions.schemas import SubscriptionRead
from app.domains.users.models import User
from app.shared.schemas import PaginatedResponse

logger = logging.getLogger(__name__)


class SubscriptionService:
    def __init__(self, db: AsyncSession, redis: Optional[Redis] = None) -> None:
        self.db = db
        self.redis = redis
        self.repo = SubscriptionRepository(db)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_current(self, user: User) -> SubscriptionRead:
        """
        Most recent subscription, active or not — for the account
        subscription page. Distinct from the access-gating check in
        permissions/guards.py, which only cares about ACTIVE ones.
        """
        sub = await self.repo.get_current_for_user(user.id)
        if sub is None:
            raise NotFoundException(
                message="No subscription found",
                error_code="no_subscription",
            )
        return SubscriptionRead.model_validate(sub)

    async def list_history(
        self, user: User, page: int, per_page: int
    ) -> PaginatedResponse[SubscriptionRead]:
        subs, total = await self.repo.list_for_user(user.id, page, per_page)
        items = [SubscriptionRead.model_validate(s) for s in subs]
        return PaginatedResponse.paginate(items, total, page, per_page)

    # ── Cancel ────────────────────────────────────────────────────────────────

    async def cancel(
        self,
        subscription_id: uuid.UUID,
        user: User,
        reason: Optional[str],
    ) -> SubscriptionRead:
        """
        Cancel a subscription. Access continues until expires_at —
        standard SaaS behaviour. is_active continues returning True
        until expires_at passes.

        Raises:
            NotFoundException: subscription doesn't exist or isn't owned by user
            ConflictException: subscription is already cancelled or expired
        """
        sub = await self.repo.get_by_id(subscription_id)
        if sub is None or sub.user_id != user.id:
            raise NotFoundException(message="Subscription not found")

        if sub.status != SubscriptionStatus.ACTIVE:
            raise ConflictException(
                message=f"Cannot cancel a subscription with status '{sub.status.value}'",
                error_code="invalid_state_transition",
            )

        updated = await self.repo.cancel(sub, reason)

        # Invalidate cached subscription status in Redis
        if self.redis:
            from app.core.redis_client import RedisKeys
            try:
                await self.redis.delete(
                    RedisKeys.active_subscription(str(user.id))
                )
            except Exception as e:
                logger.warning(
                    "Failed to clear Redis subscription cache",
                    extra={"user_id": str(user.id), "error": str(e)},
                )

        logger.info(
            "Subscription cancelled",
            extra={
                "subscription_id": str(subscription_id),
                "user_id": str(user.id),
            },
        )
        return SubscriptionRead.model_validate(updated)