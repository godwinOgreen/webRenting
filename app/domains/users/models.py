# app/domains/users/models.py

"""
Domain: Users
Tables: users
Enums: UserRole, AdminRole, OAuthProvider, KycStatus

Design decisions:
  - Agent is a role, NOT a separate table.
    Agent-specific fields (agency_name etc.) are nullable columns here.
  - password_hash is nullable for OAuth-only users.
  - first_name, last_name, dob are overwritten on KYC approval.
  - All relationships use TYPE_CHECKING guards to prevent circular imports.
    SQLAlchemy resolves string class names at mapper initialization time.
  - UserRole has 3 values: renter, agent, admin.
    MODERATOR is an AdminRole sub-role (admin_role column), not a UserRole.
  - KYC status uses pending_review (not pending) to match ERD v9.2.
  - hybrid_property used for any computed property that appears in queries.
    @property used for display-only computed properties.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin

# TYPE_CHECKING guard — imports exist only at type-check time (mypy/pyright).
# At runtime, never imported. Prevents circular imports across 16 domains.
# SQLAlchemy resolves "ClassName" strings at mapper init time via base_all.py.
if TYPE_CHECKING:
    from app.domains.analytics.models import PropertyAnalytics
    from app.domains.bookings.models import AgentAvailability, Booking
    from app.domains.consent.models import UserConsentLog
    from app.domains.kyc.models import KycDocument
    from app.domains.media.models import MediaAsset
    from app.domains.messaging.models import ConversationParticipant, Message
    from app.domains.notifications.models import Notification, UserNotificationSettings
    from app.domains.payments.models import Payment
    from app.domains.properties.models import Property
    from app.domains.reports.models import Report
    from app.domains.reviews.models import AgentReview, Review
    from app.domains.search.models import Favorite, SavedSearch, SearchHistory
    from app.domains.subscriptions.models import Subscription


# ─── Enums ───────────────────────────────────────────────────────────────────


class UserRole(str, enum.Enum):
    """
    Top-level user role. Every user has exactly one.

    RENTER — searches, books, reviews (requires subscription)
    AGENT  — lists properties, receives leads, manages calendar (requires subscription)
    ADMIN  — full platform management (admin_role sub-divides access)
    """

    RENTER = "renter"
    AGENT = "agent"
    ADMIN = "admin"


class AdminRole(str, enum.Enum):
    """
    Admin sub-role. Only set when role=admin. Stored in users.admin_role.

    SUPER_ADMIN — full access, can manage other admins
    ADMIN       — standard admin operations (approve listings, suspend users)
    MODERATOR   — content moderation (review flags, resolve reports)
    """

    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MODERATOR = "moderator"


class OAuthProvider(str, enum.Enum):
    """Supported OAuth identity providers."""

    GOOGLE = "google"
    APPLE = "apple"
    FACEBOOK = "facebook"


class KycStatus(str, enum.Enum):
    """
    KYC verification status. Updated by KYC provider webhook or admin override.

    NOT_SUBMITTED  → user hasn't started KYC
    PENDING_REVIEW → documents submitted, waiting for provider response
    VERIFIED       → provider confirmed identity (or admin override)
    REJECTED       → provider rejected (or admin override)

    When VERIFIED: first_name, last_name, dob are overwritten by KYC data.
    When admin overrides: override is final (Rule 8).
    """

    NOT_SUBMITTED = "not_submitted"
    PENDING_REVIEW = "pending_review"
    VERIFIED = "verified"
    REJECTED = "rejected"


# ─── SQLAlchemy Enum column types ────────────────────────────────────────────
# values_callable ensures stored values are lowercase strings ("renter" not "RENTER").
# Let Alembic manage CREATE TYPE (no create_type=False).

_user_role_col = sa.Enum(
    UserRole,
    name="user_role",
    values_callable=lambda obj: [e.value for e in obj],
)

_admin_role_col = sa.Enum(
    AdminRole,
    name="admin_role",
    values_callable=lambda obj: [e.value for e in obj],
)

_oauth_provider_col = sa.Enum(
    OAuthProvider,
    name="oauth_provider",
    values_callable=lambda obj: [e.value for e in obj],
)

_kyc_status_col = sa.Enum(
    KycStatus,
    name="kyc_status",
    values_callable=lambda obj: [e.value for e in obj],
)


# ─── Model ───────────────────────────────────────────────────────────────────


class User(Base, UUIDMixin, TimestampMixin):
    """
    All platform users: renters, agents, admins, moderators.

    Table: users
    PK:    id (UUID, server-generated)
    """

    __tablename__ = "users"

    # ── Core identity ─────────────────────────────────────────────────────────
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Stored lowercase. Unique across all user types.",
    )
    first_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Overwritten by KYC provider on approval.",
    )
    last_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Overwritten by KYC provider on approval.",
    )
    dob: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Overwritten by KYC provider on approval.",
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    # ── Authentication ────────────────────────────────────────────────────────
    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="NULL for OAuth-only users. Never store plaintext password.",
    )
    oauth_provider: Mapped[OAuthProvider | None] = mapped_column(
        _oauth_provider_col,
        nullable=True,
    )
    oauth_provider_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="The user's ID on the OAuth provider. Matches returning users.",
    )

    # ── Role & permissions ────────────────────────────────────────────────────
    role: Mapped[UserRole] = mapped_column(
        _user_role_col,
        nullable=False,
        server_default=text("'renter'"),
        default=UserRole.RENTER,
    )
    admin_role: Mapped[AdminRole | None] = mapped_column(
        _admin_role_col,
        nullable=True,
        comment="NULL for non-admin users. Subdivides admin access level.",
    )

    # ── KYC & verification ────────────────────────────────────────────────────
    kyc_status: Mapped[KycStatus] = mapped_column(
        _kyc_status_col,
        nullable=False,
        server_default=text("'not_submitted'"),
        default=KycStatus.NOT_SUBMITTED,
    )
    verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
        comment="Admin-granted verified badge. Separate from KYC.",
    )

    # ── Agent profile fields ──────────────────────────────────────────────────
    # Nullable for all users. Only populated when role=agent.
    agency_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Suspension ────────────────────────────────────────────────────────────
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    suspension_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Required when suspending. Shown to the suspended user.",
    )
    suspended_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    # All use string class names to prevent circular imports.
    # Resolved by SQLAlchemy at mapper init time via base_all.py.

    # Self-referencing: who suspended this user
    suspending_admin: Mapped[User | None] = relationship(
        "User",
        remote_side="User.id",
        foreign_keys=[suspended_by],
        uselist=False,
    )

    # Properties
    properties: Mapped[list[Property]] = relationship(
        "Property",
        back_populates="owner",
        foreign_keys="Property.owner_id",
    )
    rented_properties: Mapped[list[Property]] = relationship(
        "Property",
        back_populates="rented_by_user",
        foreign_keys="Property.rented_by_user_id",
    )

    # Bookings
    bookings: Mapped[list[Booking]] = relationship(
        "Booking",
        back_populates="user",
        foreign_keys="Booking.user_id",
    )

    # Agent availability
    availability_slots: Mapped[list[AgentAvailability]] = relationship(
        "AgentAvailability",
        back_populates="agent",
    )

    # Payments
    payments: Mapped[list[Payment]] = relationship(
        "Payment",
        back_populates="user",
    )

    # Subscriptions
    subscriptions: Mapped[list[Subscription]] = relationship(
        "Subscription",
        back_populates="user",
    )

    # Notifications
    notifications: Mapped[list[Notification]] = relationship(
        "Notification",
        back_populates="user",
    )
    notification_settings: Mapped[UserNotificationSettings | None] = relationship(
        "UserNotificationSettings",
        back_populates="user",
        uselist=False,
    )

    # Media
    media_assets: Mapped[list[MediaAsset]] = relationship(
        "MediaAsset",
        back_populates="uploaded_by_user",
    )

    # Reviews
    reviews_written: Mapped[list[Review]] = relationship(
        "Review",
        back_populates="user",
        foreign_keys="Review.user_id",
    )
    agent_reviews_received: Mapped[list[AgentReview]] = relationship(
        "AgentReview",
        back_populates="agent",
        foreign_keys="AgentReview.agent_id",
    )
    agent_reviews_written: Mapped[list[AgentReview]] = relationship(
        "AgentReview",
        back_populates="reviewer",
        foreign_keys="AgentReview.reviewer_id",
    )

    # KYC
    kyc_documents: Mapped[list[KycDocument]] = relationship(
        "KycDocument",
        back_populates="user",
    )

    # Search
    favorites: Mapped[list[Favorite]] = relationship(
        "Favorite",
        back_populates="user",
    )
    saved_searches: Mapped[list[SavedSearch]] = relationship(
        "SavedSearch",
        back_populates="user",
    )
    search_history: Mapped[list[SearchHistory]] = relationship(
        "SearchHistory",
        back_populates="user",
    )

    # Analytics
    analytics_events: Mapped[list[PropertyAnalytics]] = relationship(
        "PropertyAnalytics",
        back_populates="user",
    )

    # Reports
    reports_filed: Mapped[list[Report]] = relationship(
        "Report",
        back_populates="reporter",
        foreign_keys="Report.reporter_id",
    )

    # Consent
    consent_log: Mapped[list[UserConsentLog]] = relationship(
        "UserConsentLog",
        back_populates="user",
    )

    # Messaging
    conversation_participants: Mapped[list[ConversationParticipant]] = relationship(
        "ConversationParticipant",
        back_populates="user",
    )
    messages_sent: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="sender",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_agent(self) -> bool:
        return self.role == UserRole.AGENT

    @is_agent.expression
    def is_agent(cls):
        return cls.role == UserRole.AGENT

    @hybrid_property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @is_admin.expression
    def is_admin(cls):
        return cls.role == UserRole.ADMIN

    @hybrid_property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None

    @is_suspended.expression
    def is_suspended(cls):
        return cls.suspended_at.isnot(None)

    @hybrid_property
    def is_kyc_verified(self) -> bool:
        return self.kyc_status == KycStatus.VERIFIED

    @is_kyc_verified.expression
    def is_kyc_verified(cls):
        return cls.kyc_status == KycStatus.VERIFIED

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def full_name(self) -> str:
        """Display name. Used in emails, notifications, profiles."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_super_admin(self) -> bool:
        return self.role == UserRole.ADMIN and self.admin_role == AdminRole.SUPER_ADMIN

    @property
    def can_access_gated_features(self) -> bool:
        """
        Checks KYC half of the gate only.
        Full gate check: permissions/guards.py require_subscription()
        Rule: kyc_status=verified AND subscription active.
        """
        return self.is_kyc_verified and not self.is_suspended

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role.value!r}>"
