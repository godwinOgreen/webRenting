"""
permissions/guards.py

FastAPI Depends()-callable guards that enforce access control on top
of the authenticated user from core/dependencies.get_current_user.

    from app.permissions.guards import require_subscription, require_kyc
    from app.permissions.guards import require_role
    from app.domains.users.models import UserRole

    @router.post("/bookings")
    async def create_booking(
        user: User = Depends(require_subscription()),
    ):
        ...

    @router.post("/admin/properties/{id}/approve")
    async def approve(
        user: User = Depends(require_role(UserRole.ADMIN)),
    ):
        ...

Layer boundary (01_ARCHITECTURE.md):
  core/dependencies.py  → WHO: resolves token → User
  permissions/guards.py → ALLOWED: role, subscription, KYC

Redis caching:
  require_subscription() caches result for SUBSCRIPTION_TTL_SECONDS (300s).
  On Redis failure: falls through to DB — outage degrades performance,
  never blocks legitimate users.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import ForbiddenException, KYCException
from app.core.redis_client import RedisKeys, get_redis
from app.domains.subscriptions.models import Subscription
from app.domains.users.models import KycStatus, User, UserRole
from app.permissions.roles import can_manage_platform, can_moderate


# ── Internal Helpers ──────────────────────────────────────────────────────────

async def _enforce_subscription(user: User, db: AsyncSession, redis: Redis) -> None:
    """
    Enforces active subscription check with safe Redis caching.
    Raises ForbiddenException if no active subscription found.
    """
    # Admins bypass subscription requirement
    if user.role == UserRole.ADMIN:
        return

    cache_key = RedisKeys.active_subscription(str(user.id))

    # Fast path: Redis cache
    try:
        if await redis.get(cache_key) == "1":
            return
    except Exception:
        pass  # Redis down → fall back to DB gracefully

    # Slow path: DB query using is_active (includes CANCELLED until expiry)
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.is_active,
        )
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    subscription: Optional[Subscription] = result.scalar_one_or_none()

    if subscription is None:
        raise ForbiddenException(
            message="An active subscription is required for this action",
            error_code="subscription_required",
            log_context={"user_id": str(user.id)},
        )

    # Cache warmup
    try:
        await redis.setex(cache_key, RedisKeys.SUBSCRIPTION_TTL_SECONDS, "1")
    except Exception:
        pass


def _enforce_kyc(user: User) -> None:
    """
    Enforces verified KYC status.
    Raises KYCException with distinct error_code per KYC state so
    the frontend can route to the correct screen.
    """
    if user.kyc_status == KycStatus.VERIFIED:
        return

    if user.kyc_status == KycStatus.REJECTED:
        raise KYCException(
            message="Your identity verification was rejected. "
                    "Please re-submit your documents.",
            error_code="kyc_rejected",
            log_context={"user_id": str(user.id)},
        )

    if user.kyc_status == KycStatus.PENDING_REVIEW:
        raise KYCException(
            message="Your identity verification is still being processed.",
            error_code="kyc_pending",
            log_context={"user_id": str(user.id)},
        )

    # NOT_SUBMITTED
    raise KYCException(
        message="Identity verification is required. "
                "Please complete KYC verification to continue.",
        error_code="kyc_required",
        log_context={"user_id": str(user.id)},
    )


# ── Guards ────────────────────────────────────────────────────────────────────

def require_role(*roles: UserRole) -> Callable:
    """
    Enforces one of the given roles. Multiple roles = OR semantics.

    Raises:
        ForbiddenException(error_code="insufficient_role")
    """
    async def guard(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            role_names = " or ".join(r.value for r in roles)
            raise ForbiddenException(
                message=f"This action requires the {role_names} role",
                error_code="insufficient_role",
                log_context={
                    "user_id": str(current_user.id),
                    "user_role": current_user.role.value,
                    "required_roles": [r.value for r in roles],
                },
            )
        return current_user

    return guard


def require_subscription() -> Callable:
    """
    Enforces an active subscription. Redis-cached (5 min TTL).

    Raises:
        ForbiddenException(error_code="subscription_required")
    """
    async def guard(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis: Redis = Depends(get_redis),
    ) -> User:
        await _enforce_subscription(current_user, db, redis)
        return current_user

    return guard

# ── require_kyc ───────────────────────────────────────────────────────────────

def require_kyc() -> Callable:
    """
    Enforces KYC verification. No extra DB query — kyc_status
    is already loaded on the User by get_current_user.

    Raises:
        KYCException(error_code="kyc_required" | "kyc_rejected" | "kyc_pending")
    """
    async def guard(current_user: User = Depends(get_current_user)) -> User:
        _enforce_kyc(current_user)
        return current_user

    return guard


def require_verified_renter() -> Callable:
    """
    Composite guard: active subscription + KYC verified. NO role check.
    Gate for renter-side gated actions (bookings, messaging, alerts).

    Check order (cheapest first):
      1. Sub  — Redis cache, then DB
      2. KYC  — field on loaded User, no extra DB

    Raises (first failure wins):
        ForbiddenException(error_code="subscription_required")
        KYCException(error_code="kyc_required" | "kyc_rejected" | "kyc_pending")
    """
    async def guard(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis: Redis = Depends(get_redis),
    ) -> User:
        await _enforce_subscription(current_user, db, redis)
        _enforce_kyc(current_user)
        return current_user

    return guard

# ── require_agent ─────────────────────────────────────────────────────────────

def require_agent() -> Callable:
    """
    Composite guard: role=agent + active subscription + KYC verified.
    Gate for listing-creation and booking-management endpoints.

    Check order (cheapest first):
      1. Role    — no DB, no Redis
      2. Sub     — Redis cache, then DB
      3. KYC     — field on loaded User, no extra DB

    Raises (first failure wins):
        ForbiddenException(error_code="insufficient_role")
        ForbiddenException(error_code="subscription_required")
        KYCException(error_code="kyc_required" | "kyc_rejected" | "kyc_pending")
    """
    async def guard(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis: Redis = Depends(get_redis),
    ) -> User:
        if current_user.role != UserRole.AGENT:
            raise ForbiddenException(
                message="This action requires the agent role",
                error_code="insufficient_role",
                log_context={
                    "user_id": str(current_user.id),
                    "user_role": current_user.role.value,
                    "required_roles": ["agent"],
                },
            )

        await _enforce_subscription(current_user, db, redis)
        _enforce_kyc(current_user)
        return current_user

    return guard

# ── require_moderator ─────────────────────────────────────────────────────────

def require_moderator() -> Callable:
    """
    Enforces moderation privileges: admin OR moderator role.
    Uses can_moderate() from roles.py which handles both paths
    (UserRole.MODERATOR and UserRole.ADMIN+AdminRole.MODERATOR).

    Raises:
        ForbiddenException(error_code="insufficient_role")
    """
    async def guard(current_user: User = Depends(get_current_user)) -> User:
        if not can_moderate(current_user):
            raise ForbiddenException(
                message="Moderation privileges are required for this action",
                error_code="insufficient_role",
                log_context={
                    "user_id": str(current_user.id),
                    "user_role": current_user.role.value,
                },
            )
        return current_user

    return guard


def require_super_admin() -> Callable:
    """
    Enforces super_admin level — platform config, managing admin roles.

    Raises:
        ForbiddenException(error_code="insufficient_role")
    """
    async def guard(current_user: User = Depends(get_current_user)) -> User:
        if not can_manage_platform(current_user):
            raise ForbiddenException(
                message="Super admin privileges are required for this action",
                error_code="insufficient_role",
                log_context={
                    "user_id": str(current_user.id),
                    "user_role": current_user.role.value,
                    "admin_role": current_user.admin_role.value
                    if current_user.admin_role else None,
                },
            )
        return current_user

    return guard