# app/domains/admin/models.py

"""
Domain: Admin
Tables: admin_audit_log
No enums (action is String — validated in admin_service)

One model: AdminAuditLog — immutable record of every privileged admin action.

Why this exists:
  Admins have powerful capabilities: suspending users, approving listings,
  overriding KYC decisions, resolving reports. Every action MUST be logged
  for:
    1. Accountability: "who suspended this user and why?"
    2. Compliance: NDPR requires a record of data access and modifications
    3. Dispute resolution: "I was unfairly suspended" — the log proves the reason
    4. Security: detecting admin account misuse or compromise
    5. Analytics: admin workload and response time tracking

Why polymorphic (target_type + target_id):
  Same pattern as Report and Notification. Admin actions target different
  entity types (users, properties, reports, kyc_documents, subscriptions).
  One table with polymorphic reference is cleaner than one log table per domain.

What gets logged (coordinated by admin_service):
  Every admin action that changes entity state creates a log entry.
  Read-only actions (viewing a report, listing users) are NOT logged —
  only mutations.

  Common actions:
    approve_property    — admin approves a pending listing
    reject_property     — admin rejects a listing (reason required)
    suspend_property    — admin pulls a listing offline
    suspend_user        — admin suspends a user account
    unsuspend_user      — admin re-enables a suspended account
    override_kyc        — admin overrides provider KYC decision
    resolve_report      — admin resolves a content report
    dismiss_report      — admin dismisses a content report
    grant_subscription  — admin grants a free subscription
    change_role         — admin changes a user's role
    verify_agent        — admin grants verified badge to agent
    approve_media       — admin approves a media asset for display
    reject_media        — admin rejects a media asset

  action values (validated in admin_service):
    Stored as String for flexibility. Adding new actions requires no migration.
    Validated in admin_service against a Python set:
      VALID_ADMIN_ACTIONS = {"approve_property", "reject_property", ...}

Why AdminAuditLog uses CreatedAtMixin (not TimestampMixin):
  Log entries are immutable. Once written, never updated.
  created_at is when the admin performed the action.

ip_address stored (same reasoning as consent log):
  Admin actions are high-privilege operations. IP address is security
  evidence: "this admin action was performed from this IP."
  Critical for detecting compromised admin accounts.
  Stored ONLY here — not in analytics (privacy-first for regular users,
  security-first for admin actions).

Action coordination pattern:
  admin_service always writes TWO things in one transaction:
    1. The entity mutation (e.g. property.approval_status = SUSPENDED)
    2. The AdminAuditLog entry

  Example (admin_service.suspend_property):
    async def suspend_property(self, db, admin_id, property_id, reason):
        prop = await self.property_repo.get(db, property_id)
        prop.approval_status = ApprovalStatus.SUSPENDED
        log = AdminAuditLog(
            admin_id=admin_id,
            action="suspend_property",
            target_type="property",
            target_id=property_id,
            details=reason,
        )
        db.add(log)
        await db.commit()  # both writes in one transaction
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import CreatedAtMixin, UUIDMixin

if TYPE_CHECKING:
    from app.domains.users.models import User


# ─── Model ───────────────────────────────────────────────────────────────────


class AdminAuditLog(Base, UUIDMixin, CreatedAtMixin):
    """
    An immutable record of a single privileged admin action.

    Table: admin_audit_log

    Uses CreatedAtMixin (not TimestampMixin):
      Log entries are immutable. Once written, never updated.
      created_at is when the admin performed the action.

    No ORM relationships to target entities (polymorphic — no FK).
    The admin_service resolves the target by branching on target_type.
    """

    __tablename__ = "admin_audit_log"

    # ── Who performed the action ──────────────────────────────────────────────
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "The admin who performed this action. "
            "RESTRICT: audit logs are permanent — cannot delete admin with actions. "
            "Indexed: 'get all actions by admin X' queries."
        ),
    )

    # ── What action ───────────────────────────────────────────────────────────
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=(
            "What the admin did. Valid values (validated in admin_service): "
            "approve_property | reject_property | suspend_property | "
            "suspend_user | unsuspend_user | override_kyc | "
            "resolve_report | dismiss_report | grant_subscription | "
            "change_role | verify_agent | approve_media | reject_media. "
            "Stored as String for flexibility — adding new actions requires no migration."
        ),
    )

    # ── What entity was affected (polymorphic) ────────────────────────────────
    target_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "Type of entity affected. Valid values: "
            "user | property | report | kyc_document | subscription | media_asset. "
            "Drives polymorphic lookup. Validated in admin_service."
        ),
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment=(
            "UUID of the entity affected. No DB-level FK — target table varies. "
            "Service resolves: if target_type='user' → user_service.get_by_id(id)."
        ),
    )

    # ── Details ───────────────────────────────────────────────────────────────
    details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Free-text description of the action and reasoning. "
            "Required for destructive actions (suspend, reject, override). "
            "Enforced in admin_service — not a DB constraint. "
            "e.g. 'Confirmed fake listing — stock photos from Lagos, property in Abuja.'"
        ),
    )

    # ── Previous and new values (optional, for state changes) ─────────────────
    previous_value: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=(
            "The value BEFORE the action. For tracking state transitions. "
            "e.g. action='change_role', previous_value='renter', new_value='agent'. "
            "NULL when not applicable (e.g. resolve_report)."
        ),
    )
    new_value: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=(
            "The value AFTER the action. For tracking state transitions. "
            "e.g. action='suspend_user', previous_value='active', new_value='suspended'. "
            "NULL when not applicable."
        ),
    )

    # ── Security ──────────────────────────────────────────────────────────────
    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
        comment=(
            "Admin's IP address at the time of action. "
            "Security evidence for detecting compromised admin accounts. "
            "IPv6 max length = 45 chars. "
            "Stored ONLY here — not in analytics."
        ),
    )

    # ── Table constraints ─────────────────────────────────────────────────────
    __table_args__ = (
        # Composite index for admin dashboard: "show all actions on entity type X"
        Index(
            "idx_audit_target_type_created",
            "target_type",
            "created_at",
        ),
        # Composite index: "show all actions by admin X on entity type Y"
        Index(
            "idx_audit_admin_action",
            "admin_id",
            "action",
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    admin: Mapped[User] = relationship(
        "User",
        foreign_keys=[admin_id],
        uselist=False,
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def has_state_change(self) -> bool:
        """True if this action involved a state transition (previous → new)."""
        return self.previous_value is not None and self.new_value is not None

    @has_state_change.expression
    def has_state_change(cls):
        """
        SQL: WHERE AdminAuditLog.has_state_change

        Used in admin analytics:
            "Show me all role changes":
            SELECT * FROM admin_audit_log
            WHERE action = 'change_role' AND has_state_change
        """
        return sa.and_(
            cls.previous_value.isnot(None),
            cls.new_value.isnot(None),
        )

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def action_label(self) -> str:
        """
        Human-readable label for the action.
        Used in admin audit timeline views.
        """
        return self.action.replace("_", " ").title()

    @property
    def state_change_display(self) -> str | None:
        """
        Human-readable state transition.
        e.g. "renter → agent" or "pending → suspended"
        Returns None if no state change.
        """
        if not self.has_state_change:
            return None
        return f"{self.previous_value} → {self.new_value}"

    @property
    def target_display(self) -> str:
        """
        Human-readable description of the target entity.
        Used in admin audit timeline views.
        """
        return f"{self.target_type.title()}: {self.target_id}"

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<AdminAuditLog id={self.id} "
            f"admin_id={self.admin_id} "
            f"action={self.action!r} "
            f"target={self.target_type}:{self.target_id}>"
        )
