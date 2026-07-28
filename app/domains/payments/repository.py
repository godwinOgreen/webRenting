"""
domains/payments/repository.py

Data access for the payments domain.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.payments.models import Payment, PaymentStatus


class PaymentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_id(self, payment_id: uuid.UUID) -> Payment | None:
        return await self.db.get(Payment, payment_id)

    async def get_by_reference(self, paystack_reference: str) -> Payment | None:
        """Look up by Paystack reference. UNIQUE constraint ensures at most one match."""
        result = await self.db.execute(
            select(Payment).where(Payment.paystack_reference == paystack_reference)
        )
        return result.scalar_one_or_none()

    async def get_by_reference_for_update(self, paystack_reference: str) -> Payment | None:
        """
        Pessimistic row-level lock on the payment record.
        Prevents race conditions when processing concurrent Paystack webhooks.
        """
        result = await self.db.execute(
            select(Payment)
            .where(Payment.paystack_reference == paystack_reference)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[Payment], int]:
        # Clean count query without unnecessary subquery wrapping
        count_stmt = select(func.count()).select_from(Payment).where(Payment.user_id == user_id)
        total = (await self.db.execute(count_stmt)).scalar_one()

        # Paginated results query
        query = (
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self.db.execute(query)

        return list(result.scalars().all()), total

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create(
        self,
        user_id: uuid.UUID,
        amount: int,
        subscription_type: str,
        paystack_reference: str,
    ) -> Payment:
        """
        Insert a new pending Payment row. paystack_reference is set
        immediately — generated on our side and passed to Paystack's
        initialize API, so the webhook handler can match from the start.
        """
        payment = Payment(
            user_id=user_id,
            amount=amount,
            subscription_type=subscription_type,
            paystack_reference=paystack_reference,
            status=PaymentStatus.PENDING,
        )
        self.db.add(payment)
        await self.db.flush()
        return payment

    async def mark_success(self, payment: Payment, paid_at: datetime) -> Payment:
        payment.status = PaymentStatus.SUCCESSFUL
        payment.paid_at = paid_at
        await self.db.flush()
        return payment

    async def mark_failed(self, payment: Payment) -> Payment:
        payment.status = PaymentStatus.FAILED
        await self.db.flush()
        return payment
