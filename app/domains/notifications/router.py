# app/domains/notifications/router.py

"""
HTTP layer for the notifications domain. No POST / endpoint —
notifications are never created via a direct API call (see
service.py docstring on the create() cross-domain exception).

Notification settings (GET/PATCH) live on the users router
(/users/me/notification-settings) since they're part of the user
profile — not duplicated here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.domains.notifications.schemas import NotificationRead, UnreadCountRead
from app.domains.notifications.service import NotificationService
from app.domains.users.models import User
from app.shared.schemas import PaginatedResponse, SuccessResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


# ── GET /notifications ────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=PaginatedResponse[NotificationRead],
    summary="List own notifications",
)
async def list_notifications(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[NotificationRead]:
    svc = NotificationService(db)
    return await svc.list_for_user(current_user, page, per_page)


# ── GET /notifications/unread-count ───────────────────────────────────────────

@router.get(
    "/unread-count",
    response_model=SuccessResponse[UnreadCountRead],
    summary="Get unread notification count (for the bell badge)",
)
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[UnreadCountRead]:
    svc = NotificationService(db)
    return SuccessResponse.ok(data=await svc.get_unread_count(current_user))


# ── POST /notifications/read-all ──────────────────────────────────────────────
# NOTE: Placed BEFORE /{notification_id}/read to prevent FastAPI route shadowing!

@router.post(
    "/read-all",
    response_model=SuccessResponse[None],
    summary="Mark all notifications as read",
)
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[None]:
    svc = NotificationService(db)
    count = await svc.mark_all_read(current_user)
    return SuccessResponse.ok(
        data=None,
        message=f"{count} notification(s) marked as read",
    )


# ── POST /notifications/{notification_id}/read ────────────────────────────────

@router.post(
    "/{notification_id}/read",
    response_model=SuccessResponse[NotificationRead],
    summary="Mark a single notification as read",
)
async def mark_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[NotificationRead]:
    svc = NotificationService(db)
    return SuccessResponse.ok(
        data=await svc.mark_read(notification_id, current_user)
    )