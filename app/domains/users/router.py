"""
domains/users/router.py

HTTP layer for the users domain. No business logic -- calls service,
returns responses.

Endpoints:
  GET  /users/me                     -- own full profile
  PATCH /users/me                    -- update own profile
  GET  /users/me/notifications       -- own notification settings
  PATCH /users/me/notifications      -- update notification settings
  GET  /users/{id}                   -- public profile of any user
  GET  /agents                       -- paginated public agent directory
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.domains.users.models import User
from app.domains.users.schemas import (
    AgentPublicRead,
    NotificationSettingsRead,
    NotificationSettingsUpdate,
    UserPublicRead,
    UserRead,
    UserUpdate,
)
from app.domains.users.service import UserService
from app.shared.schemas import PaginatedResponse, SuccessResponse

router = APIRouter(prefix="/users", tags=["users"])
agents_router = APIRouter(prefix="/agents", tags=["agents"])


# ── GET /users/me ─────────────────────────────────────────────────────────────


@router.get(
    "/me",
    response_model=SuccessResponse[UserRead],
    summary="Get own profile",
)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[UserRead]:
    """Return the authenticated user's full profile."""
    svc = UserService(db)
    return SuccessResponse.ok(data=await svc.get_me(current_user))


# ── PATCH /users/me ───────────────────────────────────────────────────────────


@router.patch(
    "/me",
    response_model=SuccessResponse[UserRead],
    summary="Update own profile",
)
async def update_me(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[UserRead]:
    """Update the authenticated user's own profile fields."""
    svc = UserService(db)
    return SuccessResponse.ok(
        data=await svc.update_me(current_user, data),
        message="Profile updated",
    )


# ── GET /users/me/notifications ───────────────────────────────────────────────


@router.get(
    "/me/notifications",
    response_model=SuccessResponse[NotificationSettingsRead],
    summary="Get own notification settings",
)
async def get_notification_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[NotificationSettingsRead]:
    """Return the authenticated user's notification preferences."""
    svc = UserService(db)
    return SuccessResponse.ok(data=await svc.get_notification_settings(current_user))


# ── PATCH /users/me/notifications ─────────────────────────────────────────────


@router.patch(
    "/me/notifications",
    response_model=SuccessResponse[NotificationSettingsRead],
    summary="Update notification settings",
)
async def update_notification_settings(
    data: NotificationSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[NotificationSettingsRead]:
    """Update the authenticated user's notification preferences."""
    svc = UserService(db)
    return SuccessResponse.ok(
        data=await svc.update_notification_settings(current_user, data),
        message="Notification settings updated",
    )


# ── GET /agents ───────────────────────────────────────────────────────────────


@agents_router.get(
    "",
    response_model=PaginatedResponse[AgentPublicRead],
    summary="Paginated public agent directory",
)
async def list_agents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    verified_only: bool = Query(False, description="Only show verified agents"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AgentPublicRead]:
    """Browse the public agent directory with optional verification filter."""
    svc = UserService(db)
    return await svc.list_agents(page, per_page, verified_only)


# ── GET /users/{id} ───────────────────────────────────────────────────────────


@router.get(
    "/{user_id}",
    response_model=SuccessResponse[UserPublicRead],
    summary="Get public profile of any user",
)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[UserPublicRead]:
    """
    Fetch a user's public profile by ID.
    Suspended users return 404. Full agent profiles use GET /agents.
    """
    svc = UserService(db)
    return SuccessResponse.ok(data=await svc.get_public_profile(user_id))
