"""
domains/kyc/schemas.py

Request/response schemas for KYC document submission and admin review.

Submission is a two-step flow, same pattern as media upload:
  1. Renter/agent uploads an identity document (image/PDF) — the file
     itself goes through the same storage backend as property images,
     but with a document_type classification and NO public URL exposed
     (KYC documents are never publicly servable, unlike property media).
  2. The document is sent to the configured KYC provider (Smile Identity
     or Youverify) asynchronously. The provider's webhook callback
     updates status to approved/rejected and overwrites User's
     first_name/last_name/dob on approval (Rule 9 — see kyc/service.py).

Admin override (KycAdminReview) is a separate, always-final decision
that supersedes whatever the provider returned — see
KycDocument.effective_status (Phase 1 models.py) for the precedence
logic this schema layer surfaces to the admin UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Submission ────────────────────────────────────────────────────────────────


class KycSubmitRequest(BaseModel):
    """
    Metadata accompanying a KYC document upload. The actual file bytes
    are a separate multipart field in the router — same split as
    media/schemas.py UploadResponse vs AttachImageRequest.
    """

    document_type: str = Field(..., description="national_id|passport|drivers_license")

    @field_validator("document_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"national_id", "passport", "drivers_license"}
        if v not in allowed:
            raise ValueError(f"document_type must be one of: {allowed}")
        return v


class KycDocumentRead(BaseModel):
    """
    Self-view for the submitting user. Deliberately excludes
    provider_response (raw provider JSON — internal/audit only, never
    shown to the user) and document_url (the raw file location — never
    exposed, unlike property images which are meant to be public).
    """

    id: uuid.UUID
    document_type: str
    status: str
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Admin review ──────────────────────────────────────────────────────────────


class KycAdminDecisionRequest(BaseModel):
    """
    Admin override — always final (Rule 8). Every submission also
    writes an AdminAuditLog row (handled in the service, not exposed
    here as a client-supplied field).
    """

    decision: str = Field(..., description="approved|rejected")
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, v: str) -> str:
        allowed = {"approved", "rejected"}
        if v not in allowed:
            raise ValueError(f"decision must be one of: {allowed}")
        return v


class KycAdminReviewRead(BaseModel):
    """Read representation of an admin KYC override decision."""

    id: uuid.UUID
    kyc_document_id: uuid.UUID
    admin_id: uuid.UUID
    decision: str
    reason: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KycQueueItemRead(BaseModel):
    """
    Admin moderation queue item — includes the submitting user's basic
    info (admins need to know WHO they're reviewing), unlike
    KycDocumentRead which is scoped to the self-view.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    user_full_name: str
    user_email: str
    document_type: str
    status: str
    provider: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
