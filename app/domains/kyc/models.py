# app/domains/kyc/models.py

"""
Domain: KYC (Know Your Customer)
Tables: kyc_documents, kyc_admin_reviews
Enums: KycDocumentType, KycDocumentStatus, KycReviewDecision

Two models that handle identity verification.

KycDocument      — a submitted identity document + third-party provider result
KycAdminReview   — an admin's override of a provider decision (final word)

KYC flow (Decision 3 / Rule 8, kyc_service.py + tasks):
  1. User uploads document → KycDocument row created, status=pending,
     document_url set, provider/provider_reference set if submitted
     synchronously to the third-party API.
  2. Provider responds (sync or via webhook) → provider_response (JSONB)
     stored in full for audit/fraud investigation. status updated to
     approved/rejected based on provider result.
  3. If status=approved:
       - User.first_name, last_name, dob OVERWRITTEN with provider-verified
         values (Decision 2 — these fields live on User, not here)
       - User.kyc_status = verified
  4. If status=rejected: User.kyc_status = rejected, rejection_reason shown.

Admin override (Rule 8):
  An admin can create a KycAdminReview row to override the provider's
  decision in either direction. KycAdminReview.decision is FINAL —
  it supersedes KycDocument.status for determining User.kyc_status.
  Every override is also written to ADMIN_AUDIT_LOG (admin domain).

  effective_status property encodes this precedence:
      latest KycAdminReview.decision  >  KycDocument.status

KycDocumentStatus uses its own PostgreSQL enum type (name="kyc_document_status")
to avoid conflicting with User.kyc_status. The document's status only ever takes
pending/approved/rejected — never not_submitted.

Why KycDocumentType and KycReviewDecision are PostgreSQL enums (not strings):
  Document types map to government-issued IDs — they rarely change.
  Admin override decisions are safety-critical — typos would be catastrophic.
  Both benefit from type safety and database-level constraints.

NDPR compliance:
  KycDocument.user_id uses ON DELETE CASCADE.
  If a user exercises their right to erasure, ALL their identity data
  (government IDs, photos, personal details) must be deleted.
  CASCADE ensures no orphaned sensitive data survives.

provider_reference is NOT UNIQUE:
  KYC providers may reuse references across resubmissions.
  Webhook handler finds the LATEST document with that reference:
    ORDER BY created_at DESC LIMIT 1
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import CreatedAtMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.domains.users.models import User


# ─── Enums ───────────────────────────────────────────────────────────────────


class KycDocumentType(enum.StrEnum):
    """
    Type of identity document submitted.
    Applies to all roles, including agents.
    Stored as PostgreSQL enum for type safety (government IDs are stable).
    """

    NATIONAL_ID = "national_id"
    PASSPORT = "passport"
    DRIVERS_LICENSE = "drivers_license"


class KycDocumentStatus(enum.StrEnum):
    """
    Status of THIS document submission, as determined by the third-party
    provider. NOT the user's overall kyc_status — see User.kyc_status
    and effective_status below for the value that drives access decisions.

    PENDING  → submitted, awaiting provider response
    APPROVED → provider verified the document
    REJECTED → provider rejected the document (rejection_reason set)

    Has its own PostgreSQL enum type ("kyc_document_status") to avoid
    conflicting with User.kyc_status. Never uses "not_submitted".
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KycReviewDecision(enum.StrEnum):
    """
    Admin's override decision on a KycDocument. Always final (Rule 8).
    Stored as PostgreSQL enum for type safety (safety-critical decision).
    """

    APPROVED = "approved"
    REJECTED = "rejected"


# ─── SQLAlchemy Enum column types ────────────────────────────────────────────

_kyc_document_type_col = sa.Enum(
    KycDocumentType,
    name="kyc_document_type",
)

# Own PostgreSQL enum type — does not conflict with User.kyc_status.
_kyc_document_status_col = sa.Enum(
    KycDocumentStatus,
    name="kyc_document_status",
)

_kyc_review_decision_col = sa.Enum(
    KycReviewDecision,
    name="kyc_review_decision",
)


# ─── KycDocument Model ───────────────────────────────────────────────────────


