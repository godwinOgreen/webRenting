"""
domains/kyc/service.py

Business logic for KYC submission, provider webhook handling, and
admin override.

Rule 9 (identity overwrite): when a KYC document is approved — either
by the provider directly or by admin override — User.first_name,
last_name, and dob are overwritten with the verified values from the
provider's response. This service is the ONLY place that write happens.

Rule 8 (admin override is final): KycAdminReview always takes
precedence over the provider's own status. This service treats an
existing admin review on a document as locking out any later provider
webhook from changing User.kyc_status.

Cross-domain write: this service updates User.kyc_status and
first_name/last_name/dob directly via the User model — not through
UserRepository. Same justified exception pattern as payments/service.py
creating a Subscription directly: a KYC decision MUST atomically
update both KycDocument.status and User.kyc_status (and identity
fields) in one transaction, or a user could end up in the inconsistent
state of "document approved but kyc_status still not_submitted."
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import ALLOWED_KYC_MIME_TYPES, MAX_KYC_DOCUMENT_SIZE_BYTES
from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.domains.admin.models import AdminAuditLog
from app.domains.kyc.models import KycDocumentStatus, KycReviewDecision
from app.domains.kyc.repository import KycRepository
from app.domains.kyc.schemas import (
    KycAdminDecisionRequest,
    KycDocumentRead,
    KycQueueItemRead,
    KycSubmitRequest,
)
from app.domains.users.models import KycStatus, User
from app.shared.schemas import PaginatedResponse

logger = logging.getLogger(__name__)

# Maps KycDocumentStatus (per-document provider/admin outcome) to the
# User-level KycStatus that should result. Kept local to this service
# since it's the only place this translation happens.
_DOC_STATUS_TO_USER_STATUS = {
    KycDocumentStatus.PENDING: KycStatus.PENDING,
    KycDocumentStatus.APPROVED: KycStatus.VERIFIED,
    KycDocumentStatus.REJECTED: KycStatus.REJECTED,
}


class KycService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = KycRepository(db)

    # ── Submission ────────────────────────────────────────────────────────────

    async def submit(
        self,
        user: User,
        data: KycSubmitRequest,
        file_bytes: bytes,
        mime_type: str,
    ) -> KycDocumentRead:
        """
        Raises:
            ValidationException: disallowed file type or oversized file
            ConflictException: user already has a pending submission
        """
        if mime_type not in ALLOWED_KYC_MIME_TYPES:
            raise ValidationException(
                message="Unsupported file type for KYC document",
                errors={"file": [f"Must be one of: {sorted(ALLOWED_KYC_MIME_TYPES)}"]},
            )
        if len(file_bytes) > MAX_KYC_DOCUMENT_SIZE_BYTES:
            raise ValidationException(
                message="File too large",
                errors={
                    "file": [f"Maximum size is {MAX_KYC_DOCUMENT_SIZE_BYTES // (1024 * 1024)}MB"]
                },
            )

        existing = await self.repo.get_latest_for_user(user.id)
        if existing is not None and existing.status == KycDocumentStatus.PENDING:
            raise ConflictException(
                message="You already have a KYC submission pending review",
                error_code="kyc_already_pending",
            )

        # Stored privately — never through the public property-image
        # storage path. KYC documents use a separate, non-public
        # bucket/prefix (integrations/storage/manager.py.save_private).
        from app.integrations.storage.manager import get_storage

        storage = get_storage()
        document_url = await storage.save_private(
            file_bytes, f"kyc/{user.id}/{uuid.uuid4().hex}", mime_type
        )

        from app.core.config import settings

        provider = settings.KYC_PROVIDER

        # Submit to the provider — returns a provider_reference used to
        # match the async webhook callback.
        from app.integrations.kyc import submit_document

        provider_reference = await submit_document(
            user=user,
            document_type=data.document_type,
            document_url=document_url,
            provider=provider,
        )

        doc = await self.repo.create(
            user_id=user.id,
            document_type=data.document_type,
            document_url=document_url,
            provider=provider,
            provider_reference=provider_reference,
        )

        user.kyc_status = KycStatus.PENDING
        await self.db.flush()

        logger.info(
            "KYC document submitted",
            extra={"kyc_document_id": str(doc.id), "user_id": str(user.id)},
        )
        return KycDocumentRead.model_validate(doc)

    async def get_my_status(self, user: User) -> KycDocumentRead | None:
        doc = await self.repo.get_latest_for_user(user.id)
        return KycDocumentRead.model_validate(doc) if doc else None

    async def list_my_history(self, user: User) -> list[KycDocumentRead]:
        docs = await self.repo.list_for_user(user.id)
        return [KycDocumentRead.model_validate(d) for d in docs]

    # ── Provider webhook ──────────────────────────────────────────────────────

    async def handle_provider_webhook(
        self,
        provider_reference: str,
        approved: bool,
        provider_response: dict,
        verified_first_name: str | None = None,
        verified_last_name: str | None = None,
        verified_dob: str | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        """
        Called by the KYC provider webhook router after signature
        verification.

        If this document already has an admin override on record, the
        admin's decision is final (Rule 8) — the provider-side status
        column is still updated for audit purposes, but User.kyc_status
        is deliberately left untouched so a later-arriving provider
        webhook can never silently reverse an admin's decision.
        """
        doc = await self.repo.get_by_provider_reference(provider_reference)
        if doc is None:
            logger.warning(
                "KYC webhook reference does not match any document",
                extra={"provider_reference": provider_reference},
            )
            return

        new_status = KycDocumentStatus.APPROVED if approved else KycDocumentStatus.REJECTED
        await self.repo.update_provider_result(doc, new_status, provider_response, rejection_reason)

        existing_reviews = await self.repo.list_admin_reviews_for_document(doc.id)
        if existing_reviews:
            logger.info(
                "Provider webhook received for a document with an existing "
                "admin override — provider status updated, but User.kyc_status "
                "left untouched (admin decision remains final)",
                extra={"kyc_document_id": str(doc.id)},
            )
            return

        await self._apply_status_to_user(
            doc.user_id,
            new_status,
            verified_first_name,
            verified_last_name,
            verified_dob,
        )

    # ── Admin override ────────────────────────────────────────────────────────

    async def admin_override(
        self,
        document_id: uuid.UUID,
        admin: User,
        data: KycAdminDecisionRequest,
    ) -> KycDocumentRead:
        """
        Rule 8: admin decision is always final, regardless of what the
        provider previously returned. Writes both a KycAdminReview row
        AND an AdminAuditLog row (Standard 23 — every admin action that
        modifies data is logged).
        """
        doc = await self.repo.get_by_id(document_id)
        if doc is None:
            raise NotFoundException(message="KYC document not found")

        decision = KycReviewDecision(data.decision)
        await self.repo.create_admin_review(
            kyc_document_id=doc.id,
            admin_id=admin.id,
            decision=decision,
            reason=data.reason,
        )

        doc_status = (
            KycDocumentStatus.APPROVED
            if decision == KycReviewDecision.APPROVED
            else KycDocumentStatus.REJECTED
        )

        doc.status = doc_status
        if doc_status == KycDocumentStatus.REJECTED:
            doc.rejection_reason = data.reason
        await self.db.flush()

        # Admin override always wins — apply to the user regardless of
        # what the provider previously said. verified_* identity fields
        # are not re-supplied here — those only ever come from the
        # original provider response, never from an admin decision.
        await self._apply_status_to_user(
            doc.user_id,
            doc_status,
            verified_first_name=None,
            verified_last_name=None,
            verified_dob=None,
            rejection_reason=(data.reason if doc_status == KycDocumentStatus.REJECTED else None),
        )

        # Standard 23: log the admin action.
        self.db.add(
            AdminAuditLog(
                admin_id=admin.id,
                action="kyc_override",
                target_type="kyc_document",
                target_id=doc.id,
                reason=data.reason,
            )
        )
        await self.db.flush()

        logger.info(
            "KYC admin override applied",
            extra={
                "kyc_document_id": str(doc.id),
                "admin_id": str(admin.id),
                "decision": decision.value,
            },
        )

        # Refresh resolves expired attributes (updated_at) before
        # Pydantic accesses them synchronously — avoids MissingGreenlet.
        await self.db.refresh(doc)
        return KycDocumentRead.model_validate(doc)

    # ── Admin queue ───────────────────────────────────────────────────────────

    async def list_pending_queue(
        self, page: int, per_page: int
    ) -> PaginatedResponse[KycQueueItemRead]:
        docs, total = await self.repo.list_pending_queue(page, per_page)
        items = [
            KycQueueItemRead(
                id=d.id,
                user_id=d.user_id,
                user_full_name=d.user.full_name,
                user_email=d.user.email,
                document_type=d.document_type.value,
                status=d.status.value,
                provider=d.provider,
                created_at=d.created_at,
            )
            for d in docs
        ]
        return PaginatedResponse.paginate(items, total, page, per_page)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _apply_status_to_user(
        self,
        user_id: uuid.UUID,
        doc_status: KycDocumentStatus,
        verified_first_name: str | None,
        verified_last_name: str | None,
        verified_dob: str | None,
        rejection_reason: str | None = None,
    ) -> None:
        """
        The single place User.kyc_status and identity fields are
        mutated as a result of a KYC decision (Rule 9). Called from
        both the provider webhook path and the admin override path so
        the two can never drift into different update logic.
        """
        user = await self.db.get(User, user_id)
        if user is None:
            logger.error(
                "KYC status update target user not found",
                extra={"user_id": str(user_id)},
            )
            return

        user.kyc_status = _DOC_STATUS_TO_USER_STATUS[doc_status]

        if doc_status == KycDocumentStatus.APPROVED:
            # Rule 9: overwrite with verified identity data when available.
            if verified_first_name:
                user.first_name = verified_first_name
            if verified_last_name:
                user.last_name = verified_last_name
            if verified_dob:
                user.dob = date.fromisoformat(verified_dob)

        await self.db.flush()
