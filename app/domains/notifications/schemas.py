"""
domains/notifications/schemas.py

Request/response schemas for the notifications domain.

No NotificationCreate schema exposed via the API — notifications are
only ever created server-side (by other domains' services and Celery
tasks calling into notification_service.create()), never directly by
a client request.

UserNotificationSettings schemas already exist in domains/users/schemas.py
(NotificationSettingsRead / NotificationSettingsUpdate) since settings
are naturally part of a user's profile — this domain only owns the
Notification model (the inbox itself), not the settings model.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationRead(BaseModel):
    id: uuid.UUID
    type: str
    message: str
    related_type: str | None = None
    related_id: uuid.UUID | None = None
    is_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UnreadCountRead(BaseModel):
    """
    Lightweight response for the notification bell badge — just a
    count, not the full list. Polled frequently by the frontend, so
    kept minimal on purpose.
    """

    count: int
