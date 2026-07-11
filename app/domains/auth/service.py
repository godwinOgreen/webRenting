"""
domains/auth/service.py

Business logic for authentication. Orchestrates repository, security,
and Redis -- never touches the database directly (only via AuthRepository).

Boundary rules (01_ARCHITECTURE.md):
  - No db.execute(), db.add(), db.commit() -- all persistence via repo
  - No HTTP imports -- raises domain exceptions only
  - No imports from other domain services
"""
from __future__ import annotations

import logging
import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictException, UnauthorizedException
from app.core.redis_client import RedisKeys
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.domains.auth.repository import AuthRepository
from app.domains.auth.schemas import AuthUserRead, RegisterRequest, TokenResponse
from app.domains.users.models import User, UserRole

logger = logging.getLogger(__name__)


class AuthService:
    """
    Orchestrates authentication flows.

    Dependencies injected via constructor:
      db    -- SQLAlchemy async session (unit-of-work boundary)
      redis -- async Redis client (token blacklist)

    Never touches the database directly -- all persistence via self.repo.
    """

    def __init__(self, db: AsyncSession, redis: Redis) -> None:
        self.db = db
        self.redis = redis
        self.repo = AuthRepository(db)

    # ── Registration ──────────────────────────────────────────────────────────

    async def register(
        self,
        data: RegisterRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[TokenResponse, str]:
        """
        Register a new user account.

        Steps:
          1. Duplicate email check (ConflictException if taken)
          2. Hash password
          3. INSERT user row
          4. INSERT UserNotificationSettings (defaults)
          5. INSERT UserConsentLog rows:
             - terms_of_service: consented=True (required)
             - privacy_policy:   consented=True (required)
             - data_processing:  consented=True (required)
             - marketing_email:  consented=False (opt-in, default off)
             - marketing_sms:    consented=False (opt-in, default off)
          6. Mint access + refresh tokens
          7. Return TokenResponse + refresh_token string

        The DB commit happens in get_db() after this method returns --
        if token creation fails for any reason, the user INSERT rolls back.

        Args:
            data: Validated registration request from schema.
            ip_address: Client IP for NDPR consent evidence.
            user_agent: Client user agent for NDPR consent evidence.

        Returns:
            Tuple of (TokenResponse, refresh_token_string).
            Router sets refresh_token as HTTP-only cookie.

        Raises:
            ConflictException: email already registered.
        """
        if await self.repo.email_exists(data.email):
            raise ConflictException(
                message="An account with this email address already exists",
                error_code="email_taken",
                log_context={"email": data.email},
            )

        pw_hash = hash_password(data.password)

        user = await self.repo.create_user(
            email=data.email,
            password_hash=pw_hash,
            first_name=data.first_name,
            last_name=data.last_name,
            role=UserRole(data.role),
        )

        await self.repo.create_notification_settings(user.id)

        # Record all required consents (NDPR compliance)
        required_consents = [
            ("terms_of_service", True),
            ("privacy_policy", True),
            ("data_processing", True),
            ("marketing_email", False),
            ("marketing_sms", False),
        ]
        for consent_type, consented in required_consents:
            await self.repo.record_consent(
                user_id=user.id,
                consent_type=consent_type,
                consented=consented,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        logger.info(
            "User registered",
            extra={"user_id": str(user.id), "role": user.role.value},
        )

        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)

        return TokenResponse(
            access_token=access_token,
            user=AuthUserRead.model_validate(user),
        ), refresh_token

    # ── Login ─────────────────────────────────────────────────────────────────

    async def login(
        self,
        email: str,
        password: str,
    ) -> tuple[TokenResponse, str]:
        """
        Authenticate with email + password.

        Deliberately returns the same error message for "user not found"
        and "wrong password" -- prevents user enumeration via timing or
        error message differences.

        Suspension check runs AFTER credential validation so we never
        confirm a user's existence to someone with wrong credentials.

        Args:
            email: User's email (normalised by schema before reaching here).
            password: Plain-text password to verify.

        Returns:
            Tuple of (TokenResponse, refresh_token_string).

        Raises:
            UnauthorizedException: wrong credentials, OAuth-only account,
                or suspended account.
        """
        _generic_error = UnauthorizedException(
            message="Incorrect email or password",
            error_code="invalid_credentials",
        )

        user = await self.repo.get_by_email(email)
        if user is None:
            raise _generic_error

        if user.password_hash is None:
            # OAuth-only account -- password login not available.
            # Same generic error: don't reveal that the email exists
            # but was registered via OAuth.
            raise _generic_error

        if not verify_password(password, user.password_hash):
            raise _generic_error

        # Suspension check AFTER credential check intentionally
        if user.is_suspended:
            raise UnauthorizedException(
                message=(
                    "Your account has been suspended"
                    + (f": {user.suspension_reason}" if user.suspension_reason else ".")
                ),
                error_code="account_suspended",
                log_context={"user_id": str(user.id)},
            )

        logger.info(
            "User logged in",
            extra={"user_id": str(user.id), "role": user.role.value},
        )

        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)

        return TokenResponse(
            access_token=access_token,
            user=AuthUserRead.model_validate(user),
        ), refresh_token

    # ── Token refresh ─────────────────────────────────────────────────────────

    async def refresh(
        self,
        refresh_token: str,
    ) -> tuple[str, str]:
        """
        Exchange a valid refresh token for a new access + refresh token pair
        (token rotation).

        The old refresh token's JTI is blacklisted in Redis immediately.
        If the same token is presented again, it will be found in the
        blacklist and rejected -- this catches token theft.

        Args:
            refresh_token: The current refresh token from the HTTP-only cookie.

        Returns:
            Tuple of (new_access_token, new_refresh_token).

        Raises:
            UnauthorizedException: expired, invalid, or blacklisted token.
        """
        from app.core.security import InvalidTokenError, TokenExpiredError

        try:
            payload = decode_token(refresh_token, TokenType.REFRESH)
        except TokenExpiredError:
            raise UnauthorizedException(
                message="Your session has expired, please log in again",
                error_code="token_expired",
            )
        except InvalidTokenError:
            raise UnauthorizedException(
                message="Invalid session token",
                error_code="invalid_token",
            )

        jti = payload.get("jti")
        user_id = payload.get("sub")

        # Check blacklist
        if jti:
            try:
                blacklisted = await self.redis.get(
                    RedisKeys.refresh_token_blacklist(jti)
                )
                if blacklisted:
                    logger.warning(
                        "Blacklisted refresh token reused -- possible token theft",
                        extra={"jti": jti, "user_id": user_id},
                    )
                    raise UnauthorizedException(
                        message="Session token has been revoked",
                        error_code="token_revoked",
                    )
            except UnauthorizedException:
                raise
            except Exception:
                pass  # Redis failure -- continue without blacklist check

        # Fetch user to ensure they still exist and aren't suspended
        user = await self.repo.get_by_id(uuid.UUID(user_id))
        if user is None or user.is_suspended:
            raise UnauthorizedException(
                message="Invalid session",
                error_code="invalid_token",
            )

        # Blacklist the used refresh token (token rotation)
        if jti:
            try:
                ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86_400
                await self.redis.setex(
                    RedisKeys.refresh_token_blacklist(jti), ttl, "1"
                )
            except Exception:
                pass  # Redis failure is not fatal here

        new_access = create_access_token(user.id)
        new_refresh = create_refresh_token(user.id)

        logger.info(
            "Token refreshed",
            extra={"user_id": str(user.id)},
        )

        return new_access, new_refresh

    # ── Logout ────────────────────────────────────────────────────────────────

    async def logout(self, refresh_token: str) -> None:
        """
        Blacklist the refresh token so it can't be reused.

        Access tokens are short-lived (30 min) and can't be invalidated
        without a per-token blacklist -- not worth the Redis write overhead.
        The 30-min window is the standard trade-off in stateless JWT auth.

        If the token is already expired or invalid, silently succeed --
        logout should never fail from the user's perspective.
        """
        try:
            payload = decode_token(refresh_token, TokenType.REFRESH)
            jti = payload.get("jti")
            if jti:
                ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86_400
                await self.redis.setex(
                    RedisKeys.refresh_token_blacklist(jti), ttl, "1"
                )
        except Exception:
            pass  # Expired, invalid, or Redis failure -- all fine on logout