class KycDocument(Base, UUIDMixin, TimestampMixin):
    """
    A single identity document submission and its provider verification result.

    Table: kyc_documents

    Uses TimestampMixin:
      created_at: when the document was submitted
      updated_at: when the provider webhook or admin last changed the status

    A user may have multiple KycDocument rows over time (resubmissions
    after rejection). The most recent row (by created_at) combined with
    any KycAdminReview determines the user's current kyc_status.

    document_url is access-controlled — never publicly listable.
    Contains government ID numbers and photos — the most sensitive
    data on the platform.
    """

    __tablename__ = "kyc_documents"

    # ── Who submitted ─────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "The user who submitted this document. "
            "CASCADE: NDPR right to erasure — deleting user must delete ALL "
            "their identity data (government IDs, photos, personal details). "
            "Indexed: admin queries 'get all KYC docs for user X'."
        ),
    )

    # ── Document details ──────────────────────────────────────────────────────
    document_type: Mapped[KycDocumentType] = mapped_column(
        _kyc_document_type_col,
        nullable=False,
        comment="Type of identity document: national_id | passport | drivers_license.",
    )
    document_url: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment=(
            "Object storage URL. Access-controlled — never publicly listable. "
            "Contains sensitive identity information."
        ),
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[KycDocumentStatus] = mapped_column(
        _kyc_document_status_col,
        nullable=False,
        server_default=text("'pending'::kyc_document_status"),
        default=KycDocumentStatus.PENDING,
        index=True,
        comment=(
            "Provider verification status: pending | approved | rejected. "
            "Indexed: admin queries 'get all pending KYC documents'. "
            "NOT the user's overall kyc_status — use effective_status for that."
        ),
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Set by provider (or admin via KycAdminReview) when status=rejected.",
    )

    # ── Provider integration ──────────────────────────────────────────────────
    provider: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="e.g. 'smile_identity', 'youverify'. NULL if not yet submitted.",
    )
    provider_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment=(
            "Provider's reference/transaction ID for this verification. "
            "NOT UNIQUE — providers may reuse references across resubmissions. "
            "Webhook handler finds the LATEST document: ORDER BY created_at DESC LIMIT 1. "
            "Indexed: webhook lookup needs to be fast."
        ),
    )
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "Full raw JSON response from the provider. Stored for audit "
            "and fraud investigation — never displayed directly to the user."
        ),
    )

    # ── Constraints ───────────────────────────────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "length(document_url) > 0",
            name="chk_document_url",
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="kyc_documents",
    )
    admin_reviews: Mapped[list[KycAdminReview]] = relationship(
        "KycAdminReview",
        back_populates="kyc_document",
        cascade="all, delete-orphan",
        order_by="KycAdminReview.created_at",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_pending(self) -> bool:
        """True if waiting for provider response or admin review."""
        return self.status == KycDocumentStatus.PENDING

    @is_pending.expression
    def is_pending(cls):
        """
        SQL: WHERE KycDocument.is_pending

        Used in admin dashboard:
            stmt = select(KycDocument).where(KycDocument.is_pending)
        """
        return cls.status == KycDocumentStatus.PENDING

    @hybrid_property
    def is_approved(self) -> bool:
        """True if provider has verified this document."""
        return self.status == KycDocumentStatus.APPROVED

    @is_approved.expression
    def is_approved(cls):
        """SQL: WHERE KycDocument.is_approved"""
        return cls.status == KycDocumentStatus.APPROVED

    @hybrid_property
    def is_rejected(self) -> bool:
        """True if provider has rejected this document."""
        return self.status == KycDocumentStatus.REJECTED

    @is_rejected.expression
    def is_rejected(cls):
        """SQL: WHERE KycDocument.is_rejected"""
        return cls.status == KycDocumentStatus.REJECTED

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def latest_admin_review(self) -> KycAdminReview | None:
        """Most recent admin override, if any. Ordered ASC, so last is newest."""
        return self.admin_reviews[-1] if self.admin_reviews else None

    @property
    def effective_status(self) -> KycDocumentStatus:
        """
        THE status that drives User.kyc_status. Encodes Rule 8.

        Precedence:
          1. latest_admin_review.decision, if any review exists  (admin is final)
          2. self.status (provider result)

        Usage in kyc_service:
            if doc.effective_status == KycDocumentStatus.APPROVED:
                user.kyc_status = KycStatus.VERIFIED
                user.first_name = doc.provider_response["first_name"]
                ...
        """
        review = self.latest_admin_review
        if review is not None:
            return (
                KycDocumentStatus.APPROVED
                if review.decision == KycReviewDecision.APPROVED
                else KycDocumentStatus.REJECTED
            )
        return self.status

    @property
    def was_overridden(self) -> bool:
        """
        True if an admin's decision DIFFERS from the provider's result.
        Admin confirming the provider's decision (approve when already approved)
        is NOT an override — it's a confirmation.

        Used in admin audit views:
          - "Show me only actual overrides" (was_overridden=True)
          - Distinguishes "admin changed decision" from "admin confirmed decision"
        """
        review = self.latest_admin_review
        if review is None:
            return False
        provider_approved = self.status == KycDocumentStatus.APPROVED
        admin_approved = review.decision == KycReviewDecision.APPROVED
        return provider_approved != admin_approved

    @property
    def has_provider_response(self) -> bool:
        """True if the provider has responded (webhook received)."""
        return self.provider_response is not None

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<KycDocument id={self.id} "
            f"user_id={self.user_id} "
            f"type={self.document_type.value!r} "
            f"status={self.status.value!r}>"
        )


