"""
domains/notifications/service.py

Business logic for the notifications domain.

create() is the integration point every other domain's service and
every Celery task calls into to notify a user. It is the one method
in this entire file that other domains are allowed to call directly
(01_ARCHITECTURE.md §3 forbids cross-domain SERVICE imports... but
notification dispatch is the documented, deliberate exception used
throughout the platform — see note below).

──────────────────────────────────────────────────────────────────────
Cross-domain exception, documented once here rather than repeated in
every calling domain:

  Standard 1 says services cannot import other domains' services. In
  practice, nearly every domain needs to fire a notification (booking
  confirmed, payment succeeded, listing approved, message received...).
  Two options were considered:

    A) Every domain duplicates notification-creation logic locally.
       Rejected — directly violates "single source of truth," and the
       NOTIFY_TYPE_MAP / channel-dispatch logic on
       UserNotificationSettings would need to be reimplemented or
       imported into every domain anyway.

    B) NotificationService.create() is treated as a narrow, intentional
       exception to the cross-domain-service rule — the one shared
       "fire and forget" call every domain is allowed to make, the same
       way logging or metrics emission would be.

  Option B is what's implemented. Calling domains import ONLY
  NotificationService (not its repository), and only call create() —
  never list_for_user(), mark_read(), etc. This keeps the blast radius
  of the exception to exactly one method.
──────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.domains.notifications.models import Notification
from app.domains.notifications.repository import NotificationRepository
from app.domains.notifications.schemas import NotificationRead, UnreadCountRead
from app.domains.users.models import User
from app.shared.schemas import PaginatedResponse

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = NotificationRepository(db)

    # ── Create (cross-domain integration point) ──────────────────────────────

    async def create(
        self,
        user_id: uuid.UUID,
        event_type: str,
        message: str,
        related_type: Optional[str] = None,
        related_id: Optional[uuid.UUID] = None,
    ) -> Optional[Notification]:
        """
        Create a notification for a user, respecting their notification
        settings. Returns None (and creates nothing) if the user has
        disabled this event_type — see UserNotificationSettings.
        wants_notification() for the type→flag mapping and its
        fail-open default for unmapped event types.

        event_type examples: "booking_update", "listing_approved",
        "new_message", "saved_search_match", "listing_expiring",
        "subscription_expiry", "kyc_verified"

        related_type/related_id: see Notification model docstring for
        the deep-link contract (v9.2 fix).

        This method ONLY creates the in-app Notification row. Dispatch
        to email/SMS/push channels is the responsibility of the calling
        Celery task or service, which should separately check
        settings.channel_enabled("email") etc. and enqueue the relevant
        integrations/email.py, integrations/sms.py, integrations/push.py
        call. Keeping channel dispatch OUT of this method means a
        synchronous request (e.g. booking_service.confirm()) doesn't
        block on an outbound email API call — it creates the in-app
        notification immediately and lets a Celery task handle the
        slower channels asynchronously.
        """
        settings = await self.repo.get_settings_for_user(user_id)
        if settings is not None and not settings.wants_notification(event_type):
            logger.debug(
                "Notification suppressed by user settings",
                extra={"user_id": str(user_id), "event_type": event_type},
            )
            return None

        notification = await self.repo.create(
            user_id=user_id,
            type_=event_type,
            message=message,
            related_type=related_type,
            related_id=related_id,
        )
        logger.info(
            "Notification created",
            extra={
                "notification_id": str(notification.id),
                "user_id": str(user_id),
                "type": event_type,
            },
        )
        return notification

    # ── Read ──────────────────────────────────────────────────────────────────

    async def list_for_user(
        self, user: User, page: int, per_page: int
    ) -> PaginatedResponse[NotificationRead]:
        notifications, total = await self.repo.list_for_user(user.id, page, per_page)
        items = [NotificationRead.model_validate(n) for n in notifications]
        return PaginatedResponse.paginate(items, total, page, per_page)

    async def get_unread_count(self, user: User) -> UnreadCountRead:
        count = await self.repo.count_unread(user.id)
        return UnreadCountRead(count=count)

    # ── Mark read ─────────────────────────────────────────────────────────────

    async def mark_read(
        self, notification_id: uuid.UUID, user: User
    ) -> NotificationRead:
        notification = await self.repo.get_by_id(notification_id)
        if notification is None or notification.user_id != user.id:
            raise NotFoundException(message="Notification not found")
        updated = await self.repo.mark_read(notification)
        return NotificationRead.model_validate(updated)

    async def mark_all_read(self, user: User) -> int:
        """Returns the number of notifications marked read."""
        count = await self.repo.mark_all_read(user.id)
        logger.info(
            "All notifications marked read",
            extra={"user_id": str(user.id), "count": count},
        )
        return count