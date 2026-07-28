"""
core/dependencies.py

FastAPI dependency-injection surface for authentication. Every protected
router endpoint depends on get_current_user; public-but-personalizable
endpoints (e.g. a property listing page that shows a "saved" heart icon
only if logged in) depend on get_optional_user instead.

    from app.core.dependencies import get_db, get_current_user

    @router.get("/properties/{id}")
    async def get_property(
        id: UUID,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
    ):
        ...

Scope boundary (see 01_ARCHITECTURE.md — this exact split was decided
before any code was written):

    core/dependencies.py  ← infrastructure only: WHO is making this
                             request (identity), and a DB session to
                             look them up with.
    permissions/guards.py ← access control: what this identified user
                             is ALLOWED to do (require_role,
                             require_subscription, require_kyc).

This file never makes an authorization decision beyond "is this a
valid, non-suspended user." Role checks, subscription checks, and KYC
checks all belong in permissions/guards.py, which depends on
get_current_user from here and adds checks on top.

get_db is re-exported (not redefined) from app.db.session, so routers
have a single import surface for both pieces of the auth/DB story:

    from app.core.dependencies import get_db, get_current_user

rather than importing get_db from one module and get_current_user from
another. The actual session lifecycle (commit/rollback/close) is
implemented once, in db/session.py — see that module for details.
"""

from __future__ import annotations

import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import (
    InvalidTokenError,
    TokenExpiredError,
    TokenType,
    get_subject_from_token,
)
from app.db.session import get_db  # noqa: F401  ← re-exported, see module docstring
from app.domains.users.models import User

# auto_error=False: HTTPBearer's default behaviour raises a raw FastAPI
# HTTPException when the header is missing, which bypasses our
# BaseAppException hierarchy and the standard error response shape
# (03_API_AND_RESPONSES.md §17). With auto_error=False it instead
# returns None, and get_current_user raises UnauthorizedException
# itself — keeping every auth failure path going through the same
# global exception handler.
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Resolves the Authorization: Bearer <token> header into a User row.

    Raises:
        UnauthorizedException (401):
          - No Authorization header present (error_code="missing_token")
          - Token is expired (error_code="token_expired")
          - Token is malformed/tampered, or is a refresh token presented
            where an access token is required (error_code="invalid_token")
          - Token's subject is not a valid UUID (error_code="invalid_token")
          - Token's subject does not match any user (error_code="user_not_found") —
            this can legitimately happen if a user is deleted... but
            Standard 20 says users are never physically deleted, so in
            practice this indicates a forged or stale token from a
            different environment (e.g. a prod token replayed against
            staging).
        ForbiddenException (403):
          - The user account is suspended (error_code="account_suspended").
            Deliberately 403, not 401: the token IS valid and DOES
            identify a real user — they are authenticated, just not
            permitted to proceed. This distinction lets the frontend
            show "Your account has been suspended: {reason}" instead of
            silently bouncing to the login screen as if the token were
            garbage.

    This function does NOT check role, subscription status, or KYC
    status — see permissions/guards.py for those.
    """
    if credentials is None:
        raise UnauthorizedException(
            message="Authentication required",
            error_code="missing_token",
        )

    token = credentials.credentials

    try:
        subject = get_subject_from_token(token, TokenType.ACCESS)
    except TokenExpiredError as e:
        raise UnauthorizedException(
            message="Access token has expired",
            error_code="token_expired",
        ) from e
    except InvalidTokenError as e:
        raise UnauthorizedException(
            message="Invalid authentication token",
            error_code="invalid_token",
        ) from e

    try:
        user_id = uuid.UUID(subject)
    except ValueError as e:
        raise UnauthorizedException(
            message="Invalid authentication token",
            error_code="invalid_token",
        ) from e

    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedException(
            message="User account not found",
            error_code="user_not_found",
            log_context={"token_subject": str(user_id)},
        )

    if user.is_suspended:
        raise ForbiddenException(
            message=(
                "Your account has been suspended"
                + (f": {user.suspension_reason}" if user.suspension_reason else ".")
            ),
            error_code="account_suspended",
            log_context={"user_id": str(user.id)},
        )

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """
    Like get_current_user, but returns None instead of raising when no
    Authorization header is present at all — for endpoints that work
    for guests but personalize when logged in (Rule: guests can browse
    and view properties; only registered+subscribed users can search,
    save, message, or book — see permission matrix).

    IMPORTANT: a token that IS present but invalid/expired/malformed
    still raises UnauthorizedException here, exactly as in
    get_current_user. "No token" and "bad token" are deliberately NOT
    treated the same way — silently downgrading a bad token to "treat
    as guest" would hide real bugs (e.g. a frontend sending a stale
    token after logout should surface that loudly, not fail silently
    into degraded guest behaviour).
    """
    if credentials is None:
        return None
    return await get_current_user(credentials=credentials, db=db)
