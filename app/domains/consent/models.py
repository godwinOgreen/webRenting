# app/domains/consent/models.py

"""
Domain: Consent
Tables: user_consent_log
No enums (consent_type is String — validated in consent_service)

One model: an append-only log of every consent action a user takes.

UserConsentLog — immutable record: "user did X with consent Y at time Z"

NDPR (Nigeria Data Protection Regulation) compliance:
  The platform MUST be able to prove:
    1. What the user consented to (consent_type + consent_version)
    2. When they consented (created_at)
    3. How they consented (ip_address + user_agent)
    4. Whether consent was given or withdrawn (consented boolean)

  Without this log, a user could claim "I never agreed to your terms"
  and the platform would have no evidence.

Append-only design:
  Every consent action creates a NEW row. Existing rows are NEVER modified.
    - User agrees to terms at registration → INSERT (consented=True)
    - User withdraws marketing consent     → INSERT (consented=False)
    - User re-agrees after withdrawal      → INSERT (consented=True)
  Three rows for the same consent_type. The LATEST row is the current status.

  This is intentional:
    - Full audit trail of every consent change
    - Legal evidence: "user withdrew consent on 15th March, we stopped
      marketing emails on 15th March (not after)"
    - No "update" operation that could lose history

Current consent status (in consent_service):
  To check if user has given consent for a type:
    stmt = select(UserConsentLog).where(
        UserConsentLog.user_id == user_id,
        UserConsentLog.consent_type == "marketing_email",
    ).order_by(UserConsentLog.created_at.desc()).limit(1)
    latest = (await db.execute(stmt)).scalar_one_or_none()
    has_consent = latest is not None and latest.consented

Why IP address IS stored here (but NOT in analytics):
  Analytics: IP not needed. Privacy-first approach. No legal requirement.
  Consent:   IP IS needed. NDPR requires proving HOW consent was obtained.
             IP address is evidence that the consent action came from
             the user's device. Without it, consent records are legally weak.
             This is a specific legal exception to the general privacy rule.

  user_agent is stored for the same reason — device/browser fingerprint
  as additional evidence of the consent action.

consent_version:
  Tracks which version of the legal document the user consented to.
  Example: consent_type="terms_of_service", consent_version="2.1"
  When terms are updated to v3.0, existing users must re-consent.
  The log proves: "user agreed to v2.1 on 1st Jan, not v3.0"
  This is legally critical when terms change.

consent_type values (validated in consent_service):
  terms_of_service  — platform terms and conditions
  privacy_policy    — privacy policy document
  data_processing   — NDPR-specific data processing consent
  marketing_email   — opt-in for marketing emails (default: not consented)
  marketing_sms     — opt-in for marketing SMS (default: not consented)
  cookie_policy     — cookie usage consent

  Stored as String (not enum) for flexibility. Adding a new consent type
  (e.g. "ai_processing") requires no migration.

  Validated in consent_service against:
    VALID_CONSENT_TYPES = {
        "terms_of_service", "privacy_policy", "data_processing",
        "marketing_email", "marketing_sms", "cookie_policy",
    }

Registration flow (auth_service.register()):
  1. User registers → POST /auth/register
  2. auth_service creates User row
  3. auth_service creates UserConsentLog rows for all required consents:
     - terms_of_service: consented=True (required — can't register without)
     - privacy_policy:   consented=True (required)
     - data_processing:  consented=True (required)
     - marketing_email:  consented=False (opt-in, user didn't check the box)
     - marketing_sms:    consented=False (opt-in)
  4. All in one transaction

Settings flow (PATCH /consent/settings):
  1. User toggles marketing_email from False to True
  2. consent_service creates new UserConsentLog row:
     consent_type="marketing_email", consented=True
  3. Previous rows untouched — full history preserved

Right to erasure (NDPR):
  user_id uses ON DELETE CASCADE.
  Deleting the user deletes all consent logs.
  The consent logs contain IP addresses (personal data) that must
  also be erased under right to erasure.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import (
    Boolean, ForeignKey, String, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from app.db.base import Base
from app.db.mixins import UUIDMixin, CreatedAtMixin

if TYPE_CHECKING:
    from app.domains.users.models import User


# ─── Model ───────────────────────────────────────────────────────────────────

class UserConsentLog(Base, UUIDMixin, CreatedAtMixin):
    """
    An immutable record of a single consent action by a user.

    Table: user_consent_log

    Append-only: every consent action creates a new row.
    Existing rows are NEVER modified or deleted (except via user deletion).

    Uses CreatedAtMixin (not TimestampMixin):
      Consent records are immutable. No updated_at needed.
      created_at IS the timestamp of the consent action.

    NDPR compliance:
      Every row is legal evidence that consent was given or withdrawn.
      Must contain: who, what, when, how, and which version of the document.
    """

    __tablename__ = "user_consent_log"

    # ── Who ───────────────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "The user who performed this consent action. "
            "CASCADE: right to erasure — consent logs contain IP addresses "
            "(personal data) that must be deleted with the user. "
            "Indexed: 'get consent history for user X' queries."
        ),
    )

    # ── What ──────────────────────────────────────────────────────────────────
    consent_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment=(
            "What the user consented to. Valid values (validated in consent_service): "
            "terms_of_service | privacy_policy | data_processing | "
            "marketing_email | marketing_sms | cookie_policy. "
            "Stored as String for flexibility — adding new types requires no migration."
        ),
    )
    consented: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        comment=(
            "True = user GAVE consent. False = user WITHDREW consent. "
            "The latest row per (user_id, consent_type) determines current status."
        ),
    )

    # ── Which version of the legal document ───────────────────────────────────
    consent_version: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment=(
            "Version of the legal document the user consented to. "
            "e.g. '2.1', '3.0'. Required for terms_of_service and privacy_policy. "
            "When terms change, users must re-consent. This field proves which "
            "version they actually agreed to. Legally critical."
        ),
    )

    # ── How (evidence for NDPR compliance) ────────────────────────────────────
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True,
        comment=(
            "IP address at the time of consent. IPv6 max length = 45 chars. "
            "Stored as NDPR legal evidence — proves the consent action came "
            "from the user's device. NOT stored in analytics (privacy-first), "
            "but IS stored here (legal requirement)."
        ),
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True,
        comment=(
            "Browser/device user agent at the time of consent. "
            "Additional evidence for NDPR compliance. "
            "e.g. 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0...)'"
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="consent_log",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_granted(self) -> bool:
        """True if this record represents consent being GIVEN."""
        return self.consented is True

    @is_granted.expression
    def is_granted(cls):
        """
        SQL: WHERE UserConsentLog.is_granted

        Used to find all currently granted consents for a user
        (when combined with latest-per-type subquery):
            latest = (
                select(UserConsentLog)
                .where(UserConsentLog.user_id == user_id)
                .order_by(UserConsentLog.created_at.desc())
                .limit(1)
                .subquery()
            )
        """
        return cls.consented == True  # noqa: E712

    @hybrid_property
    def is_withdrawn(self) -> bool:
        """True if this record represents consent being WITHDRAWN."""
        return self.consented is False

    @is_withdrawn.expression
    def is_withdrawn(cls):
        """SQL: WHERE UserConsentLog.is_withdrawn"""
        return cls.consented == False  # noqa: E712

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def action_label(self) -> str:
        """
        Human-readable label for the consent action.
        Used in admin audit views and user-facing consent history.
        """
        action = "Gave" if self.consented else "Withdrew"
        doc = self.consent_type.replace("_", " ").title()
        version = f" (v{self.consent_version})" if self.consent_version else ""
        return f"{action} consent for {doc}{version}"

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        action = "granted" if self.consented else "withdrawn"
        return (
            f"<UserConsentLog id={self.id} "
            f"user_id={self.user_id} "
            f"type={self.consent_type!r} "
            f"action={action}>"
        )
