"""
domains/users/repository.py

Data access for the users domain. Raw SQLAlchemy queries only --
no business logic, no HTTP exceptions, no schema imports.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications.models import UserNotificationSettings
from app.domains.users.models import User, UserRole


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Fetch by primary key. Returns None if not found."""
        return await self.db.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Fetch by normalised email. Returns None if not found."""
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def list_agents(
        self,
        page: int,
        per_page: int,
        verified_only: bool = False,
    ) -> tuple[list[User], int]:
        """
        Paginated list of agents for the public agent directory.
        verified_only filters to agents with the verified badge.
        Returns (agents, total_count).
        """
        base = select(User).where(User.role == UserRole.AGENT)
        if verified_only:
            base = base.where(User.verified.is_(True))

        # Capture total aggregate matching rows using an explicit subquery block
        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar_one()

        # Fetch the exact page window slice
        result = await self.db.execute(
            base.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        )
        agents = list(result.scalars().all())
        return agents, total

    async def get_notification_settings(
        self, user_id: uuid.UUID
    ) -> UserNotificationSettings | None:
        """Fetch notification settings for a user. Returns None if not found."""
        result = await self.db.execute(
            select(UserNotificationSettings).where(UserNotificationSettings.user_id == user_id)
        )
        return result.scalar_one_or_none()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def update(self, user: User, updates: dict[str, Any]) -> User:
        """
        Apply field updates from a plain dict. Only touches keys
        present in the dict -- caller controls which fields to update
        (typically via schema.model_dump(exclude_none=True)).

        Args:
            user: The User model instance to update.
            updates: Dict of {field_name: new_value}.

        Returns:
            The updated User instance (flushed, not committed).
        """
        for field, value in updates.items():
            setattr(user, field, value)
        await self.db.flush()
        return user

    async def update_notification_settings(
        self,
        settings: UserNotificationSettings,
        updates: dict[str, Any],
    ) -> UserNotificationSettings:
        """
        Apply field updates to notification settings from a plain dict.
        Same pattern as update() -- caller controls which fields.
        """
        for field, value in updates.items():
            setattr(settings, field, value)
        await self.db.flush()
        return settings
