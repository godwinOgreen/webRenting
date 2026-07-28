"""
domains/subscriptions/repository.py

Data access for the subscriptions domain.

No create() method — subscriptions are created by payments/service.py
via Subscription.create_from_payment(), directly through that
service's AsyncSession.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.subscriptions.models import Subscription, SubscriptionStatus


class SubscriptionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_id(self, subscription_id: uuid.UUID) -> Subscription | None:
        return await self.db.get(Subscription, subscription_id)

    async def get_current_for_user(self, user_id: uuid.UUID) -> Subscription | None:
        """
        Most recent subscription row for a user, regardless of status.
        'Current' = latest by expires_at, not necessarily active.
        """
        result = await self.db.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: uuid.UUID) -> Subscription | None:
        """
        Same query used by permissions/guards.py require_subscription().
        """
        result = await self.db.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.is_active,
            )
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[Subscription], int]:
        total_stmt = select(func.count(Subscription.id)).where(Subscription.user_id == user_id)
        total = (await self.db.execute(total_stmt)).scalar_one()

        stmt = (
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.started_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total

    # ── Used by Celery subscription_check task ────────────────────────────────

    async def list_expiring_soon(self, within_days: int) -> list[Subscription]:
        """
        Active subscriptions whose expires_at falls within the next
        `within_days` days — used to send renewal reminder notifications.
        Uses database clock timestamp for timing consistency.
        """
        now_sql = func.clock_timestamp()
        cutoff_sql = now_sql + sa.text(f"INTERVAL '{within_days} days'")

        result = await self.db.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at <= cutoff_sql,
                Subscription.expires_at > now_sql,
            )
        )
        return list(result.scalars().all())

    async def list_expired_but_still_active_status(self) -> list[Subscription]:
        """
        Subscriptions whose expires_at has passed but status is still ACTIVE or CANCELLED.
        The Celery task transitions these to EXPIRED.
        """
        result = await self.db.execute(
            select(Subscription).where(
                Subscription.status.in_(
                    [
                        SubscriptionStatus.ACTIVE,
                        SubscriptionStatus.CANCELLED,
                    ]
                ),
                Subscription.expires_at <= func.clock_timestamp(),
            )
        )
        return list(result.scalars().all())

    # ── Writes ────────────────────────────────────────────────────────────────

    async def cancel(self, subscription: Subscription, reason: str | None) -> Subscription:
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.cancelled_at = datetime.now(tz=UTC)
        subscription.cancellation_reason = reason
        await self.db.flush()
        return subscription

    async def expire(self, subscription: Subscription) -> Subscription:
        """Used by Celery subscription_check task."""
        subscription.status = SubscriptionStatus.EXPIRED
        await self.db.flush()
        return subscription
