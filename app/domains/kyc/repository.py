"""
domains/kyc/repository.py

Data access for KYC documents and admin reviews.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.kyc.models import (
    KycAdminReview,
    KycDocument,
    KycDocumentStatus,
    KycReviewDecision,
)


class KycRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Document reads ────────────────────────────────────────────────────────

    async def get_by_id(self, document_id: uuid.UUID) -> KycDocument | None:
        return await self.db.get(KycDocument, document_id)

    async def get_by_id_for_user(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> KycDocument | None:
        result = await self.db.execute(
            select(KycDocument).where(KycDocument.id == document_id, KycDocument.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[KycDocument]:
        result = await self.db.execute(
            select(KycDocument)
            .where(KycDocument.user_id == user_id)
            .order_by(KycDocument.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_latest_for_user(self, user_id: uuid.UUID) -> KycDocument | None:
        """
        Most recent submission for a user — what drives their current
        KYC state (used by kyc/service.py to check for an already-
        pending submission and to answer "what's my current status").
        """
        result = await self.db.execute(
            select(KycDocument)
            .where(KycDocument.user_id == user_id)
            .order_by(KycDocument.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_provider_reference(self, provider_reference: str) -> KycDocument | None:
        """
        Used by the provider webhook handler to match callbacks.

        Returns the LATEST document with this reference — providers may
        reuse references across resubmissions.
        """
        result = await self.db.execute(
            select(KycDocument)
            .where(KycDocument.provider_reference == provider_reference)
            .order_by(KycDocument.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_pending_queue(self, page: int, per_page: int) -> tuple[list[KycDocument], int]:
        """Admin moderation queue — documents awaiting review."""
        base = (
            select(KycDocument)
            .where(KycDocument.status == KycDocumentStatus.PENDING)
            .options(selectinload(KycDocument.user))
        )
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            base.order_by(KycDocument.created_at)  # oldest first — FIFO queue
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total

    # ── Document writes ───────────────────────────────────────────────────────

    async def create(
        self,
        user_id: uuid.UUID,
        document_type: str,
        document_url: str,
        provider: str,
        provider_reference: str,
    ) -> KycDocument:
        doc = KycDocument(
            user_id=user_id,
            document_type=document_type,
            document_url=document_url,
            provider=provider,
            provider_reference=provider_reference,
            status=KycDocumentStatus.PENDING,
        )
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def update_provider_result(
        self,
        doc: KycDocument,
        status: KycDocumentStatus,
        provider_response: dict,
        rejection_reason: str | None = None,
    ) -> KycDocument:
        """Used exclusively by the KYC provider webhook handler."""
        doc.status = status
        doc.provider_response = provider_response
        if rejection_reason is not None:
            doc.rejection_reason = rejection_reason
        await self.db.flush()
        return doc

    # ── Admin review ──────────────────────────────────────────────────────────

    async def create_admin_review(
        self,
        kyc_document_id: uuid.UUID,
        admin_id: uuid.UUID,
        decision: KycReviewDecision,
        reason: str,
        evidence: dict | None = None,
    ) -> KycAdminReview:
        review = KycAdminReview(
            kyc_document_id=kyc_document_id,
            admin_id=admin_id,
            decision=decision,
            reason=reason,
            evidence=evidence,
        )
        self.db.add(review)
        await self.db.flush()
        return review

    async def list_admin_reviews_for_document(
        self, kyc_document_id: uuid.UUID
    ) -> list[KycAdminReview]:
        """
        Full override history for a document — a document can be
        overridden more than once (e.g. admin approves, a later
        investigation reverses it). effective_status on the model
        always uses the MOST RECENT review.
        """
        result = await self.db.execute(
            select(KycAdminReview)
            .where(KycAdminReview.kyc_document_id == kyc_document_id)
            .order_by(KycAdminReview.created_at.desc())
        )
        return list(result.scalars().all())