# ─── KycAdminReview Model ────────────────────────────────────────────────────


class KycAdminReview(Base, UUIDMixin, CreatedAtMixin):
    """
    An admin's override decision on a KycDocument. Always final (Rule 8).

    Table: kyc_admin_reviews

    Uses CreatedAtMixin (not TimestampMixin):
      Admin reviews are immutable. Once written, never edited.
      If a mistake is made, a different admin writes a NEW review.

    Every row here must also produce an ADMIN_AUDIT_LOG entry
    (admin_service.override_kyc() handles both writes in one transaction).

    reason is NOT NULL — required for ALL decisions (approve AND reject).
    Even when approving a provider's rejection, the admin must document why:
      "User provided additional evidence via video call, ref #1234"

    evidence is a free-form JSONB field for the admin to attach supporting
    notes/links.

    Why multiple reviews per document:
      Rare but possible: Admin A rejects → user disputes → Admin B approves.
      Each decision creates a new row. The latest decision wins.
      order_by="created_at" (ASC) + [-1] gives the newest review.
    """

    __tablename__ = "kyc_admin_reviews"

    # ── Which document ────────────────────────────────────────────────────────
    kyc_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kyc_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "The KYC document being reviewed. "
            "CASCADE: document deleted → its reviews are meaningless. "
            "Indexed: 'get all reviews for document X' queries."
        ),
    )

    # ── Which admin ───────────────────────────────────────────────────────────
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "The admin who made this decision. "
            "RESTRICT: cannot delete admin who made override decisions (audit trail). "
            "Indexed: 'get all decisions by admin X' queries."
        ),
    )

    # ── Decision ──────────────────────────────────────────────────────────────
    decision: Mapped[KycReviewDecision] = mapped_column(
        _kyc_review_decision_col,
        nullable=False,
        comment="Admin's decision: approved | rejected. Always final (Rule 8).",
    )
    reason: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment=(
            "Required for ALL decisions. Why the admin is overriding "
            "(or confirming) the provider result. "
            "Shown to the user in the notification. "
            "e.g. 'User provided additional evidence via video call, ref #1234'"
        ),
    )

    # ── Evidence ──────────────────────────────────────────────────────────────
    evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "Optional free-form supporting evidence. "
            "e.g. { 'suspicion': 'photo_mismatch', 'notes': 'Face does not match', "
            "'reference': '#1234' }. Stored for audit trail and dispute resolution."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    kyc_document: Mapped[KycDocument] = relationship(
        "KycDocument",
        back_populates="admin_reviews",
    )
    admin: Mapped[User] = relationship(
        "User",
        foreign_keys=[admin_id],
        back_populates="admin_reviews_conducted",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_approval(self) -> bool:
        """True if the admin approved the document."""
        return self.decision == KycReviewDecision.APPROVED

    @is_approval.expression
    def is_approval(cls):
        """
        SQL: WHERE KycAdminReview.is_approval

        Used in admin audit report:
            stmt = select(func.count()).where(
                KycAdminReview.admin_id == admin_id,
                KycAdminReview.is_approval,
            )
        """
        return cls.decision == KycReviewDecision.APPROVED

    @hybrid_property
    def is_rejection(self) -> bool:
        """True if the admin rejected the document."""
        return self.decision == KycReviewDecision.REJECTED

    @is_rejection.expression
    def is_rejection(cls):
        """SQL: WHERE KycAdminReview.is_rejection"""
        return cls.decision == KycReviewDecision.REJECTED

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<KycAdminReview id={self.id} "
            f"kyc_document_id={self.kyc_document_id} "
            f"admin_id={self.admin_id} "
            f"decision={self.decision.value!r}>"
        )
