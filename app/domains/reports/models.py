# app/domains/reports/models.py

"""
Domain: Reports
Tables: reports
Enums: ReportTargetType, ReportReason, ReportStatus

One model: Report — a user-filed report against a property, user,
message, or review.

Table: reports

Polymorphic target (target_type + target_id), same pattern as
Notification.related_type/related_id — no DB-level FK on target_id
since it can point to four different tables. Repository methods
resolve the target by branching on target_type.

Moderation flow (admin_service.py / reports domain service):
  1. User files report → status=pending
  2. Appears in admin moderation queue (filterable by target_type, reason)
  3. Admin reviews → sets reviewed_by, reviewed_at, status=reviewed
  4. Admin takes action (suspend user, remove listing, etc.) and sets
     status=resolved, OR status=dismissed if no action warranted
  5. Every status change beyond the initial review updates updated_at
     (v9.2 fix — tracks reopen/re-review timestamps)

  Resolving a report that results in an action (e.g. suspending a user)
  also creates an AdminAuditLog row (admin domain) — two writes, one
  transaction, coordinated by admin_service.

Anti-spam protection (Decision — service layer):
  A user can report the same entity MAX 3 times (app.constants.MAX_REPORTS_PER_ENTITY).
  Between reports on the same entity, there is a cooldown period
  (app.constants.REPORT_COOLDOWN_HOURS).
  No UNIQUE constraint — the service enforces limits with:
    1. COUNT(*) WHERE reporter_id=X AND target_type=Y AND target_id=Z
    2. If count >= MAX_REPORTS_PER_ENTITY → reject
    3. MAX(created_at) WHERE same criteria
    4. If (now - last_report) < REPORT_COOLDOWN_HOURS → reject

Account deletion + reports (Decision):
  reporter_id uses ON DELETE SET NULL.
  When a user requests account deletion (NDPR right to erasure):
    1. User row is deleted (personal data erased)
    2. reporter_id becomes NULL on all their reports
    3. Reports survive for moderation history
    4. Display logic: if reporter is NULL → show "Deleted Account"
  This preserves moderation records while respecting NDPR.

Resolution lives in AdminAuditLog (Decision):
  Report model does NOT store resolution text.
  Resolution documentation lives in AdminAuditLog (admin domain).
  The reporter receives a generic notification: "Your report has been reviewed."
  Separation of concerns: action logging is the admin domain's responsibility.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin
from app.constants import MAX_REPORTS_PER_ENTITY, REPORT_COOLDOWN_HOURS

if TYPE_CHECKING:
    from app.domains.users.models import User


# ─── Enums ───────────────────────────────────────────────────────────────────

class ReportTargetType(str, enum.Enum):
    """What kind of entity is being reported. Drives polymorphic lookup."""
    PROPERTY = "property"
    USER = "user"
    MESSAGE = "message"
    REVIEW = "review"


class ReportReason(str, enum.Enum):
    """
    Why the reporter is filing this report.
    Stored as PostgreSQL enum for type safety — these are standard
    moderation categories. Directly maps to app.constants.REPORT_REASONS.
    """
    FAKE_LISTING = "fake_listing"
    SCAM = "scam"
    INAPPROPRIATE_CONTENT = "inappropriate_content"
    HARASSMENT = "harassment"
    WRONG_INFORMATION = "wrong_information"
    OTHER = "other"


class ReportStatus(str, enum.Enum):
    """
    Moderation queue state.

    Valid transitions (enforced in report_service.py):
      PENDING  → REVIEWED   (admin starts reviewing)
      PENDING  → RESOLVED   (admin resolves directly — obvious violation)
      PENDING  → DISMISSED  (admin dismisses — invalid report)
      REVIEWED → RESOLVED   (admin takes action after investigation)
      REVIEWED → DISMISSED  (admin determines report is invalid after review)

    Terminal states: RESOLVED, DISMISSED.
    """
    PENDING = "pending"
    REVIEWED = "reviewed"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


# ─── SQLAlchemy Enum column types ────────────────────────────────────────────

_report_target_type_col = sa.Enum(
    ReportTargetType,
    name="report_target_type",
    values_callable=lambda obj: [e.value for e in obj],
)

_report_reason_col = sa.Enum(
    ReportReason,
    name="report_reason",
    values_callable=lambda obj: [e.value for e in obj],
)

_report_status_col = sa.Enum(
    ReportStatus,
    name="report_status",
    values_callable=lambda obj: [e.value for e in obj],
)


# ─── Model ───────────────────────────────────────────────────────────────────

class Report(Base, UUIDMixin, TimestampMixin):
    """
    A user-filed report. Polymorphic target via (target_type, target_id).

    Table: reports

    updated_at (v9.2 fix) is distinct from reviewed_at:
      reviewed_at — set once, when an admin first reviews the report
      updated_at  — bumped on ANY change (status transitions, re-review,
                    reopening). The TimestampMixin trigger handles this
                    automatically.

    Account deletion:
      reporter_id → SET NULL. Reports survive with reporter shown as
      "Deleted Account" in admin dashboard. Preserves moderation history
      while respecting NDPR right to erasure.

    Anti-spam:
      No UNIQUE constraint. Service enforces MAX_REPORTS_PER_ENTITY (3)
      and REPORT_COOLDOWN_HOURS between reports on the same entity.
      Allows re-reporting with new evidence after first report is dismissed.
    """

    __tablename__ = "reports"

    # ── Who filed the report ──────────────────────────────────────────────────
    reporter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "The user who filed this report. "
            "SET NULL: user deleted → report survives, display as 'Deleted Account'. "
            "Indexed: admin queries 'get all reports by user X'. "
            "Not shown to the reported entity — reporter identity is admin-only."
        ),
    )

    # ── What is being reported (polymorphic) ──────────────────────────────────
    target_type: Mapped[ReportTargetType] = mapped_column(
        _report_target_type_col, nullable=False,
        index=True,
        comment=(
            "Type of entity being reported: property | user | message | review. "
            "Drives polymorphic lookup in service layer."
        ),
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
        index=True,
        comment=(
            "Polymorphic FK — references properties.id, users.id, "
            "messages.id, or reviews.id depending on target_type. "
            "No DB-level FK constraint (target table varies)."
        ),
    )

    # ── Why it's being reported ───────────────────────────────────────────────
    reason: Mapped[ReportReason] = mapped_column(
        _report_reason_col, nullable=False,
        comment="Report reason from standard moderation categories.",
    )
    details: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Free-text description provided by the reporter.",
    )

    # ── Admin review tracking ─────────────────────────────────────────────────
    status: Mapped[ReportStatus] = mapped_column(
        _report_status_col, nullable=False,
        server_default=text("'pending'"),
        default=ReportStatus.PENDING,
        index=True,
        comment="Report lifecycle status. Indexed: admin queries 'get all pending reports'.",
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "Admin who first reviewed this report. "
            "SET NULL: admin deleted → report survives. "
            "Indexed: 'get all reports reviewed by admin X' queries."
        ),
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment=(
            "Set once, on first review. Does not change on later updates. "
            "Distinct from updated_at which tracks ANY change."
        ),
    )

    # ── Constraints ───────────────────────────────────────────────────────────
    __table_args__ = (
        # Composite index for admin dashboard: "show pending reports for entity type X"
        sa.Index(
            "idx_reports_target_status",
            "target_type",
            "status",
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    reporter: Mapped[Optional[User]] = relationship(
        "User",
        back_populates="reports_filed",
        foreign_keys=[reporter_id],
    )
    reviewer: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[reviewed_by],
        uselist=False,
    )

    # ── State transition methods ──────────────────────────────────────────────

    def mark_reviewed(self, admin_id: uuid.UUID, when: Optional[datetime] = None) -> None:
        """
        Sets reviewed_by/reviewed_at and advances status to REVIEWED if
        currently PENDING. Idempotent for reviewed_at — only sets it the
        first time. Caller flushes/commits via repository.

        Usage in report_service:
            report.mark_reviewed(admin_id=current_user.id)
            await db.commit()
        """
        if self.reviewed_at is None:
            self.reviewed_at = when or datetime.now(tz=timezone.utc)
            self.reviewed_by = admin_id
        if self.status == ReportStatus.PENDING:
            self.status = ReportStatus.REVIEWED

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_pending(self) -> bool:
        return self.status == ReportStatus.PENDING

    @is_pending.expression
    def is_pending(cls):
        """
        SQL: WHERE Report.is_pending

        Used in admin dashboard:
            stmt = select(Report).where(Report.is_pending)
        """
        return cls.status == ReportStatus.PENDING

    @hybrid_property
    def needs_attention(self) -> bool:
        """
        Report is in the admin's active queue.
        Pending or reviewed — not yet resolved or dismissed.
        """
        return self.status in (ReportStatus.PENDING, ReportStatus.REVIEWED)

    @needs_attention.expression
    def needs_attention(cls):
        """
        SQL: WHERE Report.needs_attention

        Used in admin badge count:
            count = select(func.count()).where(Report.needs_attention)
            # "You have 12 unresolved reports"
        """
        return cls.status.in_([ReportStatus.PENDING, ReportStatus.REVIEWED])

    @hybrid_property
    def is_actioned(self) -> bool:
        """Report has reached a final outcome — no further action needed."""
        return self.status in (ReportStatus.RESOLVED, ReportStatus.DISMISSED)

    @is_actioned.expression
    def is_actioned(cls):
        """SQL: WHERE Report.is_actioned"""
        return cls.status.in_([ReportStatus.RESOLVED, ReportStatus.DISMISSED])

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def reporter_display_name(self) -> str:
        """
        Display name for the reporter. Shows "Deleted Account" if the
        reporter's account has been deleted (reporter_id is NULL).
        Used in admin dashboard report list.
        """
        if self.reporter is None:
            return "Deleted Account"
        return self.reporter.full_name

    @property
    def is_about_property(self) -> bool:
        return self.target_type == ReportTargetType.PROPERTY

    @property
    def is_about_user(self) -> bool:
        return self.target_type == ReportTargetType.USER

    @property
    def is_about_message(self) -> bool:
        return self.target_type == ReportTargetType.MESSAGE

    @property
    def is_about_review(self) -> bool:
        return self.target_type == ReportTargetType.REVIEW

    @property
    def target_display(self) -> str:
        """
        Human-readable description of what's being reported.
        Used in admin dashboard report list.
        """
        return f"{self.target_type.value.title()}: {self.target_id}"

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Report id={self.id} "
            f"target={self.target_type.value}:{self.target_id} "
            f"reason={self.reason.value!r} "
            f"status={self.status.value!r}>"
        )


"""Admin resolves a report:
       ↓
admin_service.resolve_report(db, report_id, action="suspend_property")
       ↓
Two writes in ONE transaction:
  1. report.status = ReportStatus.RESOLVED
     (no resolution text on report — it's in the audit log)

  2. AdminAuditLog created:
     {
       admin_id: admin.id,
       action: "suspend_property",
       target_type: "property",
       target_id: property.id,
       details: "Confirmed fake photos — 5 reports from different users",
       report_id: report.id,
     }
       ↓
Admin takes action on entity:
  property.approval_status = ApprovalStatus.SUSPENDED
       ↓
Notification to reporter:
  "Your report has been reviewed. Thank you for helping keep the platform safe."
  (generic — no specific action details shown to reporter)"""
