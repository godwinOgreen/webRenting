"""
domains/payments/router.py

HTTP layer for the payments domain.

The webhook endpoint is the one deliberate exception to "no business
logic in routers" — signature verification MUST happen against the raw
request body before Pydantic parses anything, which means this router
reads request.body() directly rather than declaring a Pydantic request
model as the endpoint parameter. Everything after verification still
delegates to the service.
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.domains.payments.schemas import (
    InitializePaymentRequest,
    InitializePaymentResponse,
    PaymentRead,
    PaystackWebhookPayload,
)
from app.domains.payments.service import PaymentService
from app.domains.users.models import User
from app.shared.schemas import PaginatedResponse, SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


# ── POST /payments/initialize ─────────────────────────────────────────────────

@router.post(
    "/initialize",
    response_model=SuccessResponse[InitializePaymentResponse],
    summary="Start a subscription payment via Paystack",
)
async def initialize(
    data: InitializePaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[InitializePaymentResponse]:
    svc = PaymentService(db)
    return SuccessResponse.ok(
        data=await svc.initialize_payment(current_user, data)
    )


# ── GET /payments/mine ────────────────────────────────────────────────────────

@router.get(
    "/mine",
    response_model=PaginatedResponse[PaymentRead],
    summary="List own payment history",
)
async def list_my_payments(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[PaymentRead]:
    svc = PaymentService(db)
    return await svc.list_for_user(current_user, page, per_page)


# ── GET /payments/{id} ─────────────────────────────────────────────────────────

@router.get(
    "/{payment_id}",
    response_model=SuccessResponse[PaymentRead],
    summary="Get own payment details",
)
async def get_payment(
    payment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PaymentRead]:
    svc = PaymentService(db)
    return SuccessResponse.ok(
        data=await svc.get_for_user(payment_id, current_user)
    )


# ── POST /payments/webhook ────────────────────────────────────────────────────

@router.post(
    "/webhook",
    status_code=200,
    summary="Paystack webhook receiver (no auth — verified via HMAC)",
    include_in_schema=False,  # internal endpoint, not part of the public API docs
)
async def paystack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_paystack_signature: str = Header(default=""),
) -> dict:
    """
    Receives Paystack webhook events.

    No authentication dependency — Paystack calls this directly, it
    cannot send a JWT. Trust is established entirely via HMAC signature
    verification against PAYSTACK_SECRET_KEY, which only Paystack and
    this server know.

    Always returns 200 once the signature is valid, even if the event
    type is ignored or the reference is unrecognised — Paystack retries
    aggressively on non-2xx responses, and we don't want retries for
    events we've deliberately chosen not to act on. Invalid signatures
    are the only case that gets a non-200 (401), causing Paystack to
    flag the endpoint, which is the correct signal for a genuine
    integration misconfiguration.

    Commits explicitly via db.commit() rather than relying on get_db()'s
    post-return commit — webhook processing should be durably saved
    before returning 200, since a 200 tells Paystack "stop retrying."
    If we returned 200 and the implicit commit then failed, the
    subscription would silently not exist despite Paystack believing
    the webhook was handled.
    """
    raw_body = await request.body()

    if not PaymentService.verify_webhook_signature(raw_body, x_paystack_signature):
        logger.warning("Paystack webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        raw_payload = json.loads(raw_body)
        payload = PaystackWebhookPayload.from_paystack_payload(raw_payload)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Malformed Paystack webhook payload", extra={"error": str(e)})
        # Still 200 — a malformed payload is not something retrying will fix.
        return {"received": True}

    svc = PaymentService(db)
    await svc.handle_webhook(payload)
    await db.commit()  # explicit — see docstring

    return {"received": True}
