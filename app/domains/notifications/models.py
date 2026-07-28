# app/domains/notifications/models.py

"""
Domain: Notifications
Tables: notifications, user_notification_settings

Two models that together handle all platform notifications.

Notification              — a single notification sent to a user
UserNotificationSettings  — per-user preferences for which notifications they receive

Notification delivery flow:
  1. Some event triggers (booking confirmed, property approved, etc.)
  2. notification_service.create() creates a Notification row
  3. notification_service checks UserNotificationSettings for this user:
     - email_enabled + event toggle (e.g. notify_booking_update)
     - push_enabled + event toggle
     - sms_enabled + event toggle
  4. For each enabled channel:
     - Celery task dispatches via integrations/email.py, integrations/sms.py, etc.

Deep-linking (v9.2):
  Every notification has related_type and related_id.
  The frontend uses these to navigate directly to the relevant entity:
    related_type="booking",  related_id="abc-123"  → /bookings/abc-123
    related_type="property", related_id="def-456"  → /properties/def-456
    related_type="message",  related_id="ghi-789"  → /messages/ghi-789

  related_type values (validated in notification_service):
    booking | property | message | subscription | kyc | report

One notification settings row per user:
  UserNotificationSettings.user_id has a UNIQUE constraint.
  Created once when user registers (in auth_service.register()).
  Updated via PATCH /notifications/settings.

Why notifications are never deleted:
  They serve as an activity log. Users can mark them as read (is_read=True)
  but the rows persist. Old notifications may be archived by a future
  Celery task, but never physically deleted.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import CreatedAtMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.domains.users.models import User


# ─── Event type → settings column mapping ────────────────────────────────────

# Maps event_type strings (passed to NotificationService.create())
# to the corresponding boolean toggle on UserNotificationSettings.
#
# None = mandatory event (no per-user toggle, always sent).
# Fail-open: unmapped event types are always delivered.
NOTIFY_TYPE_MAP: dict[str, str | None] = {
    "new_message": "notify_new_message",
    "booking_update": "notify_booking_update",
    "listing_approved": "notify_listing_approved",
    "saved_search_match": "notify_saved_search",
    "listing_expiring": "notify_listing_expiry",
    "subscription_expiry": "notify_subscription",
    "kyc_verified": None,
    "report_resolved": None,
}

# Mapping for frontend deep-link route generation
RELATED_TYPE_TO_PATH: dict[str, str] = {
    "booking": "bookings",
    "property": "properties",
    "message": "messages",
    "subscription": "subscriptions",
    "kyc": "kyc",
    "report": "reports",
}


# ─── Notification Model ──────────────────────────────────────────────────────


class Notification(Base, UUIDMixin, CreatedAtMixin):
    """
    A single notification sent to a user.
    Table: notifications

        Uses CreatedAtMixin (not TimestampMixin):
      Notifications are never updated. The only mutation is is_read toggling,
      which doesn't warrant an updated_at trigger.

    Deep-link fields (v9.2):
      related_type + related_id tell the frontend which entity to navigate to.
      Example: {"related_type": "booking", "related_id": "abc-123"}
      Frontend constructs URL: /bookings/abc-123

    Common notification types:
      - booking_confirmed: "Your booking for Flat 3A has been confirmed"
      - property_approved: "Your listing '3 bed in Lekki' has been approved"
      - new_message: "You have a new message from Agent John"
      - subscription_expiring: "Your subscription expires in 3 days"
      - kyc_verified: "Your identity has been verified"
      - report_resolved: "Your report has been reviewed"
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # Composite Index: Fast feed pagination ordered by recency
        Index(
            "ix_notifications_user_created_at",
            "user_id",
            text("created_at DESC"),
        ),
        # Partial Index: Ultra-fast unread count badge queries
        Index(
            "ix_notifications_unread_user",
            "user_id",
            postgresql_where=text("is_read = false"),
        ),
    )

    # ── Who receives this notification ────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="CASCADE: user deleted -> their notifications are disposable.",
    )

    # ── Content ───────────────────────────────────────────────────────────────
    type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "Notification type. Drives icon/color on frontend. "
            "Examples: booking_confirmed, property_approved, new_message, "
            "subscription_expiring, kyc_verified, report_resolved."
        ),
    )
    message: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        comment="Human-readable notification text shown in notification feed.",
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
        comment=(
            "False = unread (bold on frontend). True = read. "
            "Toggled by PATCH /notifications/{id}/read. "
            "Indexed: 'unread count' queries filter by is_read=False."
        ),
    )

    # ── Deep-link (v9.2) ─────────────────────────────────────────────────────
    related_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment=(
            "Deep-link target type. Combined with related_id, frontend navigates "
            "directly to the relevant entity. Valid values: "
            "booking | property | message | subscription | kyc | report. "
            "Validated in notification_service — not a PostgreSQL enum (flexible)."
        ),
    )
    related_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment=(
            "Deep-link target ID. Combined with related_type, frontend constructs "
            "the exact URL. Example: related_type='booking', related_id='abc-123' "
            "→ frontend navigates to /bookings/abc-123."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="notifications",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_unread(self) -> bool:
        """Inverted is_read boolean for Python-side checks."""
        return not self.is_read

    @is_unread.expression
    def is_unread(cls):
        """SQL expression: WHERE Notification.is_unread"""
        return cls.is_read.is_(False)

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def has_deep_link(self) -> bool:
        """True if this notification can navigate to a specific entity."""
        return self.related_type is not None and self.related_id is not None

    @property
    def deep_link_path(self) -> str | None:
        """
        Constructs the frontend navigation path from related_type and related_id.
        Returns None if no deep-link is set.

        Frontend uses this directly:
            router.push(notification.deep_link_path)
        Example: related_type="booking", related_id="abc-123" -> "/bookings/abc-123"
        Returns None if missing identifiers or unrecognized type.
        """
        # Guard clause: Both related_type AND related_id must be non-None
        if not self.has_deep_link:
            return None

        path_segment = RELATED_TYPE_TO_PATH.get(self.related_type)  # type: ignore[arg-type]
        if not path_segment:
            return None

        return f"/{path_segment}/{self.related_id}"

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        read_status = "read" if self.is_read else "unread"
        return (
            f"<Notification id={self.id} "
            f"user_id={self.user_id} "
            f"type={self.type!r} "
            f"status={read_status}>"
        )


# ─── UserNotificationSettings Model ─────────────────────────────────────────


class UserNotificationSettings(Base, UUIDMixin, TimestampMixin):
    """
    Per-user notification preferences. One row per user (1:1 relationship).
    Table: user_notification_settings

    Created once when user registers (in auth_service.register()).
    Updated via PATCH /notifications/settings.

    Two levels of control:
      1. Channel toggles (email_enabled, push_enabled, sms_enabled):
         Master switches for each delivery channel.
         If email_enabled=False, no emails are sent regardless of event toggles.

      2. Event toggles (notify_new_message, notify_booking_update, etc.):
         Per-event switches within each channel.
         Example: user wants email for bookings but not for messages.
         Currently these are global (apply to all enabled channels).
         Per-channel event toggles (email_booking_update, push_booking_update)
         are a future enhancement if users request finer control.

    Marketing toggles (marketing_email, marketing_sms):
      Separate from transactional notifications. Default to False (opt-in).
      NDPR/GDPR compliance: users must explicitly opt in.
    """

    __tablename__ = "user_notification_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="One settings row per user. UNIQUE enforces 1:1.",
    )

    # ── Channel toggles (master switches) ─────────────────────────────────────
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    push_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    sms_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    # ── Event toggles (per-event switches) ────────────────────────────────────
    notify_new_message: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    notify_booking_update: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    notify_listing_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    notify_saved_search: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    notify_listing_expiry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    notify_subscription: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )

    # ── Marketing toggles (opt-in, NDPR compliance) ──────────────────────────
    marketing_email: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    marketing_sms: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="notification_settings",
    )

    # ── Query methods ─────────────────────────────────────────────────────────

    def wants_notification(self, event_type: str) -> bool:
        """
        Whether this user wants to receive a given event type.
        Called by NotificationService.create() before inserting.

        Fail-open: unmapped event types are always delivered.
        """
        if event_type not in NOTIFY_TYPE_MAP:
            return True

        flag_column = NOTIFY_TYPE_MAP[event_type]
        if flag_column is None:
            return True  # Mandatory notification

        return getattr(self, flag_column, True)

    def channel_enabled(self, channel: str) -> bool:
        """
        Whether a specific delivery channel is enabled.
        Called by Celery tasks before dispatching email/SMS/push.
        """
        mapping = {
            "email": self.email_enabled,
            "push": self.push_enabled,
            "sms": self.sms_enabled,
        }
        return mapping.get(channel, False)

    # ── Display properties ────────────────────────────────────────────────────

    @property
    def active_channels(self) -> list[str]:
        """Returns list of enabled channel names."""
        channels = []
        if self.email_enabled:
            channels.append("email")
        if self.push_enabled:
            channels.append("push")
        if self.sms_enabled:
            channels.append("sms")
        return channels

    @property
    def has_any_channel(self) -> bool:
        """True if at least one delivery channel is enabled."""
        return self.email_enabled or self.push_enabled or self.sms_enabled

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<UserNotificationSettings "
            f"user_id={self.user_id} "
            f"email={self.email_enabled} "
            f"push={self.push_enabled} "
            f"sms={self.sms_enabled}>"
        )
