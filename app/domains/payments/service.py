"""
domains/payments/service.py

Business logic for payments — Paystack initialization and webhook handling.

Cross-domain note: this service creates a Subscription row directly via
Subscription.create_from_payment(). Services may import MODELS from other
domains but not other domains' services or repositories.

Idempotency: handle_webhook() checks the payment's current status before
acting — if already SUCCESS, the webhook is a no-op (replay protection).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    AGENT_PLAN_PRICE_KOBO,
    RENTER_PLAN_PRICE_KOBO,
    SUBSCRIPTION_DURATION_DAYS,
)
from app.core.config import settings
from app.core.exceptions import NotFoundException, ValidationException
from app.domains.payments.models import PaymentStatus
from app.domains.payments.repository import PaymentRepository
from app.domains.payments.schemas import (
    InitializePaymentRequest,
    InitializePaymentResponse,
    PaymentRead,
    PaystackWebhookPayload,
)
from app.domains.subscriptions.models import Subscription, SubscriptionPlan
from app.domains.users.models import User
from app.shared.schemas import PaginatedResponse

logger = logging.getLogger(__name__)

_PLAN_PRICES_KOBO: dict[str, int] = {
    "renter": RENTER_PLAN_PRICE_KOBO,
    "agent": AGENT_PLAN_PRICE_KOBO,
}


class PaymentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = PaymentRepository(db)

    # ── Initialize ────────────────────────────────────────────────────────────

    async def initialize_payment(
        self,
        user: User,
        data: InitializePaymentRequest,
    ) -> InitializePaymentResponse:
        """
        Start a Paystack transaction for a subscription purchase.
        Amount is looked up server-side — never from the client.
        """
        amount_kobo = _PLAN_PRICES_KOBO.get(data.plan_type)
        if amount_kobo is None:
            raise ValidationException(
                message="Unknown plan type",
                errors={"plan_type": [f"Must be one of: {list(_PLAN_PRICES_KOBO)}"]},
            )

        reference = f"PLT-{uuid.uuid4().hex[:20]}"

        payment = await self.repo.create(
            user_id=user.id,
            amount=amount_kobo,
            subscription_type=data.plan_type,
            paystack_reference=reference,
        )

        from app.integrations.paystack import initialize_transaction

        result = await initialize_transaction(
            email=user.email,
            amount_kobo=amount_kobo,
            reference=reference,
        )

        logger.info(
            "Payment initialized",
            extra={
                "payment_id": str(payment.id),
                "user_id": str(user.id),
                "plan_type": data.plan_type,
                "reference": reference,
            },
        )

        return InitializePaymentResponse(
            payment_id=payment.id,
            authorization_url=result["authorization_url"],
            access_code=result["access_code"],
            reference=reference,
        )

    # ── Webhook ───────────────────────────────────────────────────────────────

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
        """
        Verify Paystack's X-Paystack-Signature: HMAC-SHA512 of the raw
        request body, keyed with PAYSTACK_SECRET_KEY.
        """
        if not signature_header:
            return False

        computed = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(computed, signature_header)

    async def handle_webhook(self, payload: PaystackWebhookPayload) -> None:
        """
        Process a verified Paystack webhook event.
        Idempotent — already-successful payments are no-ops.
        """
        if payload.event != "charge.success":
            logger.info(
                "Ignoring non-charge.success webhook event",
                extra={"event": payload.event},
            )
            return

        payment = await self.repo.get_by_reference_for_update(payload.reference)
        if payment is None:
            logger.warning(
                "Webhook reference does not match any payment",
                extra={"reference": payload.reference},
            )
            return

        if payment.status == PaymentStatus.SUCCESSFUL:
            logger.info(
                "Webhook for already-successful payment — no-op",
                extra={"payment_id": str(payment.id)},
            )
            return

        if payload.status != "success":
            await self.repo.mark_failed(payment)
            logger.info(
                "Payment marked failed via webhook",
                extra={"payment_id": str(payment.id)},
            )
            return

        # Explicitly abort execution if paid amount does not match expected amount
        if payment.amount != payload.amount:
            logger.error(
                "Payment amount mismatch! Potential tampering.",
                extra={
                    "payment_id": str(payment.id),
                    "expected_kobo": payment.amount,
                    "received_kobo": payload.amount,
                },
            )
            await self.repo.mark_failed(payment)
            return

        paid_at = datetime.now(tz=UTC)
        await self.repo.mark_success(payment, paid_at)

        # Validate subscription plan type
        try:
            plan = SubscriptionPlan(payment.subscription_type or "")
        except ValueError:
            logger.error(
                "Invalid subscription_type on payment row",
                extra={
                    "payment_id": str(payment.id),
                    "subscription_type": payment.subscription_type,
                },
            )
            return

        subscription = Subscription.create_from_payment(
            payment, plan, duration_days=SUBSCRIPTION_DURATION_DAYS
        )
        self.db.add(subscription)
        await self.db.flush()

        logger.info(
            "Payment succeeded, subscription created",
            extra={
                "payment_id": str(payment.id),
                "subscription_id": str(subscription.id),
                "user_id": str(payment.user_id),
            },
        )

    # ── Read ──────────────────────────────────────────────────────────────────

    async def list_for_user(
        self, user: User, page: int, per_page: int
    ) -> PaginatedResponse[PaymentRead]:
        payments, total = await self.repo.list_for_user(user.id, page, per_page)
        items = [PaymentRead.model_validate(p) for p in payments]
        return PaginatedResponse.paginate(items, total, page, per_page)

    async def get_for_user(self, payment_id: uuid.UUID, user: User) -> PaymentRead:
        payment = await self.repo.get_by_id(payment_id)
        if payment is None or payment.user_id != user.id:
            raise NotFoundException(message="Payment not found")
        return PaymentRead.model_validate(payment)
