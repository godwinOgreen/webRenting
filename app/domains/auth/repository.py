"""
domains/auth/repository.py

Data access for authentication flows. Only raw database operations --
no business logic, no password checks, no token generation.

All business rules (duplicate email, wrong password, suspended user)
live in auth/service.py. This file only knows how to query and write
the users table and the rows created at registration (notification
settings, consent log).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent.models import UserConsentLog
from app.domains.notifications.models import UserNotificationSettings
from app.domains.users.models import User, UserRole


class AuthRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_email(self, email: str) -> User | None:
        """
        Fetch a user by their normalised (lowercase) email.
        Returns None if no match -- caller decides whether that's an
        error (login: yes) or expected (registration: no, proceed).
        """
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Fetch by primary key. Returns None if not found."""
        return await self.db.get(User, user_id)

    async def get_by_oauth(
        self,
        provider: str,
        provider_id: str,
    ) -> User | None:
        """
        Fetch user by OAuth provider + provider's own user ID.
        Used during OAuth callback: find existing account before
        creating a new one.
        """
        result = await self.db.execute(
            select(User).where(
                User.oauth_provider == provider,
                User.oauth_provider_id == provider_id,
            )
        )
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        """
        Lightweight duplicate check -- does not load the full User row.
        Used by register() to raise ConflictException before attempting
        the INSERT, producing a cleaner error than catching IntegrityError.
        """
        result = await self.db.execute(select(User.id).where(User.email == email.lower()))
        return result.scalar_one_or_none() is not None

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_user(
        self,
        *,
        email: str,
        password_hash: str | None,
        first_name: str,
        last_name: str,
        role: UserRole,
        oauth_provider: str | None = None,
        oauth_provider_id: str | None = None,
    ) -> User:
        """
        Insert a new User row and flush (assigns .id without committing).
        Caller handles final unit-of-work commit via get_db() session.

        password_hash is None for OAuth users -- the column is nullable
        and core/security.hash_password() is called by the service before
        this method, never inside a repository.
        """
        user = User(
            email=email.lower(),
            password_hash=password_hash,
            first_name=first_name,
            last_name=last_name,
            role=role,
            oauth_provider=oauth_provider,
            oauth_provider_id=oauth_provider_id,
        )
        self.db.add(user)
        await self.db.flush()  # Sends INSERT to DB, assigns user.id
        return user

    async def create_notification_settings(self, user_id: uuid.UUID) -> None:
        """
        Auto-creates UserNotificationSettings with all defaults for a
        new user. Called once at registration -- the unique constraint on
        user_id guarantees at most one row per user.
        """
        settings = UserNotificationSettings(user_id=user_id)
        self.db.add(settings)
        await self.db.flush()

    async def record_consent(
        self,
        user_id: uuid.UUID,
        consent_type: str,
        consented: bool,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """
        Append one row to the append-only user_consent_log.

        Called at registration for terms_of_service=True, and whenever
        the user toggles marketing preferences. Append-only -- this
        method never updates existing rows.
        """
        entry = UserConsentLog(
            user_id=user_id,
            consent_type=consent_type,
            consented=consented,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(entry)
        await self.db.flush()
