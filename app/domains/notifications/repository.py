# app/domains/notifications/repository.py

"""
Data access for the notifications domain (the Notification inbox
model and UserNotificationSettings — both belong to this domain).
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import MAX_UNREAD_NOTIFICATIONS
from app.domains.notifications.models import Notification, UserNotificationSettings


class NotificationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── User Settings (Owned by Notifications Domain) ─────────────────────────

    async def get_settings_for_user(
        self, user_id: uuid.UUID
    ) -> Optional[UserNotificationSettings]:
        """
        Fetches notification settings for a user.
        Owned by NotificationRepository because UserNotificationSettings
        lives in app/domains/notifications/models.py.
        """
        stmt = select(UserNotificationSettings).where(
            UserNotificationSettings.user_id == user_id
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_id(
        self, notification_id: uuid.UUID
    ) -> Optional[Notification]:
        return await self.db.get(Notification, notification_id)

    async def list_for_user(
        self, user_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[Notification], int]:
        base = select(Notification).where(Notification.user_id == user_id)
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            base.order_by(Notification.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total

    async def count_unread(self, user_id: uuid.UUID) -> int:
        """
        Capped at MAX_UNREAD_NOTIFICATIONS for the badge display —
        a user with 500 unread notifications sees "50+", not "500",
        avoiding a full table scan misrepresented as a precise count
        on every badge poll. The cap is applied via LIMIT inside a
        subquery, then counted, rather than a raw COUNT(*) — this
        bounds the cost of the query itself, not just the displayed
        number.
        """
        capped = (
            select(Notification.id)
            .where(Notification.user_id == user_id, Notification.is_read.is_(False))
            .limit(MAX_UNREAD_NOTIFICATIONS)
        )
        result = await self.db.execute(
            select(func.count()).select_from(capped.subquery())
        )
        return result.scalar_one()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create(
        self,
        user_id: uuid.UUID,
        type_: str,
        message: str,
        related_type: Optional[str] = None,
        related_id: Optional[uuid.UUID] = None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            type=type_,
            message=message,
            related_type=related_type,
            related_id=related_id,
        )
        self.db.add(notification)
        await self.db.flush()
        return notification

    async def mark_read(self, notification: Notification) -> Notification:
        notification.is_read = True
        await self.db.flush()
        return notification

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        """
        Bulk UPDATE rather than load-each-and-save — a user with
        hundreds of unread notifications hitting "mark all read"
        should be one UPDATE statement, not N round trips.
        Returns the number of rows updated.
        """
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
        )
        await self.db.flush()
        return result.rowcount