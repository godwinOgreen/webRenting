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
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from app.db.base import Base
from app.db.mixins import UUIDMixin, TimestampMixin, CreatedAtMixin

if TYPE_CHECKING:
    from app.domains.users.models import User


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

    # ── Who receives this notification ────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "CASCADE: user deleted → their notifications are disposable. "
            "Indexed: notification feed queries filter by user_id."
        ),
    )

    # ── Content ───────────────────────────────────────────────────────────────
    type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment=(
            "Notification type. Drives icon/color on frontend. "
            "Examples: booking_confirmed, property_approved, new_message, "
            "subscription_expiring, kyc_verified, report_resolved."
        ),
    )
    message: Mapped[str] = mapped_column(
        String(1000), nullable=False,
        comment="Human-readable notification text. Shown in notification feed.",
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("false"),
        default=False,
        index=True,
        comment=(
            "False = unread (bold on frontend). True = read. "
            "Toggled by PATCH /notifications/{id}/read. "
            "Indexed: 'unread count' queries filter by is_read=False."
        ),
    )

    # ── Deep-link (v9.2) ─────────────────────────────────────────────────────
    related_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment=(
            "Deep-link target type. Combined with related_id, frontend navigates "
            "directly to the relevant entity. Valid values: "
            "booking | property | message | subscription | kyc | report. "
            "Validated in notification_service — not a PostgreSQL enum (flexible)."
        ),
    )
    related_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
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
        """Inverted is_read — more natural for filtering unread notifications."""
        return not self.is_read

    @is_unread.expression
    def is_unread(cls):
        """
        SQL: WHERE Notification.is_unread

        Used in unread count query:
            stmt = select(func.count()).where(
                Notification.user_id == user_id,
                Notification.is_unread,
            )
        """
        return cls.is_read == False  # noqa: E712

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def has_deep_link(self) -> bool:
        """True if this notification can navigate to a specific entity."""
        return self.related_type is not None and self.related_id is not None

    @property
    def deep_link_path(self) -> Optional[str]:
        """
        Constructs the frontend navigation path from related_type and related_id.
        Returns None if no deep-link is set.

        Frontend uses this directly:
            router.push(notification.deep_link_path)

        Example:
            related_type="booking", related_id="abc-123" → "/bookings/abc-123"
            related_type="property", related_id="def-456" → "/properties/def-456"
        """
        if not self.has_deep_link:
            return None
        # Map related_type to URL path segment
        type_to_path = {
            "booking": "bookings",
            "property": "properties",
            "message": "messages",
            "subscription": "subscriptions",
            "kyc": "kyc",
            "report": "reports",
        }
        path_segment = type_to_path.get(self.related_type)
        if path_segment is None:
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
        comment=(
            "One settings row per user. UNIQUE enforces 1:1. "
            "CASCADE: user deleted → their settings are disposable."
        ),
    )

    # ── Channel toggles (master switches) ─────────────────────────────────────
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        comment="Master switch for email notifications. If False, no emails sent.",
    )
    push_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        comment="Master switch for push notifications (FCM). If False, no pushes sent.",
    )
    sms_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("false"),
        default=False,
        comment=(
            "Master switch for SMS notifications. Default False (SMS costs money). "
            "Only enabled for critical alerts (e.g. payment confirmation)."
        ),
    )

    # ── Event toggles (per-event switches) ────────────────────────────────────
    notify_new_message: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        comment="New message received in a conversation.",
    )
    notify_booking_update: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        comment="Booking confirmed, rejected, or completed.",
    )
    notify_listing_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        comment="Property listing approved or rejected by admin.",
    )
    notify_saved_search: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        comment="New property matches a saved search alert.",
    )
    notify_listing_expiry: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        comment="Listing is about to expire (7-day warning).",
    )
    notify_subscription: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        comment="Subscription expiring soon (3-day warning) or expired.",
    )

    # ── Marketing toggles (opt-in, NDPR compliance) ──────────────────────────
    marketing_email: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("false"),
        default=False,
        comment="Marketing emails. Default False (opt-in). NDPR compliance.",
    )
    marketing_sms: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("false"),
        default=False,
        comment="Marketing SMS. Default False (opt-in). NDPR compliance.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="notification_settings",
    )

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def active_channels(self) -> list[str]:
        """Returns list of enabled channel names. Used by notification_service."""
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
