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
from app.domains.subscriptions.models import Subscription, SubscriptionStatus
from app.domains.users.models import KycStatus, User, UserRole
from app.permissions.roles import can_moderate, can_manage_platform


# ── require_role ──────────────────────────────────────────────────────────────

def require_role(*roles: UserRole) -> Callable:
    """
    Enforces one of the given roles. Multiple roles = OR semantics.

        require_role(UserRole.ADMIN)
        require_role(UserRole.ADMIN, UserRole.MODERATOR)

    Raises:
        ForbiddenException(error_code="insufficient_role")
    """
    async def guard(
        current_user: User = Depends(get_current_user),
    ) -> User:
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


# ── require_subscription ──────────────────────────────────────────────────────

def require_subscription() -> Callable:
    """
    Enforces an active subscription. Checks SUBSCRIPTIONS table with
    Redis cache (5 min TTL). On Redis failure, falls through to DB.

    is_active checks BOTH status=active AND expires_at > now() —
    guards against Celery lag where status may not yet be updated.

    Raises:
        ForbiddenException(error_code="subscription_required")
    """
    async def guard(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis: Redis = Depends(get_redis),
    ) -> User:
        # Admins bypass subscription requirement
        if current_user.role == UserRole.ADMIN:
            return current_user

        cache_key = RedisKeys.active_subscription(str(current_user.id))

        # Fast path: Redis cache
        try:
            if await redis.get(cache_key) == "1":
                return current_user
        except Exception:
            pass  # Redis failure → fall through to DB

        # Slow path: DB query
        # In require_subscription(), change the DB query to:
        result = await db.execute(
            select(Subscription)
            .where(
                Subscription.user_id == current_user.id,
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
                log_context={"user_id": str(current_user.id)},
            )

        # Warm cache on success
        try:
            await redis.setex(cache_key, RedisKeys.SUBSCRIPTION_TTL_SECONDS, "1")
        except Exception:
            pass

        return current_user

    return guard


# ── require_kyc ───────────────────────────────────────────────────────────────

def require_kyc() -> Callable:
    """
    Enforces KYC verification. Checks user.kyc_status (already loaded
    by get_current_user — no extra DB query needed).

    Returns distinct error_codes per KYC state so the frontend can
    route to the correct screen:
      kyc_required → upload screen (not submitted)
      kyc_pending  → status screen (waiting for provider)
      kyc_rejected → status screen with rejection reason

    Raises:
        KYCException (403, subclass of ForbiddenException)
    """
    async def guard(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if current_user.kyc_status == KycStatus.VERIFIED:
            return current_user

        if current_user.kyc_status == KycStatus.REJECTED:
            raise KYCException(
                message="Your identity verification was rejected. "
                        "Please re-submit your documents.",
                error_code="kyc_rejected",
                log_context={"user_id": str(current_user.id)},
            )

        if current_user.kyc_status == KycStatus.PENDING_REVIEW:
            raise KYCException(
                message="Your identity verification is still being processed.",
                error_code="kyc_pending",
                log_context={"user_id": str(current_user.id)},
            )

        # NOT_SUBMITTED
        raise KYCException(
            message="Identity verification is required. "
                    "Please complete KYC verification to continue.",
            error_code="kyc_required",
            log_context={"user_id": str(current_user.id)},
        )

    return guard


# ── require_verified_renter ───────────────────────────────────────────────────

def require_verified_renter() -> Callable:
    """
    Composite guard: active subscription + KYC verified, NO role check.

    Gate for renter-side gated actions that don't require a specific
    role — bookings, messaging an agent, saved search alerts (Rule 3 /
    the permission matrix's "SUB renter" column). Unlike require_agent(),
    this does not restrict by UserRole — an agent account could in
    principle also book a viewing on another agent's listing, and
    nothing in the business rules forbids that.

    Check order (cheapest first):
      1. Sub  — Redis cache, then DB
      2. KYC  — field on loaded User, no extra DB

    Raises (first failure wins):
        ForbiddenException(error_code="subscription_required")
        KYCException(error_code="kyc_required" | "kyc_rejected" | "kyc_pending")
    """
    sub_guard = require_subscription()
    kyc_guard = require_kyc()

    async def guard(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis: Redis = Depends(get_redis),
    ) -> User:
        await sub_guard(current_user=current_user, db=db, redis=redis)
        await kyc_guard(current_user=current_user)
        return current_user

    return guard


# ── require_agent ─────────────────────────────────────────────────────────────

def require_agent() -> Callable:
    """
    Composite guard: role=agent + active subscription + KYC verified.
    Gate for all listing-creation and booking-management endpoints.

    Check order (cheapest first):
      1. Role    — no DB, no Redis
      2. Sub     — Redis cache, then DB
      3. KYC     — field on loaded User, no extra DB

    Raises (first failure wins):
        ForbiddenException(error_code="insufficient_role")
        ForbiddenException(error_code="subscription_required")
        KYCException(error_code="kyc_required" | "kyc_rejected" | "kyc_pending")
    """
    role_guard = require_role(UserRole.AGENT)
    sub_guard = require_subscription()
    kyc_guard = require_kyc()

    async def guard(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        redis: Redis = Depends(get_redis),
    ) -> User:
        await role_guard(current_user=current_user)
        await sub_guard(current_user=current_user, db=db, redis=redis)
        await kyc_guard(current_user=current_user)
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
    async def guard(
        current_user: User = Depends(get_current_user),
    ) -> User:
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


# ── require_super_admin ───────────────────────────────────────────────────────

def require_super_admin() -> Callable:
    """
    Enforces super_admin level — platform config, managing admin roles.

    Raises:
        ForbiddenException(error_code="insufficient_role")
    """
    async def guard(
        current_user: User = Depends(get_current_user),
    ) -> User:
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