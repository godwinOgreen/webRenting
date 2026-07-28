"""
domains/users/service.py

Business logic for the users domain. Orchestrates UserRepository
and enforces domain rules. No direct DB queries, no HTTP concerns.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.domains.users.models import User, UserRole
from app.domains.users.repository import UserRepository
from app.domains.users.schemas import (
    AgentPublicRead,
    NotificationSettingsRead,
    NotificationSettingsUpdate,
    UserPublicRead,
    UserRead,
    UserUpdate,
)
from app.shared.schemas import PaginatedResponse

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = UserRepository(db)

    # ── Self ──────────────────────────────────────────────────────────────────

    async def get_me(self, user: User) -> UserRead:
        """
        Return the authenticated user's own profile.
        The user is already loaded by get_current_user -- no DB query needed.
        """
        return UserRead.model_validate(user)

    async def update_me(self, user: User, data: UserUpdate) -> UserRead:
        """
        Update the authenticated user's own profile.

        Agent-only fields (agency_name, agent_bio, etc.) are accepted
        in the schema but silently stripped for renters -- the schema
        allows them so the same update endpoint works for both roles
        without separate routes, but we don't persist irrelevant fields.
        """
        updates = data.model_dump(exclude_none=True)

        if user.role != UserRole.AGENT:
            # Declaratively strip professional attributes from tenant data maps
            for field in ("agency_name", "agent_bio", "license_number", "years_experience"):
                updates.pop(field, None)

        updated = await self.repo.update(user, updates)
        logger.info("User profile updated", extra={"user_id": str(user.id)})
        return UserRead.model_validate(updated)

    # ── Public ────────────────────────────────────────────────────────────────

    async def get_public_profile(self, user_id: uuid.UUID) -> UserPublicRead:
        """
        Fetch a user's public profile. Used by GET /users/{id}.
        Suspended users are hidden from public view.

        Note: response_model on the router is SuccessResponse[UserPublicRead],
        so agent-specific fields (agency_name, etc.) are not included here.
        Full agent profiles are available via GET /agents.
        """
        user = await self.repo.get_by_id(user_id)
        if user is None or user.is_suspended:
            raise NotFoundException(
                message="User not found",
                log_context={"user_id": str(user_id)},
            )
        return UserPublicRead.model_validate(user)

    async def list_agents(
        self,
        page: int,
        per_page: int,
        verified_only: bool = False,
    ) -> PaginatedResponse[AgentPublicRead]:
        """Paginated public agent directory."""
        agents, total = await self.repo.list_agents(page, per_page, verified_only)
        items = [AgentPublicRead.model_validate(a) for a in agents]
        return PaginatedResponse.paginate(items, total, page, per_page)

    # ── Notification settings ─────────────────────────────────────────────────

    async def get_notification_settings(self, user: User) -> NotificationSettingsRead:
        """Fetch the authenticated user's notification settings."""
        settings = await self.repo.get_notification_settings(user.id)
        if settings is None:
            raise NotFoundException(
                message="Notification settings not found",
                log_context={"user_id": str(user.id)},
            )
        return NotificationSettingsRead.model_validate(settings)

    async def update_notification_settings(
        self,
        user: User,
        data: NotificationSettingsUpdate,
    ) -> NotificationSettingsRead:
        """Partial update of the authenticated user's notification settings."""
        settings = await self.repo.get_notification_settings(user.id)
        if settings is None:
            raise NotFoundException(
                message="Notification settings not found",
                log_context={"user_id": str(user.id)},
            )
        updates = data.model_dump(exclude_none=True)
        updated = await self.repo.update_notification_settings(settings, updates)
        logger.info(
            "Notification settings updated",
            extra={"user_id": str(user.id)},
        )
        return NotificationSettingsRead.model_validate(updated)
