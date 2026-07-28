"""
domains/auth/router.py

HTTP layer for authentication. No business logic -- only:
  - Parse request
  - Call service
  - Set/clear cookies
  - Return response

Refresh token cookie spec (Standard 18):
  - HTTP-only: JavaScript cannot read it (XSS mitigation)
  - Secure: HTTPS only in production (set conditionally on ENVIRONMENT)
  - SameSite=lax: sent on top-level navigations, blocked on cross-site
    AJAX -- the right balance for a web + mobile platform
  - Path=/auth: cookie only sent to auth endpoints, not every request
  - Max-Age = REFRESH_TOKEN_EXPIRE_DAYS * 86400

Rate limiting is handled by middleware, not here -- see Standard 21.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import UnauthorizedException
from app.core.redis_client import get_redis
from app.domains.auth.schemas import (
    LoginRequest,
    RefreshResponse,
    RegisterRequest,
    TokenResponse,
)
from app.domains.auth.service import AuthService
from app.domains.users.models import User
from app.shared.schemas import SuccessResponse

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "refresh_token"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _set_refresh_cookie(response: Response, token: str) -> None:
    """Set the HTTP-only refresh token cookie."""
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        path="/auth",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86_400,
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Delete the refresh token cookie."""
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        path="/auth",
    )


def _extract_ip(request: Request) -> str | None:
    """
    Extract client IP for consent logging (NDPR evidence).

    Checks X-Forwarded-For (set by NGINX/load balancer) first,
    falls back to direct connection IP.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _extract_user_agent(request: Request) -> str | None:
    """Extract user agent string for consent logging (NDPR evidence)."""
    return request.headers.get("User-Agent")


# ── POST /auth/register ───────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=SuccessResponse[TokenResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    request: Request,
    response: Response,
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> SuccessResponse[TokenResponse]:
    """
    Create a new renter or agent account.

    On success:
      - Returns access token + user snapshot in body
      - Sets refresh token as HTTP-only cookie
      - Creates notification settings with defaults
      - Records NDPR consent log entries

    Raises:
      409 ConflictException  -- email already registered
      422 ValidationError    -- invalid request data
    """
    svc = AuthService(db, redis)
    token_response, refresh_token = await svc.register(
        data=data,
        ip_address=_extract_ip(request),
        user_agent=_extract_user_agent(request),
    )
    _set_refresh_cookie(response, refresh_token)
    return SuccessResponse.ok(data=token_response, message="Registration successful")


# ── POST /auth/login ──────────────────────────────────────────────────────────


@router.post(
    "/login",
    response_model=SuccessResponse[TokenResponse],
    summary="Log in with email and password",
)
async def login(
    response: Response,
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> SuccessResponse[TokenResponse]:
    """
    Authenticate with email + password.

    On success:
      - Returns access token + user snapshot in body
      - Sets refresh token as HTTP-only cookie

    Same error message for wrong email and wrong password to prevent
    user enumeration.

    Raises:
      401 UnauthorizedException -- wrong credentials or suspended account
    """
    svc = AuthService(db, redis)
    token_response, refresh_token = await svc.login(
        email=data.email,
        password=data.password,
    )
    _set_refresh_cookie(response, refresh_token)
    return SuccessResponse.ok(data=token_response, message="Login successful")


# ── POST /auth/refresh ────────────────────────────────────────────────────────


@router.post(
    "/refresh",
    response_model=SuccessResponse[RefreshResponse],
    summary="Exchange refresh token for new token pair",
)
async def refresh(
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
) -> SuccessResponse[RefreshResponse]:
    """
    Exchange a valid refresh token cookie for a new access + refresh
    token pair (token rotation). The old refresh token is blacklisted.

    The refresh token is read from the HTTP-only cookie, not the body.
    Clients call this transparently when they receive a 401.

    Raises:
      401 UnauthorizedException -- missing, expired, or blacklisted token
    """
    if refresh_token is None:
        raise UnauthorizedException(
            message="No refresh token found",
            error_code="missing_token",
        )

    svc = AuthService(db, redis)
    new_access, new_refresh = await svc.refresh(refresh_token)
    _set_refresh_cookie(response, new_refresh)
    return SuccessResponse.ok(
        data=RefreshResponse(access_token=new_access),
        message="Token refreshed",
    )


# ── POST /auth/logout ─────────────────────────────────────────────────────────


@router.post(
    "/logout",
    response_model=SuccessResponse,
    summary="Log out -- revoke refresh token",
)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE),
    current_user: User = Depends(get_current_user),
) -> SuccessResponse:
    """
    Revoke the refresh token and clear the cookie.

    Access token cannot be invalidated (stateless JWT) -- it expires
    naturally within 30 minutes. Requires a valid access token to
    prevent unauthenticated requests hitting the blacklist.

    Raises:
      401 UnauthorizedException -- missing or invalid access token
    """
    if refresh_token:
        svc = AuthService(db, redis)
        await svc.logout(refresh_token)

    _clear_refresh_cookie(response)
    logger.info("User logged out", extra={"user_id": str(current_user.id)})
    return SuccessResponse.empty("Logged out successfully")
